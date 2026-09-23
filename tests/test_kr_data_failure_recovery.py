"""Authentication outages recover through existing quote fallbacks, without orders."""
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

import krx_data_client as krx
from tracking import helpers


@pytest.fixture
def quotes(monkeypatch):
    monkeypatch.setattr(krx,'get_nearest_business_day_in_a_week',lambda *a,**k:'20260910')
    fetch=Mock()
    monkeypatch.setattr(krx,'get_market_ohlcv_by_ticker',fetch)
    kis=AsyncMock(return_value=0)
    monkeypatch.setattr(helpers,'_get_price_from_kis',kis)
    cursor=SimpleNamespace(execute=Mock(),fetchone=Mock(return_value=(1800000,)))
    return fetch,kis,cursor


@pytest.mark.asyncio
async def test_auth_error_immediately_uses_kis(quotes):
    fetch,kis,cursor=quotes
    fetch.side_effect=krx.KRXAuthError('[KRX_AUTH_BACKOFF]')
    kis.return_value=1834000
    assert await helpers.get_current_stock_price(cursor,'000660')==1834000
    fetch.assert_called_once()
    kis.assert_awaited_once_with('000660')
    cursor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_auth_error_and_kis_failure_preserve_account_db_fallback(quotes):
    fetch,kis,cursor=quotes
    fetch.side_effect=krx.KRXAuthError('auth')
    assert await helpers.get_current_stock_price(cursor,'000660',account_key='account-a')==1800000
    assert cursor.execute.call_args.args[1]==('000660','account-a')
    fetch.assert_called_once()


@pytest.mark.asyncio
async def test_transient_quote_error_still_retries(quotes,monkeypatch):
    fetch,kis,cursor=quotes
    frame=pd.DataFrame({'Close':[1900000]},index=['000660'])
    fetch.side_effect=[TimeoutError('temporary'),frame]
    monkeypatch.setattr(asyncio,'sleep',AsyncMock())
    assert await helpers.get_current_stock_price(cursor,'000660')==1900000
    assert fetch.call_count==2
    kis.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_krx_quote_does_not_call_kis(quotes):
    fetch,kis,cursor=quotes
    fetch.return_value=pd.DataFrame({'Close':[1900000]},index=['000660'])
    assert await helpers.get_current_stock_price(cursor,'000660')==1900000
    kis.assert_not_awaited()


def test_archive_uses_configured_mode_and_disables_orders(monkeypatch):
    constructor=Mock(return_value=object())
    monkeypatch.setitem(sys.modules,'trading.domestic_stock_trading',
                        SimpleNamespace(DomesticStockTrading=constructor))
    from cores.archive.data_enricher import KRDataEnricher
    enricher=KRDataEnricher()
    assert enricher._get_trading() is constructor.return_value
    assert enricher._get_trading() is constructor.return_value
    constructor.assert_called_once_with(auto_trading=False)


def test_kr_mcp_budget_allows_one_cold_login():
    from cores.llm.config_loader import load_mcp_registry
    spec=load_mcp_registry().get('kospi_kosdaq')
    assert spec.read_timeout_seconds==180
