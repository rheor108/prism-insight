import pytest
from tools.review_quality_claude import command, environment, validate


def test_exact_model_effort_no_tools(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'secret')
    monkeypatch.setenv('ANTHROPIC_MODEL', 'other')
    monkeypatch.setenv('CLAUDE_CODE_USE_BEDROCK', '1')
    cmd = command()
    assert cmd[cmd.index('--model')+1] == 'claude-opus-5'
    assert cmd[cmd.index('--effort')+1] == 'max'
    assert cmd[cmd.index('--tools')+1] == ''
    assert '--fallback-model' not in cmd
    assert not any(k.startswith('ANTHROPIC_') for k in environment())
    assert 'CLAUDE_CODE_USE_BEDROCK' not in environment()


@pytest.mark.parametrize('value', [
    {'result': 'ok'}, {'result': 'ok', 'modelUsage': {'claude-sonnet-5': {}}},
    {'result': '', 'modelUsage': {'claude-opus-5': {}}},
    {'result': 'error', 'is_error': True, 'modelUsage': {'claude-opus-5': {}}}])
def test_fail_closed(value):
    with pytest.raises(ValueError): validate(value)


def test_opus_response():
    validate({'result': 'report', 'modelUsage': {'claude-opus-5': {}}})


def test_cli_auxiliary_haiku_is_not_a_reviewer_fallback():
    validate({'result': 'report', 'modelUsage': {'claude-opus-5': {}, 'claude-haiku-4-5-20251001': {}}})
    with pytest.raises(ValueError):
        validate({'result': 'report', 'modelUsage': {'claude-haiku-4-5-20251001': {}}})
