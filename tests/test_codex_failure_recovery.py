"""Transport recovery tests; no broker or external service calls."""
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from prism_core import codex_subscription as sub
from prism_core.ai_models import settings


@pytest.mark.parametrize('message,code,retry', [
    ('stream disconnected before completion', 'connection', True),
    ('HTTP 503 Service unavailable', 'server_error', True),
    ('HTTP 401 Unauthorized', 'authentication', False),
    ('HTTP 429 rate limit', 'quota', False),
    ('context_length_exceeded', 'invalid_request', False),
    ('invalid schema', 'invalid_request', False),
    ('mysterious issue', 'unknown', False),
])
def test_terminal_error_classification(message, code, retry):
    event = json.dumps({'type': 'turn.failed', 'error': {'message': message}}).encode()
    assert sub._classify_failure(event, b'')[:2] == (code, retry)


def test_ignore_answers_plugin_warnings_and_old_transient_errors():
    assert sub._classify_failure(
        b'{"type":"item.completed","item":{"text":"HTTP 503"}}',
        b'WARN failed to refresh catalog: error sending request')[:2] == ('unknown', False)
    events = b'{"type":"error","message":"stream disconnected"}\n' + json.dumps(
        {'type': 'turn.failed', 'error': {'message': 'quota exceeded'}}).encode()
    assert sub._classify_failure(events, b'')[:2] == ('quota', False)


def mock_processes(monkeypatch, outcomes):
    calls = []
    async def start(*command, **kwargs):
        index = len(calls)
        calls.append(command)
        code, message, answer = outcomes[min(index, len(outcomes)-1)]
        async def communicate(prompt):
            if message:
                kwargs['stdout'].write(json.dumps({'type': 'turn.failed', 'error': {'message': message}}).encode())
            if answer:
                Path(command[command.index('--output-last-message')+1]).write_text(answer)
        return SimpleNamespace(returncode=code, communicate=communicate)
    monkeypatch.setattr(sub, '_binary', lambda: 'codex')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', start)
    monkeypatch.setattr(sub, '_RETRY_DELAYS', (0, 0))
    return calls


@pytest.mark.asyncio
async def test_retries_recover_without_leaking_raw_output(monkeypatch, caplog):
    secret = 'email=user@example.com token=secret-account-value prompt=private-holdings'
    calls = mock_processes(monkeypatch, [(1, 'HTTP 503 '+secret, None),
        (0, None, '{"answer":"ok","calls":[]}')])
    with caplog.at_level('INFO'):
        result = await sub._invoke(settings('research'), 'private prompt', web_search=True)
    assert result['answer'] == 'ok'
    assert len(calls) == 2
    assert 'CODEX_RECOVERED' in caplog.text
    assert 'stage=research' in caplog.text and 'code=server_error' in caplog.text
    for value in ('user@example.com', 'secret-account-value', 'private-holdings', 'private prompt'):
        assert value not in caplog.text
    assert calls[0][calls[0].index('--output-last-message')+1] != calls[1][calls[1].index('--output-last-message')+1]


@pytest.mark.asyncio
@pytest.mark.parametrize('message', ['401 unauthorized', '429 quota exceeded', 'invalid schema', 'unrecognized'])
async def test_nontransient_is_not_retried(monkeypatch, message):
    calls = mock_processes(monkeypatch, [(1, message, None)])
    with pytest.raises(RuntimeError, match='no API fallback'):
        await sub._invoke(settings('kr_buy'), 'prompt')
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_limit(monkeypatch):
    calls = mock_processes(monkeypatch, [(1, 'connection reset', None)])
    with pytest.raises(RuntimeError, match='code=connection'):
        await sub._invoke(settings('kr_sell'), 'prompt')
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_retry_backoff_shares_deadline(monkeypatch):
    calls = mock_processes(monkeypatch, [(1, 'HTTP 503', None)])
    monkeypatch.setattr(sub, '_RETRY_DELAYS', (1, 1))
    with pytest.raises(TimeoutError):
        await sub._invoke(replace(settings('research'), timeout_seconds=.02), 'prompt')
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cancellation_terminates_and_never_retries(monkeypatch):
    started = asyncio.Event()
    process = SimpleNamespace(returncode=None, pid=12345, wait=AsyncMock())
    async def communicate(prompt):
        started.set()
        await asyncio.Event().wait()
    process.communicate = communicate
    start = AsyncMock(return_value=process)
    monkeypatch.setattr(sub, '_binary', lambda: 'codex')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', start)
    kill = []
    monkeypatch.setattr(sub.os, 'killpg', lambda *args: kill.append(args))
    task = asyncio.create_task(sub._invoke(settings('research'), 'prompt'))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(kill) == 1 and start.await_count == 1


def test_stderr_error_without_json_is_classified():
    assert sub._classify_failure(b'not json', b'ERROR HTTP 502 bad gateway')[:2] == ('server_error', True)
    assert sub._classify_failure(b'null\n[]', b'ERROR unexpected failure')[:2] == ('unknown', False)


@pytest.mark.asyncio
async def test_invalid_response_not_retried_or_logged_raw(monkeypatch, caplog):
    calls = mock_processes(monkeypatch, [(0, None, 'private response')])
    with pytest.raises(ValueError, match='Invalid Codex response envelope'):
        await sub._invoke(settings('research'), 'prompt')
    assert len(calls) == 1
    assert 'code=invalid_response' in caplog.text
    assert 'private response' not in caplog.text


@pytest.fixture(autouse=True)
def isolate_metrics(monkeypatch, tmp_path):
    monkeypatch.setattr(sub.metrics, 'quota_snapshot', AsyncMock(return_value={'status': 'unavailable', 'windows': []}))
    monkeypatch.setattr(sub.metrics, 'METRICS_PATH', tmp_path / 'metrics.jsonl')
