"""Subscription model routing and fail-closed inference, without live trades."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel
from prism_core.ai_models import CONFIG, settings
from prism_core import codex_subscription as sub


def test_all_33_choices_and_video_variant():
    data = json.loads(CONFIG.read_text())
    assert sorted(x['number'] for x in data['stages'].values()) == list(range(1,34))
    for key in data['stages']:
        assert settings(key).key == key
    for key in ('strategy','kr_buy','us_buy','kr_sell','us_sell'):
        assert (settings(key).model,settings(key).effort)==('gpt-6-astra','xhigh')
    assert settings('journal').model=='gpt-5.6-sol'
    assert settings('telegram_summary').model=='gpt-5.6-terra'
    assert settings('telegram_evaluator').model=='gpt-5.6-sol'
    assert settings('video',variant='filter').model=='gpt-5.6-terra'
    assert not settings('embedding').enabled


@pytest.mark.parametrize('field,value',[('model','gpt-unknown'),('effort','none'),
    ('enabled','false'),('timeout_seconds',0),('max_tool_rounds',0)])
def test_invalid_configuration_rejected(tmp_path,field,value):
    data=json.loads(CONFIG.read_text());data['stages']['journal'][field]=value
    file=tmp_path/'models.json';file.write_text(json.dumps(data))
    with pytest.raises(ValueError):settings('journal',path=file)


def test_api_provider_and_embedding_cannot_be_enabled(tmp_path):
    data=json.loads(CONFIG.read_text());data['provider']='api_key'
    file=tmp_path/'models.json';file.write_text(json.dumps(data))
    with pytest.raises(ValueError):settings('journal',path=file)
    data['provider']='codex_subscription';data['stages']['embedding'].update(
        enabled=True,model='gpt-5.6-sol',effort='high')
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError):settings('embedding',path=file)


def test_command_preserves_subscription_and_model_choice(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','must-not-leak')
    monkeypatch.setenv('OPENAI_BASE_URL','https://paid-api.invalid')
    monkeypatch.setenv('ANTHROPIC_API_KEY','also-excluded')
    env=sub._environment()
    assert not any(k.startswith(('OPENAI_','ANTHROPIC_')) for k in env)
    cmd=sub._command('codex',settings('kr_sell'),'out','schema')
    assert cmd[cmd.index('--model')+1]=='gpt-6-astra'
    assert 'model_reasoning_effort="xhigh"' in cmd
    assert 'forced_login_method="chatgpt"' in cmd
    assert '--ignore-user-config' in cmd
    assert 'web_search="disabled"' in cmd
    assert '--output-schema' in cmd
    assert 'fast' not in cmd


class Tools:
    def __init__(self):self.calls=[]
    async def list_tools(self):
        return [SimpleNamespace(name=n,description=n,inputSchema={'type':'object'}) for n in
                ['sqlite-read_query','sqlite-write_query','firecrawl-firecrawl_agent','time-get_current_time']]
    async def call_tool(self,name,args):
        self.calls.append((name,args));return {'rows':[{'cost':110}]}


@pytest.mark.asyncio
async def test_tool_round_trip_preserves_inputs_and_uses_selected_model(monkeypatch):
    prompts=[]
    async def invoke(choice,prompt,**kwargs):
        prompts.append((choice,prompt))
        if len(prompts)==1:
            return {'answer':'','calls':[{'name':'sqlite-read_query','arguments':'{"query":"SELECT 110"}'}]}
        return {'answer':'10%','calls':[]}
    monkeypatch.setattr(sub,'_invoke',invoke)
    tools=Tools()
    assert await sub.run_stage('kr_sell','Original instructions','Price 121',provider=tools)=='10%'
    assert prompts[0][0].model=='gpt-6-astra'
    assert len(tools.calls)==1
    assert '"cost": 110' in prompts[1][1]
    assert 'Original instructions' in prompts[0][1]
    assert 'sqlite-write_query' not in prompts[0][1]
    assert 'firecrawl-firecrawl_agent' not in prompts[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize('name',['sqlite-write_query','orders-place_order','invented'])
async def test_unknown_or_mutating_tool_is_never_executed(monkeypatch,name):
    monkeypatch.setattr(sub,'_invoke',AsyncMock(return_value={'answer':'','calls':[{'name':name,'arguments':'{}'}]}))
    tools=Tools()
    with pytest.raises(ValueError):await sub.run_stage('us_buy','prompt','input',provider=tools)
    assert not tools.calls


@pytest.mark.asyncio
async def test_subscription_failure_is_propagated_without_api_fallback(monkeypatch):
    fake=AsyncMock(side_effect=RuntimeError('quota exhausted'))
    monkeypatch.setattr(sub,'_invoke',fake)
    with pytest.raises(RuntimeError,match='quota exhausted'):
        await sub.run_stage('translation','Translate','Hello')
    assert fake.await_count==1


@pytest.mark.asyncio
async def test_round_limit_never_returns_unfinished_answer(monkeypatch,tmp_path):
    data=json.loads(CONFIG.read_text());data['stages']['macro']['max_tool_rounds']=1
    file=tmp_path/'config.json';file.write_text(json.dumps(data));monkeypatch.setenv('PRISM_AI_CONFIG',str(file))
    monkeypatch.setattr(sub,'_invoke',AsyncMock(return_value={'answer':'partial','calls':[{'name':'time-get_current_time','arguments':'{}'}]}))
    with pytest.raises(RuntimeError,match='round limit'):
        await sub.run_stage('macro','Prompt','Input',provider=Tools())


@pytest.mark.asyncio
async def test_structured_evaluation_validated(monkeypatch):
    class Verdict(BaseModel):rating:int
    monkeypatch.setattr(sub,'_invoke',AsyncMock(return_value={'answer':'{"rating":3}','calls':[]}))
    assert await sub.run_stage('telegram_evaluator','Judge','Draft',response_model=Verdict)=='{"rating":3}'
    monkeypatch.setattr(sub,'_invoke',AsyncMock(return_value={'answer':'not JSON','calls':[]}))
    with pytest.raises(ValueError):await sub.run_stage('telegram_evaluator','Judge','Draft',response_model=Verdict)


@pytest.mark.asyncio
async def test_external_research_does_not_call_paid_mcp(monkeypatch):
    tools=Tools()
    tools.list_tools=AsyncMock(return_value=[SimpleNamespace(name='perplexity-ask',description='search',inputSchema={'type':'object'})])
    seen=[]
    async def invoke(choice,prompt,**kwargs):
        seen.append((choice.key,kwargs.get('web_search')))
        if len(seen)==1:return {'answer':'','calls':[{'name':'perplexity-ask','arguments':'{"query":"news"}'}]}
        return {'answer':'Cited result','calls':[]}
    monkeypatch.setattr(sub,'_invoke',invoke)
    assert await sub.run_stage('news','Research','Question',provider=tools)=='Cited result'
    assert ('research',True) in seen
    assert tools.calls==[]


@pytest.mark.asyncio
async def test_macro_recovers_misrouted_native_search_without_executing_it(monkeypatch):
    tools=Tools()
    tools.list_tools=AsyncMock(return_value=[SimpleNamespace(
        name='perplexity-ask',description='search',inputSchema={'type':'object'})])
    seen=[]
    async def invoke(choice,prompt,**kwargs):
        seen.append((choice.key,prompt,kwargs))
        if len(seen)==1:
            return {'answer':'','calls':[{'name':'perplexity-ask','arguments':'{"query":"KR macro"}'}]}
        if len(seen)==2:
            return {'answer':'unfinished','calls':[{'name':'web__run','arguments':'{"query":"news"}'}]}
        if len(seen)==3:
            assert 'were not executed' in prompt
            assert 'other native CLI tools' not in prompt
            assert kwargs['web_search'] is True
            return {'answer':'Verified news: https://example.org/news','calls':[]}
        assert 'Verified news' in prompt
        return {'answer':'Macro report','calls':[]}
    monkeypatch.setattr(sub,'_invoke',invoke)
    assert await sub.run_stage('macro','Analyze macro','KR',provider=tools)=='Macro report'
    assert [stage for stage,_,_ in seen]==['macro','research','research','macro']
    assert not tools.calls


@pytest.mark.asyncio
async def test_native_search_correction_is_bounded(monkeypatch):
    fake=AsyncMock(return_value={'answer':'unfinished','calls':[{'name':'web__run','arguments':'{}'}]})
    monkeypatch.setattr(sub,'_invoke',fake)
    with pytest.raises(RuntimeError,match='after corrections'):
        await sub.run_stage('research','Search','Query',web_search=True)
    assert fake.await_count==3


@pytest.mark.asyncio
@pytest.mark.parametrize('calls,web_search',[
    ([{'name':'web__run','arguments':'{}'}],False),
    ([{'name':'orders-place_order','arguments':'{}'}],True),
    ([{'name':'web__run','arguments':'{}'},{'name':'shell','arguments':'{}'}],True),
])
async def test_native_search_recovery_does_not_expand_tool_permissions(monkeypatch,calls,web_search):
    fake=AsyncMock(return_value={'answer':'','calls':calls})
    monkeypatch.setattr(sub,'_invoke',fake)
    with pytest.raises(ValueError,match='Unknown or prohibited'):
        await sub.run_stage('research','Search','Query',web_search=web_search)
    assert fake.await_count==1


@pytest.mark.asyncio
async def test_native_search_schema_requires_empty_calls(monkeypatch):
    async def start(*command,**kwargs):
        schema=json.loads(Path(command[command.index('--output-schema')+1]).read_text())
        assert schema['properties']['calls']['maxItems']==0
        assert 'web_search="live"' in command
        Path(command[command.index('--output-last-message')+1]).write_text(
            '{"answer":"Sourced answer","calls":[]}')
        return SimpleNamespace(returncode=0,communicate=AsyncMock(return_value=(None,b'')))
    monkeypatch.setattr(sub,'_binary',lambda:'codex')
    monkeypatch.setattr(asyncio,'create_subprocess_exec',start)
    assert (await sub._invoke(settings('research'),'Search',web_search=True))['answer']=='Sourced answer'


@pytest.mark.asyncio
async def test_report_routing_all_sections_and_strategy(monkeypatch):
    import cores.report_generation as reports
    import prism_core.codex_subscription as transport
    calls=[]
    async def fake(stage,instruction,message,names=(),**kwargs):calls.append(stage);return 'report'
    monkeypatch.setattr(transport,'run_with_registry',fake)
    agent=SimpleNamespace(name='test',instruction='instructions',server_names=[])
    for stage in ['price_volume','holdings_flow','financials','company','news','market','strategy','report_summary']:
        assert await reports._generate_agent_text(agent,'input',stage=stage,max_tokens=100,max_iterations=1)=='report'
    assert len(calls)==8


@pytest.mark.asyncio
async def test_legacy_chat_uses_its_stage_not_obsolete_model_kwarg(monkeypatch):
    fake=AsyncMock(return_value='answer');monkeypatch.setattr(sub,'run_stage',fake)
    client=sub.SubscriptionChatClient('archive_insight')
    result=await client.chat.completions.create(model='obsolete',messages=[{'role':'user','content':'text'}])
    assert result.choices[0].message.content=='answer'
    assert fake.call_args.args[0]=='archive_insight'


def test_subscription_factories_separate_evaluator_and_video():
    from cores.llm.subscription_llm import llm_for
    assert llm_for('telegram_summary').stage=='telegram_summary'
    assert llm_for('telegram_evaluator').stage=='telegram_evaluator'
    assert llm_for('video',variant='filter').variant=='filter'


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,decision',[('kr_buy',True),('us_buy',False),('kr_sell',True),('us_sell',False)])
async def test_decision_adapter_preserves_accept_and_reject(monkeypatch,stage,decision):
    class Decision(BaseModel):allowed:bool; reason:str
    answer=json.dumps({'allowed':decision,'reason':'fixed evidence'})
    fake=AsyncMock(return_value={'answer':answer,'calls':[]});monkeypatch.setattr(sub,'_invoke',fake)
    result=await sub.run_stage(stage,'Original decision instructions','Fixed counterexample',response_model=Decision)
    assert Decision.model_validate_json(result).allowed is decision
    assert 'Original decision instructions' in fake.call_args.args[1]
    assert fake.call_args.args[0].model=='gpt-6-astra'


@pytest.mark.asyncio
async def test_summary_workflow_keeps_independent_stage_choices(monkeypatch,tmp_path):
    from mcp_agent.app import MCPApp
    from mcp_agent.config import Settings
    from mcp_agent.agents.agent import Agent
    from mcp_agent.workflows.evaluator_optimizer.evaluator_optimizer import EvaluatorOptimizerLLM,QualityRating
    import cores.llm.subscription_llm as adapter
    stages=[]
    async def run(stage,instruction,message,*args,response_model=None,**kwargs):
        stages.append(stage)
        if response_model:
            return json.dumps({'rating':3,'feedback':'accurate','needs_improvement':False,'focus_areas':[]})
        return 'Summary with the original facts'
    monkeypatch.setattr(adapter,'run_with_registry',run)
    cfg=Settings(mcp={'servers':{}},logger={'transports':['console'],'level':'error'},otel={'enabled':False})
    app=MCPApp(name='subscription-test',settings=cfg)
    async with app.run():
        optimizer=Agent(name='optimizer',instruction='summarize',server_names=[])
        evaluator=Agent(name='evaluator',instruction='evaluate',server_names=[])
        workflow=EvaluatorOptimizerLLM(optimizer=optimizer,evaluator=evaluator,
            llm_factory=adapter.summary_factory(optimizer,evaluator),min_rating=QualityRating.EXCELLENT)
        result=await workflow.generate_str('report')
        assert 'Summary with the original facts' in result
    assert stages==['telegram_summary','telegram_evaluator']


@pytest.mark.asyncio
async def test_registry_respects_read_only_and_tool_filter(monkeypatch):
    from cores.llm.mcp_registry import McpServerRegistry,McpServerSpec
    from cores.llm.backends import openai_agents_backend as backend
    from mcp.types import Tool
    class Server:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def list_tools(self):
            return [Tool(name=n,inputSchema={'type':'object'}) for n in ['read_query','write_query','list_tables']]
    captured=[]
    def build(name,registry):captured.append(registry.get(name));return Server()
    monkeypatch.setattr(backend,'build_mcp_server',build)
    registry=McpServerRegistry({'sqlite':McpServerSpec(name='sqlite',command='uv')})
    async with sub.RegistryTools(registry,['sqlite'],{'sqlite':{'read_query','write_query'}}) as provider:
        assert [t.name for t in await provider.list_tools()]==['sqlite-read_query']
    assert captured[0].cwd==str(sub.ROOT)


@pytest.mark.asyncio
async def test_image_and_embedding_paths_do_not_require_api_key(monkeypatch):
    from cores.llm import capabilities
    monkeypatch.setenv('PRISM_FEATURE_VISION','on')
    monkeypatch.setattr(capabilities,'has_api_key',lambda:False)
    assert capabilities.vision_available()
    assert capabilities.vision_auth()=='codex_subscription'
    from cores.archive.embedding import embed_text
    assert await embed_text('some text','must-not-be-used') is None


def test_archive_cache_changes_with_stage_model_and_effort(monkeypatch, tmp_path):
    from cores.archive.query_engine import _query_hash
    data = json.loads(CONFIG.read_text())
    path = tmp_path / 'models.json'
    path.write_text(json.dumps(data))
    monkeypatch.setenv('PRISM_AI_CONFIG', str(path))
    first = _query_hash('question', 'kr', None, None, None)
    assert first == _query_hash('question', 'kr', None, None, None)
    data['stages']['archive_query']['effort'] = 'xhigh'
    path.write_text(json.dumps(data))
    second = _query_hash('question', 'kr', None, None, None)
    data['stages']['archive_query']['model'] = 'gpt-6-astra'
    path.write_text(json.dumps(data))
    third = _query_hash('question', 'kr', None, None, None)
    assert len({first, second, third}) == 3


@pytest.mark.asyncio
async def test_timeout_terminates_codex_child(monkeypatch):
    from dataclasses import replace
    process = SimpleNamespace(returncode=None, pid=987654, wait=AsyncMock())
    async def communicate(_):
        await asyncio.Event().wait()
    process.communicate = communicate
    monkeypatch.setattr(sub, '_binary', lambda: 'codex')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    killed = []
    monkeypatch.setattr(sub.os, 'killpg', lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(TimeoutError):
        await sub._invoke(replace(settings('journal'), timeout_seconds=0.01), 'prompt')
    assert killed == [(987654, sub.signal.SIGTERM)]
    process.wait.assert_awaited_once()
