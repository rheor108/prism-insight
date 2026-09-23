"""Batch reconciliation of analysis prices and broker-confirmed entry costs.

No order calls. A broker balance may identify a single legacy position, but
independent pyramiding rows require exact order links and a quantity/cost match.
Unknown evidence is never promoted to an actual fill. Historical rows are not
retroactively relabelled as confirmed trades.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import sqlite3
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
FIELDS = {
    'analysis_price': 'REAL',
    'actual_buy_price': 'REAL',
    'actual_buy_quantity': 'REAL',
    'actual_buy_amount': 'REAL',
    'entry_cost_status': "TEXT NOT NULL DEFAULT 'UNVERIFIED'",
    'entry_cost_source': 'TEXT',
    'entry_cost_checked_at': 'TEXT',
}


def tables(market):
    if market not in ('KR', 'US'):
        raise ValueError('Unsupported entry-cost market')
    prefix = 'us_' if market == 'US' else ''
    return prefix + 'stock_holdings', prefix + 'trading_history'


def columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}


def ensure_entry_cost_schema(conn, market):
    """Add fields without rewriting existing buy_price/profit_rate or committing."""
    holdings, history = tables(market)
    for table in (holdings, history):
        existing = columns(conn, table)
        if not existing:
            continue
        for name, kind in FIELDS.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
        # Legacy buy_price is the only available analysis-price provenance. The
        # actual fields intentionally remain NULL until external confirmation.
        conn.execute(f'UPDATE {table} SET analysis_price=buy_price WHERE analysis_price IS NULL')
        conn.execute(f"UPDATE {table} SET entry_cost_source='legacy_unverified' WHERE entry_cost_source IS NULL")
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_analysis_price_insert
            AFTER INSERT ON {table} WHEN NEW.analysis_price IS NULL BEGIN
            UPDATE {table} SET analysis_price=COALESCE(analysis_price,NEW.buy_price),
                entry_cost_source=COALESCE(entry_cost_source,'analysis_pending')
            WHERE id=NEW.id; END''')
    if columns(conn, history) and columns(conn, holdings):
        # Sell writers insert history before deleting the exact legacy lot.
        # Refuse ambiguous attribution; original history columns remain intact.
        match = ('h.account_key=NEW.account_key AND h.ticker=NEW.ticker '
                 'AND h.buy_date=NEW.buy_date AND h.buy_price=NEW.buy_price')
        assigns = ', '.join(f'{field}=(SELECT h.{field} FROM {holdings} h WHERE {match})'
                            for field in FIELDS)
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS {history}_entry_cost_insert
            AFTER INSERT ON {history}
            WHEN (SELECT COUNT(*) FROM {holdings} h WHERE {match})=1 BEGIN
            UPDATE {history} SET {assigns} WHERE id=NEW.id; END''')
    conn.execute('''CREATE TABLE IF NOT EXISTS entry_cost_reconciliations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT NOT NULL,
        account_key TEXT NOT NULL, holding_id INTEGER NOT NULL,
        checked_at TEXT NOT NULL, source TEXT NOT NULL,
        previous_buy_price REAL, actual_buy_price REAL NOT NULL,
        actual_buy_quantity REAL NOT NULL, actual_buy_amount REAL NOT NULL,
        order_refs_json TEXT NOT NULL)''')


def number(value, *, positive=False):
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation):
        raise ValueError('Missing or invalid broker number') from None
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise ValueError('Invalid broker number')
    return result


def rows(conn, sql, params=()):
    cur = conn.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def order_number(value):
    return str(value or '').strip().lstrip('0') or ''


def _order_links(conn, market, account_key):
    if not columns(conn, 'order_intents') or not columns(conn, 'broker_orders'):
        return {}
    links = defaultdict(list)
    for row in rows(conn, '''SELECT i.source_position_id, i.created_at, i.symbol, b.broker_order_id,
            b.raw_response_json FROM order_intents i LEFT JOIN broker_orders b ON b.intent_id=i.id
            WHERE i.market=? AND i.account_id=? AND i.side='BUY' ''',
            (market, account_key)):
        pos = row['source_position_id'] or ''
        prefix = f'legacy:{market}:'
        if not pos.startswith(prefix):
            continue
        try:
            holding_id = int(pos[len(prefix):])
            response = json.loads(row['raw_response_json'] or '{}')
        except (ValueError, TypeError):
            continue
        # Reservations and ordinary orders have distinct identifier namespaces.
        broker_id = str(row['broker_order_id'] or '')
        created = str(row['created_at'])[:10]
        # Follow the explicit local queue id; never pair by symbol/price alone.
        if market == 'US' and broker_id.startswith('PENDING-') and columns(conn, 'us_pending_orders'):
            for _ in range(5):
                pending = rows(conn, '''SELECT * FROM us_pending_orders
                    WHERE id=? AND account_key=? AND ticker=? AND order_type='buy' ''',
                    (broker_id.removeprefix('PENDING-'), account_key, row['symbol']))
                if len(pending) != 1 or pending[0]['status'] not in ('executed', 'requeued'):
                    break
                item = pending[0]
                try:
                    response = json.loads(item['order_result'])
                except (TypeError, ValueError):
                    break
                broker_id = str(response.get('order_no') or '')
                created = str(item.get('executed_at') or item['created_at'])[:10]
                if not broker_id.startswith('PENDING-'):
                    break
        reserved = bool(response.get('is_reserved_order') or response.get('period_type')
                        or str(response.get('order_type', '')).startswith('reserved_'))
        links[holding_id].append((order_number(broker_id), created, reserved))
    return links


def _matched_orders(holding, links, orders):
    selected = {}
    for order_no, created, reserved in links:
        if not order_no:
            return []
        try:
            start = datetime.fromisoformat(created).date() - timedelta(days=1)
        except ValueError:
            return []
        matches = []
        for order in orders:
            if order['ticker'] != holding['ticker']:
                continue
            if reserved != bool(order.get('reservation_no')):
                continue
            candidate_id = order.get('reservation_no') if reserved else order.get('order_no')
            if order_number(candidate_id) != order_no:
                continue
            day = datetime.strptime(order.get('reservation_date', order['order_date'])
                                    if reserved else order['order_date'], '%Y%m%d').date()
            # Prevent accidentally matching a reused order number on another day.
            if start <= day <= start + timedelta(days=2):
                matches.append(order)
        if len(matches) != 1:
            return []
        match = matches[0]
        selected[(match['order_date'], match['order_no'])] = match
    return list(selected.values())


def reconcile_entry_costs(conn, market, account_key, snapshot):
    """Reconcile one account atomically. Caller supplies authoritative read-only data."""
    holdings_table, _ = tables(market)
    if conn.in_transaction:
        raise RuntimeError('Entry-cost reconciliation requires an idle connection')
    if (snapshot.get('account_key') != account_key or snapshot.get('market') != market
            or snapshot.get('balance_authoritative') is not True):
        raise ValueError('Non-authoritative or wrong-account broker snapshot')
    holdings = rows(conn, f'SELECT * FROM {holdings_table} WHERE account_key=?', (account_key,))
    grouped = defaultdict(list)
    for row in holdings:
        grouped[row['ticker']].append(row)
    balances = {}
    for row in snapshot['holdings']:
        ticker = row['ticker']
        if not ticker or ticker in balances:
            raise ValueError('Duplicate or missing broker symbol')
        quantity = number(row['quantity'])
        if quantity:
            balances[ticker] = (quantity, number(row['avg_price'], positive=True))
    links = _order_links(conn, market, account_key)
    orders = snapshot.get('orders', []) if snapshot.get('orders_authoritative') is True else []
    checked_at = snapshot['checked_at']
    changes, unresolved = [], []
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        current = rows(conn, f'SELECT * FROM {holdings_table} WHERE account_key=?', (account_key,))
        if current != holdings:
            raise RuntimeError('Holdings changed during reconciliation; retry next batch')
        for ticker, lots in grouped.items():
            balance = balances.get(ticker)
            if not balance:
                unresolved.append(ticker)
                continue  # Never infer cancellation/sale from a missing holding.
            quantity, average = balance
            costs = []
            used_order_refs = set()
            for lot in lots:
                matched = _matched_orders(lot, links.get(lot['id'], []), orders)
                if matched:
                    identities = {(o['order_date'], o['order_no']) for o in matched}
                    if used_order_refs.intersection(identities):
                        costs = []
                        break  # One order cannot fund two independent entry rows.
                    used_order_refs.update(identities)
                    qty = sum(number(o['filled_quantity']) for o in matched)
                    amount = sum(number(o['filled_amount']) for o in matched)
                    remaining = sum(number(o['remaining_quantity']) for o in matched)
                    if qty > 0 and amount > 0:
                        costs.append((lot, qty, amount, 'PARTIAL' if remaining else 'CONFIRMED',
                                      'broker_order_fills', matched))
                elif (len(lots) == 1 and not links.get(lot['id'])
                      and lot.get('entry_cost_source') in ('legacy_unverified', 'broker_balance')):
                    # One legacy account-level position can use the broker's
                    # current average cost. Never distribute it among add lots.
                    costs.append((lot, quantity, quantity * average, 'CONFIRMED',
                                  'broker_balance', []))
            # Cost/quantity agreement catches manual trades, already-sold lots,
            # ambiguous reservation matches and duplicated fill rows.
            total_qty = sum(c[1] for c in costs)
            total_amount = sum(c[2] for c in costs)
            tolerance = Decimal('1') if market == 'KR' else Decimal('0.01')
            if (len(costs) != len(lots) or total_qty != quantity
                    or abs(total_amount / quantity - average) > tolerance):
                unresolved.append(ticker)
                continue
            for lot, qty, amount, status, source, refs in costs:
                price = amount / qty
                values = (float(price), float(qty), float(amount), status, source)
                before = tuple(lot.get(key) for key in ('actual_buy_price', 'actual_buy_quantity',
                                                       'actual_buy_amount', 'entry_cost_status', 'entry_cost_source'))
                conn.execute(f'''UPDATE {holdings_table} SET actual_buy_price=?,
                    actual_buy_quantity=?, actual_buy_amount=?, entry_cost_status=?,
                    entry_cost_source=?, entry_cost_checked_at=?, buy_price=?
                    WHERE id=? AND account_key=?''',
                    (*values, checked_at, float(price), lot['id'], account_key))
                if columns(conn, 'positions'):
                    conn.execute('''UPDATE positions SET entry_price=?
                        WHERE id=? AND market=? AND account_id=?''',
                        (float(price), f"legacy:{market}:{lot['id']}", market, account_key))
                if before != values:
                    safe_refs = [{k: ref[k] for k in ('order_no', 'order_date', 'filled_quantity', 'filled_amount')}
                                 for ref in refs]
                    conn.execute('''INSERT INTO entry_cost_reconciliations
                        (market,account_key,holding_id,checked_at,source,previous_buy_price,
                         actual_buy_price,actual_buy_quantity,actual_buy_amount,order_refs_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?)''',
                        (market, account_key, lot['id'], checked_at, source, lot['buy_price'],
                         float(price), float(qty), float(amount), json.dumps(safe_refs)))
                    changes.append(lot['id'])
                if status != 'CONFIRMED':
                    unresolved.append(ticker)
    return {'updated_ids': changes, 'unresolved_tickers': sorted(set(unresolved))}


async def reconcile_agent_entry_costs(agent, market):
    """Run once before the account's batch decisions, without blocking asyncio."""
    table, _ = tables(market)
    # Compatibility for integrations providing a legacy schema; normal schema
    # initialization always installs the fields before an agent can trade.
    if 'actual_buy_price' not in columns(agent.conn, table):
        return
    account_key, account_name = agent._account_scope()
    holdings = rows(agent.conn, f'SELECT * FROM {table} WHERE account_key=?', (account_key,))
    if not holdings:
        return
    from prism_core.execution_service import ExecutionService
    factory = ExecutionService.domestic if market == 'KR' else ExecutionService.us
    now = datetime.now(ZoneInfo('Asia/Seoul' if market == 'KR' else 'America/New_York'))
    # Both APIs' recent-order endpoints: query at most 90 days. Older single
    # legacy positions can still use an authoritative current balance.
    days = [str(h['buy_date'])[:10] for h in holdings]
    earliest = datetime.fromisoformat(min(days)) - timedelta(days=1)
    start = max(earliest.strftime('%Y-%m-%d'), (now - timedelta(days=89)).strftime('%Y-%m-%d')).replace('-', '')
    end = now.strftime('%Y%m%d')
    try:
        async with factory(account_name=account_name) as trading:
            snapshot = await asyncio.to_thread(trading.get_entry_cost_snapshot, start, end)
        result = reconcile_entry_costs(agent.conn, market, account_key, snapshot)
        logger.info('[ENTRY_COST][%s] updated=%s unresolved=%s', market,
                    len(result['updated_ids']), result['unresolved_tickers'])
        agent._entry_cost_unresolved = set(result['unresolved_tickers'])
    except Exception as exc:
        # Last confirmed costs remain usable; never overwrite them with quotes.
        logger.warning('[ENTRY_COST][%s] inquiry unavailable (%s); costs retained',
                       market, type(exc).__name__)
        agent._entry_cost_unresolved = {h['ticker'] for h in holdings
                                        if h.get('entry_cost_status') != 'CONFIRMED'}


def confirmed_cost(row):
    """Legacy callers without new fields remain compatible; migrated rows do not."""
    return ('entry_cost_status' not in row or
            (row.get('entry_cost_status') == 'CONFIRMED' and bool(row.get('actual_buy_price'))))


def refresh_sale_cost(conn, market, account_key, holding_ids, stock_data):
    """Refresh a seller's potentially stale entry cost under its existing DB lock."""
    table, _ = tables(market)
    if 'actual_buy_price' not in columns(conn, table):
        return stock_data['buy_price']
    if len(holding_ids) != 1:
        raise ValueError('A confirmed cost must identify one independent entry row')
    matches = rows(conn, f'SELECT * FROM {table} WHERE id=? AND account_key=?',
                   (holding_ids[0], account_key))
    if len(matches) != 1 or not confirmed_cost(matches[0]):
        raise ValueError('Cannot record a sale using an unconfirmed entry cost')
    live = matches[0]
    for field in FIELDS:
        stock_data[field] = live[field]
    stock_data['buy_price'] = live['actual_buy_price']
    return live['actual_buy_price']


def weighted_position(cursor, table, ticker, account_key=None):
    if table not in ('stock_holdings', 'us_stock_holdings'):
        raise ValueError('Invalid holdings table')
    sql = f'SELECT * FROM {table} WHERE ticker=?'
    params = [ticker]
    if account_key:
        sql += ' AND account_key=?'
        params.append(account_key)
    lots = rows(cursor.connection, sql, params)
    if not lots:
        return {'row_count': 0, 'avg_buy_price': 0.0}
    if 'actual_buy_quantity' not in lots[0]:
        average = sum(float(r['buy_price']) for r in lots) / len(lots)
    elif not all(confirmed_cost(r) and r.get('actual_buy_quantity', 0) > 0 for r in lots):
        average = 0.0  # No profit-based additional entry on unverified costs.
    else:
        average = sum(r['actual_buy_amount'] for r in lots) / sum(r['actual_buy_quantity'] for r in lots)
    return {'row_count': len(lots), 'avg_buy_price': average}
