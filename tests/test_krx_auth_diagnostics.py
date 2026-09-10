"""Offline auth failures: capture evidence without changing login or leaking secrets."""
import importlib.util
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType
from unittest.mock import AsyncMock

import pytest

from patches.krx import auth_diagnostics as diag
from tools.install_krx_auth_diagnostics import patched_source


def test_redaction_and_response_allowlist():
    password = 'p"w\\secret!'
    secret = 'shortcookie'
    text = diag.redact(
        f'{password} {secret} token=abc https://data.krx.co.kr/login?pw=hidden '
        'Bearer abcd user@example.com 010-1234-5678\nblocked', [password, secret])
    for forbidden in [password, secret, 'hidden', 'abcd', 'user@example.com', '1234', '\n']:
        assert forbidden not in text
    assert 'blocked' in text
    assert diag.response_fields({'pw': 'hidden', 'data': {'code': 'E_LOGIN',
        'message': '접근 제한', 'session': 'secret'}, 'huge': ['private']}) == {
            'data': {'code': 'E_LOGIN', 'message': '접근 제한'}}


def page_fixture():
    body = SimpleNamespace(inner_text=AsyncMock(return_value=
        '로그인 오류: 사용자 tester 비밀번호 p"w\\secret! token=abcd cookievalue'))
    frame = SimpleNamespace(url='https://data.krx.co.kr/login?session=hidden',
                            locator=lambda _: body)
    page = SimpleNamespace(frames=[frame], context=SimpleNamespace(
        cookies=AsyncMock(return_value=[{'name': 'JSESSIONID', 'value': 'cookievalue'}])),
        on=lambda *_: None, remove_listener=lambda *_: None)
    return page


@pytest.mark.asyncio
async def test_failure_evidence_is_bounded_and_redacted(caplog):
    logger = logging.getLogger('test.krx')
    evidence = diag.AuthDiagnostics(logger, ['tester', 'p"w\\secret!'])
    evidence.attach(page_fixture())
    response = SimpleNamespace(url='https://data.krx.co.kr/auth?token=hidden', status=403,
        request=SimpleNamespace(method='POST', resource_type='xhr'),
        headers={'content-type': 'application/json', 'content-length': '100'},
        json=AsyncMock(return_value={'code': 'E_AUTH', 'message': '접근 제한 tester',
                                    'password': 'never log'}))
    await evidence.capture_response(response, 'login_submit')
    evidence.on_dialog(SimpleNamespace(type='alert', message='중복 로그인 tester'))
    with caplog.at_level(logging.WARNING):
        await evidence.failure('data_redirect')
        await evidence.failure('login_exception')
    await evidence.close()
    records = [r.message for r in caplog.records if '[KRX_AUTH_DIAGNOSTIC]' in r.message]
    assert len(records) == 1
    record = json.loads(records[0].split(' ', 1)[1])
    assert record['phase'] == 'data_redirect'
    assert record['events'][0]['status'] == 403
    assert record['events'][0]['fields']['code'] == 'E_AUTH'
    assert '접근 제한' in records[0] and '중복 로그인' in records[0]
    for secret in ['tester', 'cookievalue', 'hidden', 'never log', 'secret!', 'abcd']:
        assert secret not in records[0]
    assert evidence.log_filter not in logger.filters


@pytest.mark.asyncio
async def test_capture_failure_does_not_replace_auth_failure(caplog):
    evidence = diag.AuthDiagnostics(logging.getLogger('test.krx.capture'))
    page = page_fixture()
    page.context.cookies.side_effect = RuntimeError('secret exception')
    evidence.attach(page)
    await evidence.failure('data_redirect')
    await evidence.close()
    assert 'capture_error' in caplog.text
    assert 'secret exception' not in caplog.text


@pytest.mark.asyncio
async def test_large_or_non_json_response_is_not_read():
    evidence = diag.AuthDiagnostics(logging.getLogger('test.krx.body'))
    response = SimpleNamespace(url='https://data.krx.co.kr/auth', status=200,
        request=SimpleNamespace(method='POST'),
        headers={'content-type': 'application/json', 'content-length': '999999'},
        json=AsyncMock())
    await evidence.capture_response(response, 'submit')
    response.json.assert_not_called()
    for _ in range(50):
        evidence.on_dialog(SimpleNamespace(type='alert', message='실패'))
    assert len(evidence.login_events) == 20
    await evidence.close()


def test_unknown_dependency_refused():
    with pytest.raises(ValueError, match='Unsupported KRX dependency hash'):
        patched_source('different dependency')


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['data_redirect', 'credential_exception'])
async def test_patched_real_login_captures_before_browser_cleanup(monkeypatch, caplog, failure):
    """Run the installed dependency's patched method with a fake browser, no network."""
    spec = importlib.util.find_spec('krx_data_client')
    if spec is None:
        pytest.skip('Optional KRX dependency not installed')
    path = Path(spec.origin)
    backup = path.with_suffix('.py.prism-original')
    original = (backup if backup.exists() else path).read_text()
    patched = patched_source(original)
    monkeypatch.setitem(sys.modules, 'prism_krx_auth_diagnostics', diag)
    module = ModuleType('krx_diagnostic_fixture')
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(patched, str(path), 'exec'), module.__dict__)
    page = page_fixture()
    page.url = 'https://data.krx.co.kr/login'
    page.closed = False

    async def goto(url, **kwargs):
        if failure == 'credential_exception' and 'MAIN/main' in url:
            raise RuntimeError('login failed tester p"w\\secret! https://data.krx.co.kr/auth?token=hidden')
        page.url = ('https://data.krx.co.kr/MDCCOMS001.cmd?session=hidden'
                    if 'menuId=' in url else url)
        page.frames[0].url = page.url

    page.goto = goto
    context = page.context
    context.new_page = AsyncMock(return_value=page)
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context))
    runtime = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
    from playwright import async_api
    monkeypatch.setattr(async_api, 'async_playwright',
                        lambda: SimpleNamespace(start=AsyncMock(return_value=runtime)))
    monkeypatch.setattr(module.asyncio, 'sleep', AsyncMock())
    manager = module.KRXAuthManager.__new__(module.KRXAuthManager)
    manager.krx_id, manager.krx_pw, manager.headless = 'tester', 'p"w\\secret!', True
    input_element = SimpleNamespace(fill=AsyncMock(), click=AsyncMock())
    form = SimpleNamespace(wait_for_selector=AsyncMock(return_value=input_element))
    iframe = SimpleNamespace(content_frame=AsyncMock(return_value=form))
    manager._require_login_iframe = AsyncMock(return_value=iframe)
    manager._cleanup_browser = AsyncMock()
    with caplog.at_level(logging.WARNING):
        with pytest.raises(module.KRXAuthError) as raised:
            await manager._login_async_krx()
    assert manager._cleanup_browser.await_count >= 1
    assert ('data_redirect' if failure == 'data_redirect' else 'login_exception') in caplog.text
    assert '로그인 오류' in caplog.text
    assert '다른 프로세스의 로그인으로' not in caplog.text
    assert 'tester' not in caplog.text and 'cookievalue' not in caplog.text
    assert all(secret not in str(raised.value) for secret in ['tester', 'secret!', 'hidden'])


def test_redaction_preserves_diagnostic_keys_and_paths():
    message = '[KRX_SESSION_VALIDATION] url=https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd?session=hidden'
    cleaned = diag.redact(message)
    assert '[KRX_SESSION_VALIDATION]' in cleaned
    assert 'https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd' in cleaned
    assert 'hidden' not in cleaned
    assert diag.redact(cleaned) == cleaned


@pytest.mark.asyncio
async def test_closed_real_browser_cleanup_preserves_original_error():
    from playwright.async_api import async_playwright
    logger = logging.getLogger('test.krx.closed')
    driver = await async_playwright().start()
    browser = await driver.chromium.launch(headless=True)
    evidence = diag.AuthDiagnostics(logger)
    evidence.attach(await browser.new_page())
    await browser.close()
    await driver.stop()
    await evidence.close()
    await evidence.close()
    assert evidence.log_filter not in logger.filters


@pytest.mark.asyncio
async def test_login_evidence_survives_home_response_flood(caplog):
    evidence = diag.AuthDiagnostics(logging.getLogger('test.krx.retention'))
    evidence.attach(page_fixture())
    await evidence.capture_page('after_submit')
    response = SimpleNamespace(url='https://data.krx.co.kr/auth?secret=x',status=401,
        request=SimpleNamespace(method='POST'),
        headers={'content-type':'application/json','content-length':'100'},
        json=AsyncMock(return_value={'errorCode':'LOGIN_FAILED','message':'인증 오류'}))
    await evidence.capture_response(response, 'login_submit')
    for _ in range(50):
        response.status = 200
        await evidence.capture_response(response, 'home')
    await evidence.failure('data_redirect')
    await evidence.close()
    assert 'LOGIN_FAILED' in caplog.text and 'after_submit' in caplog.text
    assert len(evidence.events)==20


@pytest.fixture
def patched_module(monkeypatch):
    spec = importlib.util.find_spec('krx_data_client')
    if spec is None:
        pytest.skip('Optional KRX dependency not installed')
    path = Path(spec.origin)
    backup = path.with_suffix('.py.prism-original')
    monkeypatch.setitem(sys.modules, 'prism_krx_auth_diagnostics', diag)
    module = ModuleType('krx_retry_fixture')
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(patched_source((backup if backup.exists() else path).read_text()),
                 str(path),'exec'),module.__dict__)
    return module


def auth_manager(module, tmp_path):
    from unittest.mock import Mock
    manager = module.KRXAuthManager.__new__(module.KRXAuthManager)
    manager.LOCK_PATH = tmp_path/'auth.lock'
    manager.krx_id = 'test_account'
    manager.login_method = 'krx'
    manager._load_session = Mock(return_value=False)
    manager._blocked_until = Mock(return_value=None)
    manager._cleanup_session_files = Mock()
    manager._clear_blocked = Mock()
    manager._login_async_krx = AsyncMock(side_effect=module.KRXAuthError('fixture auth failure'))
    return manager


def test_auth_failure_backoff_is_shared_and_expires(patched_module,tmp_path,monkeypatch):
    first = auth_manager(patched_module,tmp_path)
    with pytest.raises(patched_module.KRXAuthError,match='KRX_AUTH_BACKOFF'):
        first.login()
    first._login_async_krx.assert_awaited_once()
    second = auth_manager(patched_module,tmp_path)
    with pytest.raises(patched_module.KRXAuthError,match='no login attempted'):
        second.login(force=True)
    second._load_session.assert_not_called()
    second._login_async_krx.assert_not_awaited()
    assert 'test_account' not in diag._retry_path(first).read_text()
    original_time = diag.time.time()
    monkeypatch.setattr(diag.time,'time',lambda:original_time+301)
    second._login_async_krx = AsyncMock(return_value=True)
    assert second.login() is True
    assert diag.auth_retry_remaining(second)==0


@pytest.mark.parametrize('status',[400,403,500])
def test_http_validation_error_does_not_delete_session_or_relogin(patched_module,tmp_path,status):
    from unittest.mock import Mock
    manager = auth_manager(patched_module,tmp_path)
    manager._load_session.return_value = True
    manager._last_validated = None
    manager._get_recent_business_day = lambda:'20260910'
    manager._session = SimpleNamespace(post=Mock(return_value=SimpleNamespace(
        status_code=status,headers={'Content-Type':'text/html'},url='https://data.krx.co.kr/check')))
    with pytest.raises(patched_module.KRXAuthError,match='KRX_VALIDATION_HTTP'):
        manager.login()
    manager._cleanup_session_files.assert_not_called()
    manager._login_async_krx.assert_not_awaited()


def test_valid_session_still_reused(patched_module,tmp_path):
    from unittest.mock import Mock
    manager = auth_manager(patched_module,tmp_path)
    manager._load_session.return_value=True
    manager._last_validated=None
    manager._get_recent_business_day=lambda:'20260910'
    manager._update_last_validated=Mock()
    manager._session=SimpleNamespace(post=Mock(return_value=SimpleNamespace(
        status_code=200,headers={'Content-Type':'application/json'},text='{"output":[]}',
        json=lambda:{'output':[]})))
    assert manager.login() is True
    manager._login_async_krx.assert_not_awaited()


def test_data_http_400_is_not_misclassified_as_expiration(patched_module):
    from unittest.mock import Mock
    import requests
    client=patched_module.KRXDataClient.__new__(patched_module.KRXDataClient)
    client._ensure_session=Mock()
    response=requests.Response()
    response.status_code=400
    response.url='https://data.krx.co.kr/data'
    response.headers['Content-Type']='text/html'
    response._content=b'<html>bad request</html>'
    client._auth_manager=SimpleNamespace(session=SimpleNamespace(post=Mock(return_value=response)))
    with pytest.raises(patched_module.KRXDataError,match='KRX_DATA_HTTP'):
        client._request('test',{})
