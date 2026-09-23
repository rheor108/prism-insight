# 2026-09-23 KR batch failure repairs

## Scope and evidence

- Claude subscription login was missing. After interactive login, a synthetic
  structured-response call succeeded. No credentials were changed by this patch.
- Concurrent KIS refreshes previously read an expired token before the write lock.
  Serialize read/issue/save by API identity, then reread inside the lock. Six
  concurrent simulated requests now issue one token. No order APIs used in tests.
- Screening emits `stock_name`; the orchestrator omitted that column. Preserve
  it and the trigger JSON name, use configured market data as fallback, and refuse
  to generate a report with an unresolved `Stock_<ticker>` identity.
- Filename parsing now stops the ticker at the first underscore, preserving
  underscores in company names and supporting US tickers containing a period.
- Failed sections, missing sections, empty content and exact placeholder text
  cannot enter strategy synthesis or the final report. Summary errors propagate.
  Valid reports with explicitly unavailable individual metrics remain accepted.
- A single empty inference answer receives one correction within the existing
  timeout/tool-round budget. A single JSON fence is accepted for structured
  answers; arbitrary text extraction is not allowed.
- Claude errors use allowlisted categories without storing raw provider output.
  Authentication/quota errors are not retried as transient failures.
- Public-source smoke confirmed Firecrawl authentication failure. Company agents
  can research issuer IR/DART when WiseReport is inaccessible. A public-only
  Claude -> Codex research smoke verified the company identity and a source URL.
  The Firecrawl credential itself still requires its owner to renew it if desired.
- ONEIL listing detection uses the authenticated KRX client instead of importing
  pykrx/pkg_resources. An empty listing is not cached as proof of KOSDAQ membership.
- Korean charts select the actual installed font family (including Noto CJK) and
  reject matplotlib's silent Latin-font fallback.
- All report stages receive a consistent identity and intraday/close comparison
  contract. These prompt changes reduce ambiguity; they do not prove numerical
  correctness or replace subsequent quality assessment.

## Validation and operation

99 targeted tests passed, covering negative artifacts and retained valid cases,
concurrent token refresh, structured answers, identity propagation and report
contracts. Existing dependency deprecation warnings remain.

Active KR cron resolves `/home/seungbum/prism-insight-upstream` to this checkout.
No cron changes, live batch reruns, orders or Telegram test messages were made.
No entry/exit thresholds, portfolio constraints or stop-loss rules were changed.
An all-report failure retains the existing orchestrator early-return behavior;
this patch does not introduce a separate holdings-management execution mode.

## Outstanding data and observation

- Holding 095610 has unverified entry cost. Read-only broker snapshot was
  authoritative but contained no matching current balance or buy fill in the
  queried period. No cost was invented and no holding was deleted. Owner
  confirmation and matching account/order evidence are needed to reconcile it.
- The next scheduled batch has not yet validated these fixes in production.
- Earlier quality-review evidence remains unevaluated; restoring login alone is
  not proof that a review ran, and this repair did not resubmit that evidence.

Rollback: revert the repair commit, preserving credentials, DBs, reports and cron.
