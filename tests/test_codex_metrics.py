import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from prism_core import codex_metrics as m
from prism_core import codex_subscription as sub
from prism_core.ai_models import settings


def snapshot(used=53, reset=100):
    return {'status': 'available', 'windows': m.windows({'rateLimits': {
        'limitId': 'codex', 'primary': {'usedPercent': used, 'resetsAt': reset,
                                      'windowDurationMins': 10080}}})}


def test_delta_and_resets_missing_zero():
    assert m.quota_delta(snapshot(52), snapshot())[0]['account_delta_percentage_points'] == 1
    assert m.quota_delta(snapshot(), snapshot())[0]['account_delta_percentage_points'] == 0
    for before, after in [(snapshot(), snapshot(0, 200)), ({}, snapshot()), (snapshot(), snapshot(52))]:
        assert m.quota_delta(before, after)[0]['account_delta_percentage_points'] is None
    assert m.windows({'rateLimits': {'primary': {'usedPercent': None}}}) == []


def test_tokens_only_completion_numbers():
    data = b'bad json\n' + json.dumps({'type': 'item.completed', 'usage': {'output_tokens': 999},
        'text': 'secret'}).encode() + b'\n' + json.dumps({'type': 'turn.completed', 'usage': {
        'input_tokens': 100, 'cached_input_tokens': 90, 'output_tokens': 10, 'account': 'secret'}}).encode()
    assert m.token_usage(io.BytesIO(data)) == {'input_tokens': 100, 'cached_input_tokens': 90, 'output_tokens': 10}
    assert m.token_usage(io.BytesIO(b'')) is None


@pytest.mark.asyncio
async def test_rpc_only_reads_limits_and_cleans_up(monkeypatch):
    sent = []
    inp = SimpleNamespace(write=lambda b: sent.append(json.loads(b)), drain=AsyncMock())
    out = SimpleNamespace(readline=AsyncMock(side_effect=[
        b'{"id":1,"result":{}}\n', json.dumps({'id': 2, 'result': {'rateLimits': {
            'limitId': 'codex', 'accountId': 'secret', 'primary': {'usedPercent': 0}}}}).encode()+b'\n']))
    proc = SimpleNamespace(stdin=inp, stdout=out, returncode=None)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=proc))
    stop = AsyncMock()
    monkeypatch.setattr(sub, '_terminate', stop)
    result = await m.quota_snapshot('codex', {})
    assert [v['method'] for v in sent] == ['initialize', 'initialized', 'account/rateLimits/read']
    assert result['status'] == 'available' and 'secret' not in json.dumps(result)
    stop.assert_awaited_once_with(proc)


@pytest.mark.asyncio
async def test_rpc_failure_unavailable_not_zero(monkeypatch):
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(side_effect=OSError('secret')))
    result = await m.quota_snapshot('codex', {})
    assert result['status'] == 'unavailable' and result['windows'] == []
    assert 'secret' not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('exit_code', [0, 1])
async def test_call_and_attempt_records_on_success_failure(monkeypatch, tmp_path, exit_code):
    monkeypatch.setattr(m, 'METRICS_PATH', tmp_path / 'calls.jsonl')
    monkeypatch.setattr(m, 'quota_snapshot', AsyncMock(side_effect=[snapshot(52), snapshot(53)]))
    monkeypatch.setattr(sub, '_binary', lambda: 'codex')
    async def start(*command, **kwargs):
        async def communicate(prompt):
            kwargs['stdout'].write(b'{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":4}}\n')
            if exit_code == 0:
                Path(command[command.index('--output-last-message')+1]).write_text('{"answer":"ok","calls":[]}')
        return SimpleNamespace(returncode=exit_code, communicate=communicate)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', start)
    if exit_code:
        with pytest.raises(RuntimeError):
            await sub._invoke(settings('research'), 'private prompt')
    else:
        await sub._invoke(settings('research'), 'private prompt')
    records = [json.loads(v) for v in m.METRICS_PATH.read_text().splitlines()]
    assert [r['kind'] for r in records] == ['attempt', 'call']
    assert records[0]['tokens']['input_tokens'] == 100
    assert records[1]['duration_seconds'] >= 0
    assert records[1]['outcome'] == ('failed' if exit_code else 'success')
    assert records[1]['quota_changes'][0]['account_delta_percentage_points'] == 1
    assert records[1]['quota_scope'] == 'account_shared_not_per_call'
    assert 'private prompt' not in m.METRICS_PATH.read_text()
    assert m.METRICS_PATH.stat().st_mode & 0o777 == 0o600


def test_write_failure_is_nonfatal(monkeypatch, tmp_path):
    monkeypatch.setattr(m, 'METRICS_PATH', tmp_path)
    m.emit({'kind': 'call'})
