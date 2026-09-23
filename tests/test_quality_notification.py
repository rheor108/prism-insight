from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from tools.send_quality_notification import send

@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
    monkeypatch.setenv('TELEGRAM_CHANNEL_ID', 'test-channel')


def test_success_and_dedup(tmp_path):
    post = Mock(return_value=SimpleNamespace(json=lambda: {'ok': True, 'result': {'message_id': 1}}))
    kw = dict(state_path=tmp_path/'state.db', post=post)
    assert send('event-1', 'report', **kw)['status'] == 'sent'
    assert send('event-1', 'report', **kw)['status'] == 'duplicate_suppressed'
    assert post.call_count == 1
    with pytest.raises(ValueError):
        send('event-1', 'different', **kw)


def test_uncertain_delivery_not_retried(tmp_path):
    post = Mock(side_effect=TimeoutError('secret-token'))
    kw = dict(state_path=tmp_path/'state.db', post=post)
    assert send('event-2', 'report', **kw)['status'] == 'uncertain'
    result = send('event-2', 'report', **kw)
    assert result['previous_status'] == 'uncertain' and post.call_count == 1


def test_rejected(tmp_path):
    post = Mock(return_value=SimpleNamespace(json=lambda: {'ok': False, 'description': 'private'}))
    assert send('event-3', 'report', state_path=tmp_path/'state.db', post=post) == {'status': 'rejected', 'message_id': None}
