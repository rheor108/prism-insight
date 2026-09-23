"""No live research, credentials, trading or notifications in these tests."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from prism_core import codex_subscription as sub
from prism_core import research_quality as quality


def claim(**updates):
    value = {
        'id': 'ds_revenue', 'requested_item': 'DS 매출', 'statement': 'DS 매출은 127.5조원입니다.',
        'status': 'SUPPORTED', 'kind': 'fact', 'value': '127.5', 'unit': '조원', 'period': '2026 Q2',
        'sources': [{'url': 'https://example.org/earnings', 'title': '실적발표',
                     'published_date': '2026-07-30', 'source_type': 'official',
                     'excerpt': 'DS 매출은 127.5조원입니다.', 'opened': True,
                     'explicit_non_disclosure': False}],
    }
    value.update(updates)
    return value


def packet(claims=None, **updates):
    return quality.ResearchPacket.model_validate({
        'as_of': '2026-09-23', 'window_start': None,
        'claims': claims or [claim()], **updates,
    })


def reviewed(items):
    return quality.ResearchReview(claims=[
        {'id': c['id'], 'action': 'replace', 'replacement': c} for c in items])


def checked(item, **window):
    return quality.checked_claims(packet(**window), reviewed([item]))[0]


def test_supported_number_in_korean_excerpt_is_retained():
    result = checked(claim())
    assert result.status == 'SUPPORTED'
    assert result.value == 127.5
    assert '127.5조원' in quality.render(packet(), [result])


@pytest.mark.parametrize('change', ['missing_source', 'not_opened', 'future', 'wrong_number', 'no_unit', 'no_period', 'nonfinite'])
def test_incomplete_numeric_evidence_never_reaches_parent_as_supported(change):
    c = claim()
    if change == 'missing_source': c['sources'] = []
    elif change == 'not_opened': c['sources'][0]['opened'] = False
    elif change == 'future': c['sources'][0]['published_date'] = '2026-09-24'
    elif change == 'wrong_number': c['value'] = '120.8'
    elif change == 'no_unit': c['unit'] = None
    elif change == 'no_period': c['period'] = None
    elif change == 'nonfinite': c['value'] = 'NaN'
    result = checked(c)
    assert result.status == 'UNVERIFIED'
    assert c['statement'] not in quality.render(packet(), [result])


@pytest.mark.parametrize('source_type,explicit,expected', [
    ('official', True, 'NOT_DISCLOSED'), ('official', False, 'NOT_FOUND'),
    ('media', True, 'NOT_FOUND'),
])
def test_non_disclosure_requires_explicit_official_evidence(source_type, explicit, expected):
    c = claim(status='NOT_DISCLOSED', statement='메모리 매출은 비공개입니다.', value=None)
    c['sources'][0].update(source_type=source_type, explicit_non_disclosure=explicit,
                           excerpt='해당 정보를 별도로 공개하지 않습니다.')
    result = checked(c)
    assert result.status == expected
    if expected == 'NOT_FOUND': assert c['statement'] not in quality.render(packet(), [result])


def test_requested_window_excludes_old_or_undated_news():
    for when in ['2025-09-22', '2026-09-16', None]:
        c = claim(); c['sources'][0]['published_date'] = when
        assert checked(c, window_start='2026-09-17').status == 'UNVERIFIED'
    c['sources'][0]['published_date'] = '2026-09-17'
    assert checked(c, window_start='2026-09-17').status == 'SUPPORTED'


def test_forecast_remains_forecast_and_source_excerpt_is_not_republished():
    c = claim(kind='forecast');result = checked(c)
    rendered = quality.render(packet(), [result])
    assert '[SUPPORTED / 전망]' in rendered
    assert 'https://example.org/earnings' in rendered
    assert rendered.count('DS 매출은 127.5조원입니다.') == 1


@pytest.mark.parametrize('claims', [[], [claim(), claim()], [claim(id='different')]])
def test_review_cannot_drop_duplicate_or_replace_requested_items(claims):
    with pytest.raises((ValidationError, ValueError)):
        quality.checked_claims(packet(), reviewed(claims))


@pytest.mark.asyncio
async def test_complete_pipeline_reviews_missing_information_and_renders_correction(monkeypatch):
    draft = packet([claim(status='NOT_FOUND', statement='not found', sources=[])])
    review = reviewed([claim()])
    runner = AsyncMock(side_effect=[draft.model_dump_json(), review.model_dump_json()])
    result = await quality.research(runner, 'Investigate', {'query': 'DS 매출'})
    assert runner.await_count == 2
    assert runner.call_args_list[1].kwargs['response_model'] is quality.ResearchReview
    assert runner.call_args_list[1].args[2]['candidate_evidence']['claims'][0]['status'] == 'NOT_FOUND'
    assert '127.5조원' in result and 'SUPPORTED' in result


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [RuntimeError('transport'), ValueError('invalid JSON')])
async def test_review_failure_does_not_release_draft(failure):
    runner = AsyncMock(side_effect=[packet().model_dump_json(), failure])
    result = await quality.research(runner, 'Investigate', 'Query')
    assert 'UNVERIFIED' in result
    assert '127.5' not in result and 'https://' not in result


@pytest.mark.asyncio
async def test_cancellation_propagates_from_verifier():
    runner = AsyncMock(side_effect=[packet().model_dump_json(), asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await quality.research(runner, '', 'query')


@pytest.mark.asyncio
async def test_real_stage_routing_performs_two_sequential_web_calls(monkeypatch):
    invoke = AsyncMock(side_effect=[
        {'answer': packet().model_dump_json(), 'calls': []},
        {'answer': reviewed([claim()]).model_dump_json(), 'calls': []},
    ])
    monkeypatch.setattr(sub, '_invoke', invoke)
    result = await sub.run_stage('research', 'Original', {'query': 'DS 매출'}, web_search=True)
    assert '127.5조원' in result
    assert invoke.await_count == 2
    assert all(call.kwargs['web_search'] for call in invoke.call_args_list)
    assert all(call.args[0].model == 'gpt-5.6-sol' for call in invoke.call_args_list)


@pytest.mark.asyncio
async def test_existing_non_research_stages_do_not_get_extra_calls(monkeypatch):
    invoke = AsyncMock(return_value={'answer': 'Unchanged answer', 'calls': []})
    monkeypatch.setattr(sub, '_invoke', invoke)
    assert await sub.run_stage('news', 'Instruction', 'Input') == 'Unchanged answer'
    invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_collection_and_review_share_one_deadline(monkeypatch):
    from dataclasses import replace
    real_choice = sub.settings('research')
    monkeypatch.setattr(sub, 'settings', lambda *a, **k: replace(real_choice, timeout_seconds=0.03))
    calls = []
    async def runner(*args, **kwargs):
        calls.append(kwargs['response_model'])
        if len(calls) == 1:
            await asyncio.sleep(0.01)
            return packet().model_dump_json()
        await asyncio.sleep(1)
        raise AssertionError('review should be cancelled')
    monkeypatch.setattr(sub, '_run_stage', runner)
    with pytest.raises(asyncio.TimeoutError):
        await sub.run_stage('research', '', 'query', web_search=True)
    assert len(calls) == 2


def test_credential_bearing_url_is_not_rendered_as_verified_source():
    c = claim();c['sources'][0]['url'] = 'https://name:secret@example.org/report'
    result = checked(c)
    assert result.status == 'UNVERIFIED'
    assert 'secret' not in quality.render(packet(), [result])


@pytest.mark.parametrize('value', ['2026-09-16', 'increase', '120-130'])
def test_non_numeric_metadata_cannot_be_used_as_a_numeric_value(value):
    with pytest.raises(ValidationError):
        quality.Claim.model_validate(claim(value=value))


@pytest.mark.parametrize('value,excerpt,expected', [
    (3.75, 'target range of 3-3/4 to 4 percent', 'SUPPORTED'),
    (4, 'target range of 3-3/4 percent', 'UNVERIFIED'),
    (0.25, '1/4 percentage point increase', 'SUPPORTED'),
    (25, '1/4 percentage point increase', 'UNVERIFIED'),
    (-0.25, '-1/4 percentage point', 'SUPPORTED'),
    (0.25, '-1/4 percentage point', 'UNVERIFIED'),
    (3, '1/4 percentage point increase', 'UNVERIFIED'),
])
def test_source_fractions_are_compared_as_whole_values(value, excerpt, expected):
    c = claim(value=value)
    c['sources'][0]['excerpt'] = excerpt
    assert checked(c).status == expected


def test_dates_and_directions_are_reviewed_without_numeric_metadata():
    c = claim(value=None, unit=None, statement='2026년 9월 16일 인상했습니다.')
    c['sources'][0]['excerpt'] = 'September 16, 2026. The Committee decided to raise the target range.'
    result = checked(c)
    assert result.status == 'SUPPORTED'
    assert '원문 수치' not in quality.render(packet(), [result])


def test_compact_accept_keeps_evidence_but_still_runs_numeric_checks():
    review = quality.ResearchReview(claims=[{'id': 'ds_revenue', 'action': 'accept'}])
    assert quality.checked_claims(packet(), review)[0].status == 'SUPPORTED'
    assert quality.checked_claims(packet([claim(value=999)]), review)[0].status == 'UNVERIFIED'


def test_compact_unverified_suppresses_draft():
    review = quality.ResearchReview(claims=[{'id': 'ds_revenue', 'action': 'unverified'}])
    result = quality.render(packet(), quality.checked_claims(packet(), review))
    assert '127.5' not in result and 'https://' not in result


@pytest.mark.parametrize('action,replacement', [
    ('replace', None), ('replace', claim(id='wrong')), ('accept', claim()),
])
def test_malformed_compact_verdict_is_rejected(action, replacement):
    review = quality.ResearchReview(claims=[{
        'id': 'ds_revenue', 'action': action, 'replacement': replacement}])
    with pytest.raises(ValueError):
        quality.checked_claims(packet(), review)
