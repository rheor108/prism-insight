"""Offline regressions for Oct 6 failures. No credentials or broker calls."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from prism_core import research_quality as q, claude_subscription as claude, codex_subscription as codex
from prism_core.ai_models import settings
from patches.krx.auth_diagnostics import navigate_auth_page


def packet(n):
    return {'as_of':'2026-10-06','window_start':None,'claims':[
        {'id':f'c{i}','requested_item':f'Item {i}','statement':'unverified fixture',
         'status':'UNVERIFIED','kind':'fact','sources':[]} for i in range(n)]}


@pytest.mark.asyncio
@pytest.mark.parametrize('n', [17,64])
async def test_all_claims_survive_bounded_sequential_review(n):
    batches=[]
    async def runner(stage, instruction, message, **kwargs):
        if kwargs['response_model'] is q.ResearchPacket:
            return q.ResearchPacket.model_validate(packet(n)).model_dump_json()
        claims=message['candidate_evidence']['claims'];batches.append(len(claims))
        return q.ResearchReview(claims=[q.ReviewedItem(id=c['id'],action='unverified') for c in claims]).model_dump_json()
    result=await q.research(runner,'Synthetic','Synthetic')
    assert sum(batches)==n and max(batches)<=16
    assert result.count('[UNVERIFIED /')==n
    assert f'Item {n-1} [' in result


def test_collection_remains_bounded():
    with pytest.raises(ValidationError):q.ResearchPacket.model_validate(packet(65))


@pytest.mark.asyncio
async def test_http403_rejected_without_networkidle():
    page=SimpleNamespace(goto=AsyncMock(return_value=SimpleNamespace(status=403)))
    with pytest.raises(RuntimeError,match='KRX_NAVIGATION_HTTP.*403'):
        await navigate_auth_page(page,'https://example.test',timeout=100)
    page.goto.assert_awaited_once_with('https://example.test',wait_until='domcontentloaded',timeout=100)


@pytest.mark.asyncio
async def test_navigation_ready_without_idle_and_no_response_fails():
    page=SimpleNamespace(goto=AsyncMock(return_value=SimpleNamespace(status=200)))
    assert (await navigate_auth_page(page,'https://example.test',timeout=100)).status==200
    page.goto.return_value=None
    with pytest.raises(RuntimeError,match='NO_RESPONSE'):
        await navigate_auth_page(page,'https://example.test',timeout=100)


@pytest.mark.asyncio
@pytest.mark.parametrize('recover',[True,False])
async def test_refresh_contention_retried_once_without_repeating_parent_tools(monkeypatch,recover):
    def proc(data):return SimpleNamespace(returncode=0,communicate=AsyncMock(return_value=(json.dumps(data).encode(),b'')))
    auth=proc({'loggedIn':True,'authMethod':'claude.ai'})
    busy=proc({'is_error':True,'result':'Failed to refresh OAuth token: another Claude Code process is refreshing it or exited mid-refresh.'})
    good=proc({'structured_output':{'answer':'ok','calls':[]},'modelUsage':{'claude-sonnet-5':{}}})
    spawn=AsyncMock(side_effect=[auth,busy,good if recover else busy])
    monkeypatch.setattr(claude.asyncio,'create_subprocess_exec',spawn)
    sleep=AsyncMock();monkeypatch.setattr(claude.asyncio,'sleep',sleep)
    monkeypatch.setattr(claude.shutil,'which',lambda _: '/bin/true')
    monkeypatch.setattr(codex,'_terminate',AsyncMock())
    monkeypatch.setattr(claude.metrics,'emit',lambda _:None)
    if recover:assert (await claude.invoke(settings('news'),'fixture'))['answer']=='ok'
    else:
        with pytest.raises(RuntimeError,match='claude_refresh_busy'):await claude.invoke(settings('news'),'fixture')
    sleep.assert_awaited_once_with(60)
    assert spawn.await_count==3


def test_auth_expiration_not_misclassified_as_transient_lock():
    assert claude.classify_failure(b'{"is_error":true,"result":"OAuth token expired"}',b'').code=='claude_authentication'


@pytest.mark.asyncio
async def test_failed_review_chunk_does_not_drop_remaining_items():
    batches=[]
    async def runner(stage, instruction, message, **kwargs):
        if kwargs['response_model'] is q.ResearchPacket:
            return q.ResearchPacket.model_validate(packet(17)).model_dump_json()
        claims=message['candidate_evidence']['claims'];batches.append(len(claims))
        if len(batches)==1:raise ValueError('invalid review')
        return q.ResearchReview(claims=[q.ReviewedItem(id=c['id'],action='unverified') for c in claims]).model_dump_json()
    result=await q.research(runner,'Synthetic','Synthetic')
    assert batches==[16,1]
    assert result.count('[UNVERIFIED /')==17


def test_structured_tool_null_is_valid_but_never_a_final_answer():
    from pydantic import BaseModel
    from jsonschema import ValidationError as SchemaError
    class Verdict(BaseModel):
        rating: int
    schema=claude.output_schema(Verdict.model_json_schema())
    tool={'answer':None,'calls':[{'name':'read','arguments':'{}'}]}
    claude.validate_envelope(tool,schema)
    for invalid in [{'answer':None,'calls':[]}, {'answer':'','calls':tool['calls']},
                    {'answer':{'rating':1},'calls':tool['calls']}]:
        with pytest.raises(SchemaError):claude.validate_envelope(invalid,schema)


@pytest.mark.asyncio
async def test_null_tool_transport_preserves_parent_loop(monkeypatch):
    from pydantic import BaseModel
    class Verdict(BaseModel):
        rating: int
    def proc(data):return SimpleNamespace(returncode=0,communicate=AsyncMock(return_value=(json.dumps(data).encode(),b'')))
    auth=proc({'loggedIn':True,'authMethod':'claude.ai'})
    def response(answer,calls):return proc({'structured_output':{'answer':answer,'calls':calls},'modelUsage':{'claude-sonnet-5':{}}})
    spawn=AsyncMock(side_effect=[auth,response(None,[{'name':'read_fixture','arguments':'{}'}]),auth,response({'rating':1},[])])
    monkeypatch.setattr(claude.asyncio,'create_subprocess_exec',spawn)
    monkeypatch.setattr(claude.shutil,'which',lambda _: '/bin/true')
    monkeypatch.setattr(codex,'_terminate',AsyncMock())
    monkeypatch.setattr(claude.metrics,'emit',lambda _:None)
    provider=SimpleNamespace(list_tools=AsyncMock(return_value=[SimpleNamespace(name='read_fixture',description='synthetic',inputSchema={'type':'object'})]),call_tool=AsyncMock(return_value={'fixture':True}))
    result=await codex.run_stage('telegram_evaluator','Judge','Synthetic',provider=provider,response_model=Verdict)
    assert json.loads(result)=={'rating':1}
    provider.call_tool.assert_awaited_once_with('read_fixture',{})
