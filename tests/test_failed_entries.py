import json
import sqlite3
from types import SimpleNamespace

import pytest
from prism_core.failed_entries import compensate_agent_rejection, compensate_failed_live_entry


def fixture_db(market='KR'):
    c = sqlite3.connect(':memory:')
    table = 'stock_holdings' if market == 'KR' else 'us_stock_holdings'
    c.executescript(f'''
      CREATE TABLE {table} (id INTEGER PRIMARY KEY, account_key TEXT, ticker TEXT,
        entry_cost_status TEXT, actual_buy_price REAL, actual_buy_quantity REAL, actual_buy_amount REAL);
      CREATE TABLE positions (id TEXT, market TEXT, account_id TEXT, symbol TEXT,
        status TEXT, entry_intent_id TEXT, exit_intent_id TEXT, updated_at TEXT);
      CREATE TABLE order_intents (id TEXT, market TEXT, account_id TEXT, symbol TEXT,
        side TEXT,status TEXT,execution_mode TEXT,source_position_id TEXT);
      CREATE TABLE broker_orders (intent_id TEXT,accepted INTEGER,status TEXT,broker_order_id TEXT);
    ''')
    c.execute(f'INSERT INTO {table} VALUES (1,?,?,?,?,?,?)', ('prod:test:01','TEST','UNVERIFIED',None,None,None))
    c.execute('INSERT INTO positions VALUES (?,?,?,?,?,?,?,?)',
              (f'legacy:{market}:1',market,'prod:test:01','TEST','OPEN','intent',None,''))
    c.execute('INSERT INTO order_intents VALUES (?,?,?,?,?,?,?,?)',
              ('intent',market,'prod:test:01','TEST','BUY','FAILED','live',f'legacy:{market}:1'))
    c.execute("INSERT INTO broker_orders VALUES ('intent',0,'FAILED',NULL)")
    c.commit()
    return c, table


@pytest.mark.parametrize('market', ['KR','US'])
def test_rejected_live_entry_archived_not_realized_trade(market):
    c, table = fixture_db(market)
    assert compensate_failed_live_entry(c,market,1,'intent')
    assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    assert c.execute('SELECT status FROM positions').fetchone()[0] == 'ENTRY_FAILED'
    archived = c.execute('SELECT holding_snapshot_json FROM failed_entry_reconciliations').fetchone()[0]
    assert json.loads(archived)['ticker'] == 'TEST'
    assert not compensate_failed_live_entry(c,market,1,'intent')


@pytest.mark.parametrize('mutation', [
    "UPDATE order_intents SET status='UNKNOWN'",
    "UPDATE order_intents SET status='SUBMITTED'",
    "UPDATE order_intents SET status='QUEUED'",
    "UPDATE order_intents SET execution_mode='demo'",
    "UPDATE order_intents SET account_id='vps:test:01'",
    "UPDATE order_intents SET account_id='different-account'",
    "UPDATE order_intents SET symbol='OTHER'",
    "UPDATE order_intents SET source_position_id='legacy:KR:2'",
    "UPDATE broker_orders SET accepted=1",
    "UPDATE broker_orders SET broker_order_id='accepted-order'",
    "DELETE FROM broker_orders",
    "UPDATE stock_holdings SET entry_cost_status='CONFIRMED'",
    "UPDATE stock_holdings SET actual_buy_quantity=1",
    "UPDATE positions SET exit_intent_id='exit'",
    "UPDATE positions SET status='PENDING_EXIT'",
    "UPDATE positions SET entry_intent_id='other'",
    "INSERT INTO order_intents SELECT 'second',market,account_id,symbol,side,status,execution_mode,source_position_id FROM order_intents",
])
def test_ambiguous_filled_other_account_and_demo_positions_preserved(mutation):
    c,table=fixture_db()
    c.execute(mutation); c.commit()
    assert not compensate_failed_live_entry(c,'KR',1,'intent')
    assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 1


def test_only_this_attempt_messages_removed():
    c,_=fixture_db()
    agent=SimpleNamespace(conn=c,message_queue=['prior','premature buy'],_msg_types=['analysis','analysis'],_msg_effect_ids=[None,None])
    assert compensate_agent_rejection(agent,'KR',1,{'intent_status':'FAILED','intent_id':'intent'},1)
    assert agent.message_queue == ['prior'] and agent._msg_types == ['analysis']


def test_transaction_rolls_back_if_archive_write_fails():
    c,_=fixture_db()
    c.execute('CREATE TABLE failed_entry_reconciliations (wrong_column TEXT)'); c.commit()
    with pytest.raises(sqlite3.OperationalError):
        compensate_failed_live_entry(c,'KR',1,'intent')
    assert c.execute('SELECT count(*) FROM stock_holdings').fetchone()[0] == 1
    assert c.execute('SELECT status FROM positions').fetchone()[0] == 'OPEN'
