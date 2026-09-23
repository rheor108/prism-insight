import json
from unittest.mock import AsyncMock

import pytest

from prism_core.ai_models import settings
from prism_core import claude_subscription as claude
from prism_core import codex_subscription as codex


def test_claude_subscription_command_and_environment(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_AUTH_TOKEN', 'secret')
    monkeypatch.setenv('CLAUDE_CODE_USE_BEDROCK', '1')
    env = claude.environment('medium')
    assert 'ANTHROPIC_AUTH_TOKEN' not in env
    assert 'CLAUDE_CODE_USE_BEDROCK' not in env
    cmd = claude.command('claude', settings('news'), codex.ENVELOPE)
    assert cmd[cmd.index('--model') + 1] == 'claude-sonnet-5'
    assert cmd[cmd.index('--tools') + 1] == ''
    assert '--safe-mode' in cmd and '--restricted' in cmd
    assert '--json-schema' in cmd
    assert env['CLAUDE_CODE_EFFORT_LEVEL'] == 'medium'


def result(model='claude-sonnet-5', envelope=None):
    return json.dumps({'structured_output': envelope or {'answer': 'ok', 'calls': []},
                      'modelUsage': {model: {'inputTokens': 10, 'outputTokens': 2}}})


def test_parse_validates_model_schema_and_usage():
    value, usage = claude.parse_result(result(), 'claude-sonnet-5', codex.ENVELOPE)
    assert value['answer'] == 'ok' and usage['input_tokens'] == 10
    with pytest.raises(ValueError, match='model'):
        claude.parse_result(result('claude-opus-5'), 'claude-sonnet-5', codex.ENVELOPE)
    from jsonschema import ValidationError
    with pytest.raises(ValidationError):
        claude.parse_result(result(envelope={'answer': 5}), 'claude-sonnet-5', codex.ENVELOPE)


@pytest.mark.asyncio
async def test_claude_dispatch_and_parent_tool_roundtrip(monkeypatch):
    class Tools:
        async def list_tools(self):
            from types import SimpleNamespace
            return [SimpleNamespace(name='sqlite-read_query', description='', inputSchema={'type': 'object'})]
        call_tool = AsyncMock(return_value={'rows': [123]})
    tools = Tools()
    invoke = AsyncMock(side_effect=[
        {'answer': '', 'calls': [{'name': 'sqlite-read_query', 'arguments': '{"query":"SELECT 123"}'}]},
        {'answer': 'verified', 'calls': []}])
    monkeypatch.setattr(claude, 'invoke', invoke)
    assert await codex.run_stage('news', 'Analyze', 'test', provider=tools) == 'verified'
    tools.call_tool.assert_awaited_once_with('sqlite-read_query', {'query': 'SELECT 123'})
    assert '123' in invoke.await_args_list[1].args[1]
    assert invoke.await_args.args[0].provider == 'claude_subscription'
    assert settings('video', variant='filter').provider == 'codex_subscription'


@pytest.mark.asyncio
async def test_failure_does_not_fallback(monkeypatch):
    monkeypatch.setattr(claude, 'invoke', AsyncMock(side_effect=RuntimeError('Claude unavailable')))
    with pytest.raises(RuntimeError, match='Claude unavailable'):
        await codex.run_stage('journal', 'Review', 'test')


@pytest.mark.asyncio
async def test_unsupported_native_capabilities_fail_before_execution():
    with pytest.raises(ValueError):
        await claude.invoke(settings('news'), 'test', web_search=True)


@pytest.mark.asyncio
async def test_api_auth_is_rejected_and_metrics_mark_unknown(monkeypatch):
    from types import SimpleNamespace
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(
        b'{"loggedIn":true,"authMethod":"api_key"}', b'')))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(claude.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(claude.shutil, 'which', lambda _: '/bin/true')
    events = []
    monkeypatch.setattr(claude.metrics, 'emit', events.append)
    with pytest.raises(RuntimeError, match='claude_authentication'):
        await claude.invoke(settings('news'), 'test')
    assert spawn.await_count == 1
    assert events[0]['outcome'] == 'failure'
    assert events[0]['quota_after']['status'] == 'unavailable'


@pytest.mark.asyncio
async def test_timeout_terminates_claude_child(monkeypatch):
    import asyncio
    from dataclasses import replace
    from types import SimpleNamespace
    async def communicate():
        await asyncio.Event().wait()
    process = SimpleNamespace(returncode=None, communicate=communicate)
    monkeypatch.setattr(claude.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    monkeypatch.setattr(claude.shutil, 'which', lambda _: '/bin/true')
    terminate = AsyncMock()
    monkeypatch.setattr(codex, '_terminate', terminate)
    monkeypatch.setattr(claude.metrics, 'emit', lambda _: None)
    with pytest.raises(TimeoutError):
        await claude.invoke(replace(settings('news'), timeout_seconds=0.01), 'test')
    terminate.assert_awaited_once_with(process)
