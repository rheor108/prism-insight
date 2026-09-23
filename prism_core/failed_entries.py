"""Compensate only provably rejected live entries; preserve ambiguous orders."""
import json
from datetime import datetime, timezone

from prism_core.entry_costs import rows, tables
from prism_core.positions import legacy_position_id


def compensate_failed_live_entry(conn, market, holding_id, intent_id):
    """Archive and remove one unfilled legacy row, atomically, with ledger proof.

    Missing/ambiguous evidence returns False. No broker request or trade history
    is generated. The audit snapshots preserve the original user data.
    """
    if conn.in_transaction:
        raise RuntimeError('Compensation requires a separate transaction')
    table, _ = tables(market)
    position_id = legacy_position_id(market, holding_id)
    conn.execute('BEGIN IMMEDIATE')
    try:
        holdings = rows(conn, f'SELECT * FROM {table} WHERE id=?', (holding_id,))
        intents = rows(conn, 'SELECT * FROM order_intents WHERE id=?', (intent_id,))
        positions = rows(conn, 'SELECT * FROM positions WHERE id=?', (position_id,))
        brokers = rows(conn, 'SELECT * FROM broker_orders WHERE intent_id=?', (intent_id,))
        if len(holdings) != 1 or len(intents) != 1 or len(positions) != 1 or not brokers:
            conn.rollback()
            return False
        h, i, p = holdings[0], intents[0], positions[0]
        proven = (
            i['market'] == market and i['side'] == 'BUY'
            and i['status'] == 'FAILED' and i['execution_mode'] == 'live'
            and str(i['account_id']).startswith('prod:')
            and i['source_position_id'] == position_id
            and i['account_id'] == h['account_key'] == p['account_id']
            and i['symbol'] == h['ticker'] == p['symbol']
            and p['market'] == market and p['status'] == 'OPEN'
            and p['entry_intent_id'] == intent_id and not p['exit_intent_id']
            and h.get('entry_cost_status') != 'CONFIRMED'
            and not any(h.get(k) for k in ('actual_buy_price', 'actual_buy_quantity', 'actual_buy_amount'))
            and all(b['accepted'] == 0 and b['status'] == 'FAILED'
                    and not b['broker_order_id'] for b in brokers)
        )
        # Any additional attempt associated with this lot requires manual review.
        related = conn.execute('SELECT COUNT(*) FROM order_intents WHERE source_position_id=?', (position_id,)).fetchone()[0]
        if not proven or related != 1:
            conn.rollback()
            return False
        conn.execute('''CREATE TABLE IF NOT EXISTS failed_entry_reconciliations (
            intent_id TEXT PRIMARY KEY, market TEXT NOT NULL, holding_id INTEGER NOT NULL,
            holding_snapshot_json TEXT NOT NULL, position_snapshot_json TEXT NOT NULL,
            reason TEXT NOT NULL, reconciled_at TEXT NOT NULL)''')
        now = datetime.now(timezone.utc).isoformat()
        conn.execute('INSERT INTO failed_entry_reconciliations VALUES (?,?,?,?,?,?,?)',
                     (intent_id, market, holding_id, json.dumps(h, ensure_ascii=False),
                      json.dumps(p, ensure_ascii=False), 'REJECTED_LIVE_ENTRY_NO_ORDER', now))
        conn.execute("UPDATE positions SET status='ENTRY_FAILED',updated_at=? WHERE id=?", (now, position_id))
        conn.execute(f'DELETE FROM {table} WHERE id=?', (holding_id,))
        conn.commit()
        return True
    except BaseException:
        conn.rollback()
        raise


def compensate_agent_rejection(agent, market, holding_id, result, queue_start):
    """Drop only this attempt's premature success messages after compensation."""
    if result.get('intent_status') != 'FAILED' or not result.get('intent_id'):
        return False
    if not compensate_failed_live_entry(agent.conn, market, holding_id, result['intent_id']):
        return False
    for name in ('message_queue', '_msg_types', '_msg_effect_ids'):
        queue = getattr(agent, name, None)
        if isinstance(queue, list):
            del queue[queue_start:]
    return True
