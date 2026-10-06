"""Synthetic output regressions; no broker, credentials, or network."""
import json
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace
import pytest
from pydantic import BaseModel
from jsonschema import ValidationError
from prism_core import claude_subscription as claude, codex_subscription as codex
from cores.report_integrity import validate_sections, IncompleteReportError

class Detail(BaseModel):
    feedback: str

class Verdict(BaseModel):
    rating: int
    needs_improvement: bool
    detail: Detail


def test_evaluation_schema_checks_nested_fields_and_tool_turns():
    schema = claude.output_schema(Verdict.model_json_schema())
    claude.validate_envelope({'answer': {'rating': 2, 'needs_improvement': True, 'detail': {'feedback': 'revise'}}, 'calls': []}, schema)
    claude.validate_envelope({'answer': None, 'calls': [{'name': 'read', 'arguments': '{}'}]}, schema)
    for answer in ['', '{"rating":2}', {'rating': 2}, {'rating': 2, 'needs_improvement': True, 'detail': {}}]:
        with pytest.raises(ValidationError):
            claude.validate_envelope({'answer': answer, 'calls': []}, schema)
    with pytest.raises(ValidationError):
        claude.validate_envelope({'answer': {'rating': 2, 'needs_improvement': True, 'detail': {'feedback': 'x'}},
                  'calls': [{'name': 'read', 'arguments': '{}'}]}, schema)


@pytest.mark.asyncio
async def test_claude_structured_transport_normalizes_object_and_retries_missing_field(monkeypatch):
    auth = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'{"loggedIn":true,"authMethod":"claude.ai"}', b'')))
    def process(answer):
        return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(json.dumps({
            'structured_output': {'answer': answer, 'calls': []},
            'modelUsage': {'claude-sonnet-5': {'outputTokens': 10}}
        }).encode(), b'')))
    failed = process({'rating': 2})
    expected = {'rating': 2, 'needs_improvement': True, 'detail': {'feedback': 'fix'}}
    success = process(expected)
    spawn = AsyncMock(side_effect=[auth, failed, success])
    monkeypatch.setattr(claude.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(claude.shutil, 'which', lambda _: '/bin/true')
    monkeypatch.setattr(codex, '_terminate', AsyncMock())
    events = []
    monkeypatch.setattr(claude.metrics, 'emit', events.append)
    result = await codex.run_stage('telegram_evaluator', 'Judge', 'Synthetic draft', response_model=Verdict)
    assert json.loads(result) == expected
    assert spawn.await_count == 3
    assert 'JSON OBJECT' in success.communicate.await_args.args[0].decode()
    assert events[0]['failure_reason'].startswith('schema_validation_')
    assert events[-1]['tokens_complete'] is False


@pytest.mark.parametrize('text,reason', [('', 'empty_body'), ('### Title\nTODO', 'placeholder'),
    ('REPORT_DATA_UNAVAILABLE: private source text', 'source_unavailable'), ('Analysis failed: private', 'upstream_failure')])
def test_report_reason_without_raw_text(text, reason):
    with pytest.raises(IncompleteReportError) as exc:
        validate_sections({'company_overview': text}, ['company_overview'])
    assert exc.value.reasons == {'company_overview': reason}
    assert 'private' not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('recover', [True, False])
async def test_incomplete_report_retry_has_feedback_and_remains_fail_closed(monkeypatch, recover):
    from cores import report_generation as report
    monkeypatch.setattr(report, 'wait_exponential', lambda **kw: lambda state: 0)
    good = '공시에서 해당 기업의 사업을 확인했습니다. PER은 미확인입니다.'
    infer = AsyncMock(side_effect=['REPORT_DATA_UNAVAILABLE: no source', good if recover else 'REPORT_DATA_UNAVAILABLE: still missing'])
    monkeypatch.setattr(report, '_generate_agent_text', infer)
    args = (SimpleNamespace(), 'company_overview', 'Synthetic', '000000', '20261002', Mock())
    if recover:
        assert await report.generate_report(*args) == good
    else:
        with pytest.raises(IncompleteReportError):
            await report.generate_report(*args)
    assert infer.await_count == 2
    assert 'source_unavailable' in infer.await_args_list[1].args[1]
    assert 'DART' in infer.await_args_list[1].args[1]


def test_cli_strict_schema_error_is_not_output_retry():
    error = claude.classify_failure(b'{}', b'strict mode: missing type array (strictTypes)')
    assert error.code == 'claude_schema'
    assert not error.retryable


@pytest.mark.asyncio
async def test_report_auth_failure_is_not_retried(monkeypatch):
    from cores import report_generation as report
    from prism_core.inference_errors import InferenceError
    infer = AsyncMock(side_effect=InferenceError('claude_authentication'))
    monkeypatch.setattr(report, '_generate_agent_text', infer)
    with pytest.raises(InferenceError):
        await report.generate_report(SimpleNamespace(), 'company_overview', 'Synthetic', '000000', '20261002', Mock())
    assert infer.await_count == 1
