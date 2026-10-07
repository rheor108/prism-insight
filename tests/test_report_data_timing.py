"""Report-only timing regressions: partial data remains visible, never a trade gate."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cores.report_data_timing import KST, row_status
from cores import data_prefetch as prefetch
from cores.agents.stock_price_agents import create_price_volume_analysis_agent
from cores.agents.market_index_agents import create_market_index_analysis_agent


@pytest.mark.parametrize('hour',[8,10,16,23])
def test_today_not_certified_by_wall_clock_even_after_close(hour):
    now=datetime(2026,10,7,hour,tzinfo=KST)
    assert row_status('2026-10-07',now)=='same_day_unconfirmed'
    assert row_status('20261006',now)=='historical_daily'
    assert row_status('2026-10-08',now)=='future_date_unverified'
    assert row_status('bad',now)=='date_unknown'


def test_timezone_conversion_and_naive_rejection():
    now=datetime(2026,10,6,16,tzinfo=timezone.utc) # Oct 7 KST
    assert row_status('2026-10-07',now)=='same_day_unconfirmed'
    with pytest.raises(ValueError):row_status('2026-10-07',datetime(2026,10,7))


def data():
    return {'2026-10-06':{'Close':5010,'Volume':33500000},
            '2026-10-07':{'Close':5630,'Volume':18960000},
            '__meta__':{'note':'upstream note'}}


def test_markdown_adds_status_without_mutating_prices_or_source():
    source=data();before=deepcopy(source)
    text=prefetch._dict_to_markdown(source,'Fixture',ohlcv_retrieved_at=datetime(2026,10,7,9,45,tzinfo=KST))
    assert source==before
    for value in ['5630','18960000','5010','33500000','historical_daily','same_day_unconfirmed','2026-10-07T09:45:00+09:00','upstream note']:assert value in text
    assert '__meta__' not in text
    assert 'Bar status' in text


def test_historical_reference_date_does_not_make_last_row_intraday():
    text=prefetch._dict_to_markdown({'2026-10-02':{'Close':100,'Volume':200}},ohlcv_retrieved_at=datetime(2026,10,7,10,tzinfo=KST))
    row=next(line for line in text.splitlines() if line.startswith('| 2026-10-02'))
    assert 'historical_daily' in row and 'same_day_unconfirmed' not in row


def test_prefetch_stock_and_index_both_include_retrieval_status(monkeypatch):
    source=data()
    server=SimpleNamespace(get_stock_ohlcv=lambda *a:source,get_index_ohlcv=lambda *a:source)
    monkeypatch.setattr(prefetch,'_get_mcp_server_module',lambda:server)
    monkeypatch.setattr(prefetch,'now_kst',lambda:datetime(2026,10,7,10,tzinfo=KST))
    for text in [prefetch.prefetch_stock_ohlcv('000000','20261006','20261007'),prefetch.prefetch_index_ohlcv('1001','20261006','20261007')]:
        assert 'Bar status' in text and 'same_day_unconfirmed' in text
    assert 'Bar status' not in source['2026-10-07']


@pytest.mark.parametrize('language',['ko','en'])
@pytest.mark.parametrize('cached',[True,False])
def test_agent_rules_cover_prefetched_and_tool_fallback(language,cached):
    stock=create_price_volume_analysis_agent('Fixture','000000','20261007','20251007',1,language=language,prefetched_data='fixture' if cached else None)
    market=create_market_index_analysis_agent('20261007','20251007',1,language=language,prefetched_kospi='fixture' if cached else None,prefetched_kosdaq='fixture' if cached else None)
    for agent in [stock,market]:
        assert 'same_day_unconfirmed' in agent.instruction
        assert ('동일 경과시간' if language=='ko' else 'matching elapsed-session') in agent.instruction
        assert ('잠정 지표' if language=='ko' else 'provisional') in agent.instruction
    assert stock.server_names==(() if cached else ('kospi_kosdaq',))


def test_empty_errors_and_non_ohlcv_tables_unchanged():
    now=datetime(2026,10,7,10,tzinfo=KST)
    assert prefetch._dict_to_markdown({},ohlcv_retrieved_at=now)==''
    assert prefetch._dict_to_markdown({'error':'unavailable'},ohlcv_retrieved_at=now)==''
    text=prefetch._dict_to_markdown({'2026-10-07':{'net_buy':123}})
    assert 'Bar status' not in text and 'retrieved_at' not in text
