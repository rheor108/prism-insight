"""Broker-cost accounting tests. No network, credentials, LLMs or orders."""
import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import asyncio
import threading

import pytest

from prism_core.entry_costs import (ensure_entry_cost_schema, reconcile_entry_costs,
                                    rows, weighted_position, confirmed_cost)
from prism_core.entry_cost_inquiry import read_entry_cost_snapshot, _pages, _fills
from prism_core.order_intents import IntentStore, OrderIntent
from tracking.db_schema import TABLE_STOCK_HOLDINGS, TABLE_TRADING_HISTORY


@pytest.fixture(params=['KR', 'US'])
def case(request, tmp_path):
    market = request.param
    db = tmp_path / 'cost.sqlite'
    conn = sqlite3.connect(db)
    prefix = 'us_' if market == 'US' else ''
    h, t = prefix+'stock_holdings', prefix+'trading_history'
    conn.execute(TABLE_STOCK_HOLDINGS.replace('stock_holdings', h))
    conn.execute(TABLE_TRADING_HISTORY.replace('trading_history', t))
    ensure_entry_cost_schema(conn, market)
    conn.commit()
    store = IntentStore(db)
    account = 'prod:12345678:01'
    snapshot = {'market':market, 'account_key':account,
                'checked_at':'2026-09-08T12:00:00+00:00', 'balance_authoritative':True,
                'holdings':[{'ticker':'TEST','quantity':2,'avg_price':110}],
                'orders_authoritative':True,'orders':[]}
    yield SimpleNamespace(conn=conn, market=market, h=h, t=t, account=account,
                          snapshot=snapshot, store=store)
    conn.close()


def holding(c, price=100, day='2026-09-08 09:30:00', account=None, legacy=True):
    cur = c.conn.execute(f'''INSERT INTO {c.h}
        (account_key,account_name,ticker,company_name,buy_price,buy_date)
        VALUES (?,?,?,?,?,?)''', (account or c.account,'test','TEST','Synthetic',price,day))
    if legacy:
        c.conn.execute(f"UPDATE {c.h} SET entry_cost_source='legacy_unverified' WHERE id=?", (cur.lastrowid,))
    c.conn.commit()
    return cur.lastrowid


def link(c, hid, order='001', reserved=False):
    intent = OrderIntent.create(market=c.market,account_id=c.account,symbol='TEST',side='buy',
        order_style='smart',source='test',source_position_id=f'legacy:{c.market}:{hid}',
        source_decision_id=f'test:{hid}',limit_price=100)
    c.store.reserve(intent)
    c.store.mark_submitting(intent.id)
    c.conn.execute("UPDATE order_intents SET created_at='2026-09-08T00:00:00+00:00' WHERE id=?",(intent.id,))
    c.conn.commit()
    response = {'success':True, 'order_no':order}
    if reserved:response['is_reserved_order']=True
    c.store.record_result(intent,status='SUBMITTED',accepted=True,response=response)


def fill(order='1', qty=2, amount=220, remaining=0, **extra):
    return dict(ticker='TEST',order_no=order,order_date='20260908',filled_quantity=qty,
                filled_amount=amount,remaining_quantity=remaining,**extra)


def test_single_legacy_balance_corrects_cost_preserves_analysis_and_is_idempotent(case):
    c=case;hid=holding(c)
    first=reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)
    row=rows(c.conn,f'SELECT * FROM {c.h}')[0]
    assert first['updated_ids']==[hid]
    assert (row['analysis_price'],row['buy_price'],row['actual_buy_quantity'],row['actual_buy_amount'])==(100,110,2,220)
    assert confirmed_cost(row)
    assert (121-row['buy_price'])/row['buy_price']*100==pytest.approx(10)
    reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)
    assert c.conn.execute('SELECT COUNT(*) FROM entry_cost_reconciliations').fetchone()[0]==1


def test_acknowledgement_and_quote_do_not_prove_fill(case):
    c=case;hid=holding(c);link(c,hid)
    result=reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)
    row=rows(c.conn,f'SELECT * FROM {c.h}')[0]
    assert result['unresolved_tickers']==['TEST']
    assert row['actual_buy_price'] is None and row['analysis_price']==100
    assert not confirmed_cost(row)
    assert weighted_position(c.conn.cursor(),c.h,'TEST',c.account)['avg_buy_price']==0


def test_exact_order_fill_and_partial_fill_progression(case):
    c=case;hid=holding(c);link(c,hid)
    c.snapshot['holdings'][0].update(quantity=1,avg_price=105)
    c.snapshot['orders']=[fill(qty=1,amount=105,remaining=1)]
    reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)
    row=rows(c.conn,f'SELECT * FROM {c.h}')[0]
    assert row['entry_cost_status']=='PARTIAL' and not confirmed_cost(row)
    c.snapshot['holdings'][0].update(quantity=2,avg_price=110)
    c.snapshot['orders']=[fill()]
    reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)
    row=rows(c.conn,f'SELECT * FROM {c.h}')[0]
    assert row['actual_buy_quantity']==2 and row['actual_buy_amount']==220
    assert row['analysis_price']==100 and confirmed_cost(row)


def test_pyramiding_uses_quantities_and_never_distributes_account_average(case):
    c=case;one=holding(c);two=holding(c,price=190,day='2026-09-08 10:30:00')
    c.snapshot['holdings'][0].update(quantity=4,avg_price=175)
    assert reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)['updated_ids']==[]
    link(c,one,'1');link(c,two,'2')
    c.snapshot['orders']=[fill('1',1,100),fill('2',3,600)]
    reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)
    assert weighted_position(c.conn.cursor(),c.h,'TEST',c.account)=={'row_count':2,'avg_buy_price':175}
    assert c.conn.execute(f'SELECT buy_price FROM {c.h} ORDER BY id').fetchall()==[(100.0,),(200.0,)]


def test_same_broker_order_cannot_be_attributed_to_two_lots(case):
    c=case;one=holding(c);two=holding(c,day='2026-09-08 10:30:00')
    link(c,one,'1');link(c,two,'1')
    c.snapshot['orders']=[fill('1',1,110)]
    assert reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)['updated_ids']==[]


@pytest.mark.parametrize('change',['account','incomplete','quantity_mismatch','date','symbol','duplicate'])
def test_unreliable_evidence_never_overwrites_cost(case,change):
    c=case;hid=holding(c);link(c,hid)
    c.snapshot['orders']=[fill()]
    if change=='account':c.snapshot['account_key']='prod:99999999:01'
    if change=='incomplete':c.snapshot['balance_authoritative']=False
    if change=='quantity_mismatch':c.snapshot['orders'][0]['filled_quantity']=3
    if change=='date':c.snapshot['orders'][0]['order_date']='20260808'
    if change=='symbol':c.snapshot['orders'][0]['ticker']='OTHER'
    if change=='duplicate':c.snapshot['orders'].append(fill())
    if change in ('account','incomplete'):
        with pytest.raises(ValueError):reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)
    else:assert reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)['updated_ids']==[]
    assert c.conn.execute(f'SELECT actual_buy_price FROM {c.h}').fetchone()[0] is None


def test_reservation_namespace_maps_only_explicit_broker_link(case):
    c=case;hid=holding(c);link(c,hid,'77',reserved=True)
    c.snapshot['orders']=[fill('88'),fill('88',reservation_no='77')]
    assert reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)['updated_ids']==[hid]


def test_historical_provenance_survives_sell_and_schema_reinitialization(case):
    c=case;holding(c)
    reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)
    c.conn.execute(f'''INSERT INTO {c.t}
        (account_key,ticker,company_name,buy_price,buy_date,sell_price,sell_date,profit_rate,holding_days)
        VALUES (?,'TEST','Synthetic',110,'2026-09-08 09:30:00',121,'2026-09-09 10:00:00',10,1)''',(c.account,))
    c.conn.execute(f'DELETE FROM {c.h}')
    c.conn.commit()
    for _ in range(2):ensure_entry_cost_schema(c.conn,c.market)
    row=rows(c.conn,f'SELECT * FROM {c.t}')[0]
    assert row['analysis_price']==100 and row['actual_buy_price']==110
    assert row['actual_buy_amount']==220 and row['profit_rate']==10


def test_other_account_is_not_modified(case):
    c=case;holding(c);holding(c,price=999,account='prod:99999999:01')
    reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)
    row=rows(c.conn,f'SELECT * FROM {c.h} WHERE account_key<>?',(c.account,))[0]
    assert row['buy_price']==999 and row['actual_buy_price'] is None


def test_sell_refreshes_stale_analysis_price_from_locked_confirmed_row(case):
    from prism_core.entry_costs import refresh_sale_cost
    c=case;hid=holding(c)
    stale={'buy_price':100}
    with pytest.raises(ValueError):refresh_sale_cost(c.conn,c.market,c.account,[hid],stale)
    reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)
    c.conn.execute('BEGIN IMMEDIATE')
    assert refresh_sale_cost(c.conn,c.market,c.account,[hid],stale)==110
    assert stale['buy_price']==110 and stale['analysis_price']==100
    c.conn.rollback()


class Response:
    def __init__(self, body, continuation='', ok=True):
        self.body=SimpleNamespace(**body);self.continuation=continuation;self.ok=ok
    def isOK(self):return self.ok
    def getBody(self):return self.body
    def getHeader(self):return SimpleNamespace(tr_cont=self.continuation)


def test_pagination_reads_all_pages_and_rejects_repeated_cursor():
    calls=[]
    def fetch(path,tr,cont,params):
        calls.append((cont,dict(params)))
        if len(calls)==1:return Response({'output':[{'id':1}],'ctx_area_fk100':'f','ctx_area_nk100':'n'},'M')
        return Response({'output':[{'id':2}]})
    assert _pages(fetch,'read','READ',{},'output',100)==[{'id':1},{'id':2}]
    assert calls[1]==('N',{'CTX_AREA_FK100':'f','CTX_AREA_NK100':'n'})
    with pytest.raises(ValueError):
        _pages(lambda *a:Response({'output':[],'ctx_area_fk100':'f','ctx_area_nk100':'n'},'M'),
               'read','READ',{},'output',100)


@pytest.mark.parametrize('market',['KR','US'])
def test_inquiry_only_uses_read_endpoints_and_cumulative_fill_amount(market):
    calls=[]
    def fetch(path,tr,cont,params):
        calls.append((path,tr,params))
        if path.endswith('inquire-balance'):
            row={'pdno':'TEST','hldg_qty':'2','pchs_avg_pric':'110'} if market=='KR' else {
                'ovrs_pdno':'TEST','ovrs_cblc_qty':'2','pchs_avg_pric':'110'}
            return Response({'output1':[row]})
        if path.endswith(('order-resv-ccnl','order-resv-list')):return Response({'output':[]})
        row={'sll_buy_dvsn_cd':'02','pdno':'TEST','odno':'0001','ord_dt':'20260908'}
        if market=='KR':row.update(tot_ccld_qty='2',tot_ccld_amt='220',rmn_qty='0',ord_unpr='100')
        else:row.update(ft_ccld_qty='2',ft_ccld_amt3='220',nccs_qty='0',ft_ord_unpr3='100')
        return Response({'output1' if market=='KR' else 'output':[row]})
    result=read_entry_cost_snapshot(fetch,market=market,account_key='prod:test:01',
        account_no='test',product='01',mode='real',start='20260907',end='20260908')
    assert result['balance_authoritative'] and result['orders_authoritative']
    assert len(result['holdings'])==1 and len(result['orders'])==1
    assert result['orders'][0]['filled_amount']==220
    assert all('inquire-' in p or p.endswith(('order-resv-ccnl','order-resv-list')) for p,_,_ in calls)


def test_new_holding_without_order_link_is_not_a_legacy_balance_import(case):
    c=case;holding(c,legacy=False)
    assert reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)['updated_ids']==[]
    assert c.conn.execute(f'SELECT actual_buy_price FROM {c.h}').fetchone()[0] is None


def test_us_local_queue_follows_only_the_scoped_pending_id(case):
    c=case
    if c.market!='US':pytest.skip('US local queue only')
    hid=holding(c,legacy=False);link(c,hid,'PENDING-5')
    c.conn.execute('''CREATE TABLE us_pending_orders(id INTEGER PRIMARY KEY,account_key TEXT,
        ticker TEXT,order_type TEXT,status TEXT,order_result TEXT,created_at TEXT,executed_at TEXT)''')
    c.conn.execute('INSERT INTO us_pending_orders VALUES(?,?,?,?,?,?,?,?)',
        (5,c.account,'TEST','buy','executed',json.dumps({'order_no':'77','order_type':'reserved_limit'}),
         '2026-09-08 09:00:00','2026-09-08 10:05:00'))
    c.conn.commit()
    c.snapshot['orders']=[fill('88',reservation_no='77')]
    assert reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)['updated_ids']==[hid]


def test_invalid_fill_numeric_is_not_treated_as_zero():
    with pytest.raises(ValueError):
        _fills([dict(sll_buy_dvsn_cd='02',pdno='TEST',odno='1',ord_dt='20260908',
                     tot_ccld_qty='NaN',tot_ccld_amt='100',rmn_qty='0')],'KR')


def test_batch_hook_queries_the_active_account_off_event_loop(case, monkeypatch):
    from prism_core.entry_costs import reconcile_agent_entry_costs
    from prism_core.execution_service import ExecutionService
    c=case;holding(c)
    main_thread=threading.get_ident()
    calls=[]
    class ReadOnlyContext:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
        def get_entry_cost_snapshot(self,start,end):
            assert threading.get_ident()!=main_thread
            calls.append((start,end))
            return c.snapshot
    def factory(*,account_name):
        assert account_name=='test'
        return ReadOnlyContext()
    monkeypatch.setattr(ExecutionService,'domestic' if c.market=='KR' else 'us',factory)
    agent=SimpleNamespace(conn=c.conn,_account_scope=lambda:(c.account,'test'))
    asyncio.run(reconcile_agent_entry_costs(agent,c.market))
    assert len(calls)==1 and agent._entry_cost_unresolved==set()
    assert c.conn.execute(f'SELECT buy_price FROM {c.h}').fetchone()[0]==110


def test_unavailable_broker_preserves_last_confirmed_cost(case,monkeypatch):
    from prism_core.entry_costs import reconcile_agent_entry_costs
    from prism_core.execution_service import ExecutionService
    c=case;holding(c)
    reconcile_entry_costs(c.conn,c.market,c.account,c.snapshot)
    def unavailable(**kwargs):raise ConnectionError('synthetic outage')
    monkeypatch.setattr(ExecutionService,'domestic' if c.market=='KR' else 'us',unavailable)
    agent=SimpleNamespace(conn=c.conn,_account_scope=lambda:(c.account,'test'))
    asyncio.run(reconcile_agent_entry_costs(agent,c.market))
    row=rows(c.conn,f'SELECT * FROM {c.h}')[0]
    assert row['actual_buy_price']==110 and row['analysis_price']==100
    assert agent._entry_cost_unresolved==set()
