"""Regressions for the September 23 batch, with no live inference or orders."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from cores.report_integrity import IncompleteReportError, validate_sections
from prism_core import codex_subscription as sub, claude_subscription as claude
from prism_core.inference_errors import InferenceError, should_retry


@pytest.mark.parametrize('text', ['', '### News\n테스트', 'Analysis failed: news',
    'Investment strategy analysis failed', 'REPORT_DATA_UNAVAILABLE: all sources failed'])
def test_incomplete_reports_are_rejected(text):
    with pytest.raises(IncompleteReportError):
        validate_sections({'news': text}, ['news'])


def test_evidence_with_disclosed_missing_metric_remains_valid():
    validate_sections({'news': '공시에서 신규 수주가 확인되었습니다. PER은 확인 불가입니다.'}, ['news'])


@pytest.mark.asyncio
async def test_empty_answer_recovers_once(monkeypatch):
    invoke = AsyncMock(side_effect=[{'answer': '', 'calls': []}, {'answer': 'Evidence unavailable.', 'calls': []}])
    monkeypatch.setattr(sub, '_invoke', invoke)
    assert await sub.run_stage('market', 'Analyze', 'Synthetic input') == 'Evidence unavailable.'
    assert invoke.await_count == 2


@pytest.mark.asyncio
async def test_empty_answer_stops_after_one_retry(monkeypatch):
    invoke = AsyncMock(return_value={'answer': '', 'calls': []})
    monkeypatch.setattr(sub, '_invoke', invoke)
    with pytest.raises(ValueError, match='after one retry'):
        await sub.run_stage('market', 'Analyze', 'Synthetic input')
    assert invoke.await_count == 2


@pytest.mark.asyncio
async def test_fenced_structured_answer(monkeypatch):
    class Verdict(BaseModel):
        rating: int
    monkeypatch.setattr(sub, '_invoke', AsyncMock(return_value={'answer': '```json\n{"rating": 3}\n```', 'calls': []}))
    answer = await sub.run_stage('telegram_evaluator', 'Synthetic', 'input', response_model=Verdict)
    assert json.loads(answer) == {'rating': 3}


@pytest.mark.parametrize('raw,code,retry', [(b'not logged in secret-value','claude_authentication',False),
    (b'429 quota','claude_quota',False), (b'network connection','claude_connection',True)])
def test_safe_claude_error_classification(raw, code, retry):
    error = claude.classify_failure(b'{}', raw)
    assert error.code == code and should_retry(error) == retry
    assert 'secret-value' not in str(error)


def test_success_exit_with_error_payload_is_classified():
    with pytest.raises(InferenceError, match='claude_authentication'):
        claude.parse_result('{"is_error":true,"result":"not logged in"}', 'claude-sonnet-5', sub.ENVELOPE)


@pytest.mark.parametrize('name,code,company', [('440110_Stock_440110_20260923_report.pdf','440110','Stock_440110'),
    ('059090_미코_20260923_report.pdf','059090','미코'), ('BRK.B_Berkshire_Hathaway_20260923_report.pdf','BRK.B','Berkshire_Hathaway')])
def test_filename_identity(name, code, company):
    from telegram_summary_agent import TelegramSummaryGenerator
    result = TelegramSummaryGenerator.extract_metadata_from_filename(None, name)
    assert result['stock_code'] == code and result['stock_name'] == company


def test_concurrent_token_refresh_issues_once(monkeypatch, tmp_path):
    from trading import kis_auth as ka
    monkeypatch.setattr(ka, 'config_root', str(tmp_path))
    monkeypatch.setattr(ka, '_cfg', {'prod': 'https://example.invalid'})
    monkeypatch.setattr(ka, 'resolve_account', lambda **kw: {'app_key': 'test', 'app_secret': 'test'})
    monkeypatch.setattr(ka, 'validate_credentials', lambda *a: (True, ''))
    monkeypatch.setattr(ka, 'changeTREnv', lambda *a, **kw: None)
    monkeypatch.setattr(ka, '_TRENV', None)
    token = []
    issued = []
    monkeypatch.setattr(ka, 'read_token', lambda **kw: token[0] if token else None)
    monkeypatch.setattr(ka, 'save_token', lambda value, expiry, **kw: token.append(value))
    def issue(*args):
        issued.append(1)
        time.sleep(0.05)
        return {'access_token': 'synthetic', 'access_token_token_expired': '2099-01-01 00:00:00'}
    monkeypatch.setattr(ka, '_request_token_with_retry', issue)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: ka.auth(), range(6)))
    assert len(issued) == 1
    assert token == ['synthetic']


def test_index_detection_uses_authenticated_client(monkeypatch):
    from cores import stock_chart as chart
    import krx_data_client
    monkeypatch.setattr(chart, '_KOSPI_TICKERS_CACHE', None)
    monkeypatch.setattr(krx_data_client, 'get_market_ticker_list', lambda **kw: ['005930'])
    assert chart._detect_index_ticker('005930') == '1001'
    assert chart._detect_index_ticker('059090') == '2001'


def test_empty_listing_is_not_cached_as_kosdaq(monkeypatch):
    from cores import stock_chart as chart
    import krx_data_client
    monkeypatch.setattr(chart, '_KOSPI_TICKERS_CACHE', None)
    monkeypatch.setattr(krx_data_client, 'get_market_ticker_list', lambda **kw: [])
    assert chart._detect_index_ticker('005930') == '1001'
    assert chart._KOSPI_TICKERS_CACHE is None


@pytest.mark.asyncio
async def test_trigger_stock_name_is_preserved_without_pykrx(monkeypatch, tmp_path):
    import pandas as pd
    import trigger_batch
    from stock_analysis_orchestrator import StockAnalysisOrchestrator
    monkeypatch.chdir(tmp_path)
    frame = {'selected': pd.DataFrame({'stock_name': ['미코']}, index=['059090'])}
    monkeypatch.setattr(asyncio.get_running_loop(), 'run_in_executor', AsyncMock(return_value=frame))
    orchestrator = StockAnalysisOrchestrator.__new__(StockAnalysisOrchestrator)
    orchestrator.selected_tickers = {}
    result = await orchestrator.run_trigger_batch('morning')
    assert result[0]['name'] == '미코'


@pytest.mark.asyncio
async def test_failed_summary_is_propagated(monkeypatch):
    from cores import report_generation as report
    from unittest.mock import Mock
    monkeypatch.setattr(report, '_generate_agent_text', AsyncMock(side_effect=InferenceError('claude_authentication')))
    with pytest.raises(InferenceError):
        await report.generate_summary({'news': 'Evidence'}, '미코', '059090', '20260923', Mock())


@pytest.mark.asyncio
async def test_failed_section_cannot_reach_strategy_or_charts(monkeypatch):
    from cores import analysis
    from cores import data_prefetch
    monkeypatch.setattr(data_prefetch, 'prefetch_kr_analysis_data', lambda *a, **kw: {})
    monkeypatch.setattr(analysis, 'get_agent_directory', lambda name, code, date, sections, *a, **kw: {s: object() for s in sections})
    monkeypatch.setattr(analysis, 'generate_report', AsyncMock(side_effect=InferenceError('claude_authentication')))
    monkeypatch.setattr(analysis, 'generate_market_report', AsyncMock(return_value='Verified market evidence'))
    strategy = AsyncMock()
    monkeypatch.setattr(analysis, 'generate_investment_strategy', strategy)
    monkeypatch.setattr(analysis, '_market_analysis_cache', {})
    with pytest.raises(IncompleteReportError):
        await analysis.analyze_stock('059090', '미코', '20260923')
    strategy.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_company_identity_does_not_generate_report(monkeypatch, tmp_path):
    import cores.market_data
    import stock_analysis_orchestrator as module
    monkeypatch.setattr(cores.market_data, 'get_market_ticker_name', lambda ticker: '')
    monkeypatch.setattr(asyncio, 'to_thread', AsyncMock(return_value=''))
    monkeypatch.setattr(module, 'REPORTS_DIR', tmp_path)
    orchestrator = module.StockAnalysisOrchestrator.__new__(module.StockAnalysisOrchestrator)
    assert await orchestrator.generate_reports([{'code': '059090', 'name': ''}], 'morning') == []
    assert not list(tmp_path.iterdir())
