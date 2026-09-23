# Rejected live buy retained as a simulated holding

## Evidence and scope

The 2026-09-10 KR afternoon batch inserted holding 095610 at 17:15:02,
then attempted a KIS buy with a KRW 10,000 budget and KRW 154,900 quote.
The broker adapter returned zero buyable quantity. The persisted BUY intent is
FAILED, with a rejected broker record and no broker order number. The legacy
code nevertheless retained the simulator holding and counted/published a buy.
This explains the discrepancy without assuming missing broker fills.

On September 23 the owner confirmed no balance, filled order or unfilled order.
The read-only broker snapshot also contained no matching balance or buy fill in
the September 9–23 period. The DB lot was unverified with no actual cost fields.

## Repair contract (KR and US)

This changes execution bookkeeping, not stock selection, sizing or exit rules.
Only a production-account (`prod:`) live BUY with a persisted FAILED intent,
exact account/symbol/lot linkage, OPEN position without an exit intent, no
confirmed cost, and exclusively rejected broker rows without order IDs may be
compensated. Additional attempts on the same lot require manual review.

The original holding and position are archived transactionally in
`failed_entry_reconciliations`; the active holding is removed and its position
becomes ENTRY_FAILED. Order evidence remains. No realized trade or P&L is invented.
The caller drops only this attempt's premature messages and skips success counts
and signals. Accepted, queued, unknown, confirmed, demo and mismatched-account
cases remain unchanged. Legacy simulators remain independent for demo accounts.

## Validation

50 targeted tests passed: archived rejection and transaction rollback, KR/US
shared compensation, preservation counterexamples, real broker-adapter boundary
with a fake broker, pending-entry lifecycle, pyramiding and analysis-failure alerts.
The lifecycle fixture now supplies an empty trade-history table and stubs unrelated
market gates/pulse fetching. Its local SQLite offloads run with cooperative yields
in the test, avoiding nest_asyncio/executor interference; production offloading is
unchanged. KR/US tracking modules also passed compilation.

## Data repair and operations

Only KR holding id 2 was compensated. Other holdings and realized trade-history
counts were unchanged. Its original row is preserved in the audit table and the
entire DB was backed up beforehand to the ignored local path
`runtime/repairs/stock_tracking_before_failed_entry_20260923.sqlite` (owner-only).
No broker order, Telegram message or cron change was performed.

The first repair PR's pending 095610 item is resolved by this evidence and repair.
The next scheduled production batch remains the forward-validation step.

Rollback code by reverting this repair commit. Restore a specific archived lot
only after rechecking current broker/order evidence; do not overwrite the live
DB wholesale with its older backup.
