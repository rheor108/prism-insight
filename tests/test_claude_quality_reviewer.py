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


@pytest.mark.parametrize('failure,code,recover', [
    ('Failed to refresh OAuth token: another Claude Code process is refreshing it or exited mid-refresh.', 'oauth_refresh_busy', True),
    ('Failed to refresh OAuth token: another Claude Code process is refreshing it or exited mid-refresh.', 'oauth_refresh_busy', False),
    ('not logged in secret-token', 'authentication', False),
    ('usage limit secret-token', 'quota', False),
])
def test_retry_only_refresh_contention(monkeypatch, failure, code, recover):
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from tools import review_quality_claude as r
    monkeypatch.setattr(r.subprocess, 'run', Mock(return_value=SimpleNamespace(returncode=0, stdout='{"loggedIn":true,"authMethod":"claude.ai"}')))
    def proc(data):return SimpleNamespace(returncode=0,communicate=Mock(return_value=(json.dumps(data),'')),poll=lambda:0)
    bad=proc({'is_error':True,'result':failure})
    good=proc({'result':'review','modelUsage':{'claude-opus-5':{}}})
    spawn=Mock(side_effect=[bad,good if recover else bad]);monkeypatch.setattr(r.subprocess,'Popen',spawn)
    sleep=Mock();monkeypatch.setattr(r.time,'sleep',sleep)
    if recover:
        result=r.run('synthetic')
        assert result['status']=='success' and len(result['attempts'])==2
    else:
        with pytest.raises(r.ReviewError) as caught:r.run('synthetic')
        assert caught.value.code==code
        assert 'secret-token' not in str(caught.value)
        assert len(caught.value.attempts)==(2 if code=='oauth_refresh_busy' else 1)
    assert spawn.call_count==(2 if code=='oauth_refresh_busy' else 1)
    assert sleep.call_count==(1 if code=='oauth_refresh_busy' else 0)


def test_failed_review_persists_only_safe_categories(monkeypatch,tmp_path,capsys):
    from tools import review_quality_claude as r
    import json
    evidence=tmp_path/'input';evidence.write_text('private report')
    output=tmp_path/'report.json'
    monkeypatch.setattr(__import__('sys'),'argv',['review','--evidence-file',str(evidence),'--output-file',str(output)])
    def fail(_):raise RuntimeError('Bearer secret-token private report')
    monkeypatch.setattr(r,'run',fail)
    with pytest.raises(SystemExit):r.main()
    captured=capsys.readouterr().out
    saved=(tmp_path/'report.json.failure.json').read_text()
    assert json.loads(saved)['error_code']=='local_execution'
    assert 'secret-token' not in captured+saved and 'private report' not in saved
    assert not output.exists()


def test_refresh_wait_uses_original_deadline(monkeypatch):
    from tools import review_quality_claude as r
    from types import SimpleNamespace
    from unittest.mock import Mock
    monkeypatch.setattr(r.subprocess,'run',Mock(return_value=SimpleNamespace(returncode=0,stdout='{"loggedIn":true,"authMethod":"claude.ai"}')))
    busy='{"is_error":true,"result":"Failed to refresh OAuth token: another Claude Code process"}'
    p=SimpleNamespace(returncode=0,communicate=Mock(return_value=(busy,'')),poll=lambda:0)
    monkeypatch.setattr(r.subprocess,'Popen',Mock(return_value=p))
    monkeypatch.setattr(r.time,'monotonic',Mock(side_effect=[0,1,2,2390,2390]))
    sleep=Mock();monkeypatch.setattr(r.time,'sleep',sleep)
    with pytest.raises(r.ReviewError,match='timeout'):r.run('synthetic')
    sleep.assert_not_called()


def test_inference_timeout_terminates_child(monkeypatch):
    from tools import review_quality_claude as r
    from types import SimpleNamespace
    from unittest.mock import Mock
    monkeypatch.setattr(r.subprocess,'run',Mock(return_value=SimpleNamespace(returncode=0,stdout='{"loggedIn":true,"authMethod":"claude.ai"}')))
    p=SimpleNamespace(pid=123,returncode=None,communicate=Mock(side_effect=r.subprocess.TimeoutExpired('claude',1)),poll=lambda:None,wait=Mock())
    monkeypatch.setattr(r.subprocess,'Popen',Mock(return_value=p))
    kill=Mock();monkeypatch.setattr(r.os,'killpg',kill)
    with pytest.raises(r.ReviewError,match='timeout'):r.run('synthetic')
    kill.assert_called_once_with(123,r.signal.SIGTERM)
    p.wait.assert_called_once_with(timeout=3)
