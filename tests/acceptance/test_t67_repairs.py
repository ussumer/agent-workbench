from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import asyncio,json,time
import pytest
from scripts.planning import gdpevo_v4_taskset as runtime


def test_actual_fewshot_wrapper_extracts_only_original_json():
    content=(runtime.ROOT/'tests/fixtures/t67_cont1_fewshot_response.txt').read_text(encoding='utf-8')
    answer=runtime.parse_json_answer(content)
    assert answer['program_id']=='PRG-084'
    assert answer['financial_exposure']['candidates'][1]['total_npv_minor']==3599636734

@pytest.mark.parametrize('content',[
    'Intro\n```json\n{"answer":42}\n```\nDone.',
    '```\n{"answer":42}\n```',
    '{"answer":42}',
])
def test_submission_accepts_one_complete_object(content):
    assert runtime.parse_json_answer(content)=={'answer':42}

@pytest.mark.parametrize('content',[
    'Intro\n```json\n{"answer":42}\n```\nOther\n```json\n{"answer":43}\n```',
    'Intro\n```json\n{"answer":42, "nested":{"value":1}}',
    'Intro\n```json\n{"outer": {"answer":42}\n```',
    'Intro\n```json\nnot json {"answer":42} trailing junk\n```',
    '```json\n[ {"answer":42} ]\n```',
    'Tool call limit reached: run limit exceeded (38/36 calls).',
    '{"answer":41}\n```json\n{"answer":42}\n```',
    '```json\n{"answer":42}\n```\n```python\n{"answer":43}\n```',
])
def test_submission_rejects_ambiguous_or_unfinished_content(content):
    with pytest.raises(ValueError):runtime.parse_json_answer(content)


def test_concurrent_long_callbacks_are_complete_lines(tmp_path,monkeypatch):
    path=tmp_path/'trace.jsonl';original=Path.open
    class SplitWriter:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):return self
        def __exit__(self,*args):self.stream.close()
        def write(self,text):
            middle=len(text)//2;self.stream.write(text[:middle]);self.stream.flush()
            time.sleep(.003);self.stream.write(text[middle:]);self.stream.flush()
    def controlled_open(p,mode='r',*args,**kwargs):
        stream=original(p,mode,*args,**kwargs)
        return SplitWriter(stream) if p==path and mode=='a' else stream
    monkeypatch.setattr(Path,'open',controlled_open)
    recorder=runtime.recorder_class()(path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n:recorder.save('tool_end',{'id':n,'content':'x'*100000}),range(16)))
    rows=runtime.jsonl(path)
    assert len(rows)==16 and {r['value']['id'] for r in rows}==set(range(16))
    assert all(len(r['value']['content'])==100000 for r in rows)


def test_failed_sourcing_request_is_recorded_and_still_raises(monkeypatch):
    from mcp.server.fastmcp import FastMCP
    server=FastMCP('failed-read');records=[]
    def failed(*args):raise ValueError('real Java HTTP failed status 404')
    monkeypatch.setattr(runtime,'business_read',failed)
    runtime.register_sourcing_read(server,{},lambda *args:records.append(args))
    with pytest.raises(Exception,match='404'):
        asyncio.run(server.call_tool('sourcing_read',{'path':'/api/sourcing/documents/missing','params':{}}))
    assert records and records[0][0]=='sourcing_read'
    assert records[0][1]=={'path':'/api/sourcing/documents/missing','params':{}}
    assert records[0][2]['status']=='failed'
    assert '404' in records[0][2]['error']['message']


def tool_history(rounds,*,unique=False,failed=False):
    rows=[]
    for n in range(rounds):
        args={'path':f'/api/sourcing/programs/PRG-{n if unique else 21:03d}'}
        rows.append({'type':'ai','tool_calls':[{'id':str(n),'name':'sourcing_read','args':args}]})
        response={'data':{'record':n if unique else 21},'request_id':str(n)}
        rows.append({'type':'tool','tool_call_id':str(n),'status':'error' if failed else 'success','content':json.dumps(response)})
    return rows


def test_budget_allows_full_discovery_in_both_phases():
    limits=runtime.session_limits('solve')
    assert limits['tool_calls']>=120 and limits['model_calls']>=60
    assert runtime.session_limits('skill')==limits


def test_no_progress_detects_repeated_results_but_allows_new_documents():
    assert runtime.no_progress_reason(tool_history(10)) is not None
    assert runtime.no_progress_reason(tool_history(50,unique=True)) is None
    assert runtime.no_progress_reason(tool_history(9,failed=True)) is not None
    assert runtime.no_progress_reason(tool_history(4)+tool_history(1,unique=True)+tool_history(3)) is None


def test_real_tool_middleware_allows_forty_calls_but_stops_oversized_batch(tmp_path):
    from langchain_core.messages import AIMessage
    middleware=runtime.actor_middlewares('solve',tmp_path)
    tools=next(m for m in middleware if m.__class__.__name__=='ToolCallLimitMiddleware')
    message=AIMessage(content='',tool_calls=[{'id':str(i),'name':'sourcing_read','args':{'path':f'/api/sourcing/programs/PRG-{i:03d}'}} for i in range(40)])
    assert tools.after_model({'messages':[message]},None).get('jump_to') is None
    oversized=AIMessage(content='',tool_calls=[{'id':str(i),'name':'sourcing_read','args':{}} for i in range(121)])
    assert tools.after_model({'messages':[oversized]},None)['jump_to']=='end'


def test_loop_middleware_ends_before_another_model_call_and_records_reason(tmp_path):
    middleware=runtime.actor_middlewares('solve',tmp_path)[-1]
    result=asyncio.run(middleware.abefore_model({'messages':tool_history(10)},None))
    assert result['jump_to']=='end'
    assert runtime.read(tmp_path/'termination.json')['kind']=='no_progress'
    assert asyncio.run(middleware.abefore_model({'messages':tool_history(50,unique=True)},None)) is None


@pytest.mark.parametrize('exit_failure',[False,True])
@pytest.mark.parametrize('content',['unfinished answer','Model call limits exceeded: run limit (60/60)'])
@pytest.mark.parametrize('failure_stage',['output','load','cleanup'])
def test_postprocessing_failure_does_not_misreport_real_session_exit(tmp_path,monkeypatch,exit_failure,content,failure_stage):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from langchain_core.messages import AIMessage
    from langgraph.store.memory import InMemoryStore
    import deepagents,api_view.web_config,langchain_mcp_adapters.tools
    config=tmp_path/'config.json';runtime.write(config,{'mongo_uri':'mongodb://unit','database':'unit'})
    resources=SimpleNamespace(store=InMemoryStore(),checkpointer=None,close=lambda:None)
    resources.start=lambda:resources
    monkeypatch.setattr(api_view.web_config,'MongoResources',lambda settings:resources)
    class Graph:
        async def astream(self,*args,**kwargs):yield {'messages':[AIMessage(content=content)]}
    monkeypatch.setattr(deepagents,'create_deep_agent',lambda **kwargs:Graph())
    names=['sourcing_read','computation_execute','computation_status','finish_session']
    async def tools(session):
        if failure_stage=='load':raise ValueError('capability loading failed')
        return [SimpleNamespace(name=n) for n in names]
    monkeypatch.setattr(langchain_mcp_adapters.tools,'load_mcp_tools',tools)
    class Client:
        @asynccontextmanager
        async def session(self,name):
            yield self
            if exit_failure:raise RuntimeError('stdio context failed to close')
        async def call_tool(self,name,args):
            if failure_stage=='cleanup':raise ValueError('cleanup failed')
            return SimpleNamespace(isError=False,structuredContent={'recycled':True,'container_absent':True})
    monkeypatch.setattr(runtime,'mcp_client',lambda *args:Client())
    folder=tmp_path/'actor'
    with pytest.raises((ValueError,RuntimeError)):
        asyncio.run(runtime.actor_session(None,config,folder,[],None,'private'))
    assert runtime.read(folder/'stdio-cleanup.json')['closed'] is (not exit_failure)
    if not exit_failure and failure_stage=='output' and content.startswith('Model call limits exceeded:'):
        assert runtime.read(folder/'termination.json')['kind']=='budget'


def test_computation_transport_ids_do_not_hide_repeated_no_state_results():
    rows=[]
    for n in range(10):
        rows.extend([{'type':'ai','tool_calls':[{'id':str(n),'name':'computation_execute','args':{'code':'print(42)'}}]},
            {'type':'tool','tool_call_id':str(n),'status':'success','content':json.dumps({'operation_id':str(n),'status':'completed','stdout':'42',
             'stderr':'','worker_pid':100+n,'terminated_pids':[100+n],'processes_stopped':True,'base_version':n,'version':n+1,'saved_names':[]})}])
    assert runtime.no_progress_reason(rows) is not None
    for row in rows:
        if row.get('type')=='tool':
            value=json.loads(row['content']);value['saved_names']=['iteration'];value['state_hashes']={'iteration':{'sha256':'constant','bytes':2}};row['content']=json.dumps(value)
    assert runtime.no_progress_reason(rows) is not None
    for n,row in enumerate(rows):
        if row.get('type')=='tool':
            value=json.loads(row['content']);value['state_hashes']['iteration']['sha256']=str(n);row['content']=json.dumps(value)
    assert runtime.no_progress_reason(rows) is None


def test_rejected_path_then_corrected_discovery_is_verifiable(tmp_path):
    from tests.acceptance.test_t67_runtime import mcp_mechanism_fixture
    folder,scope,records=mcp_mechanism_fixture(tmp_path)
    failed={'kind':'sourcing_read','request':{'path':'/programs','params':{}},'response':{'status':'failed','error':{'message':'domain path denied'}},**scope}
    records.append(failed)
    (folder/'mcp/tools.jsonl').write_text(''.join(json.dumps(c)+'\n' for c in records))
    with (folder/'raw-trace.jsonl').open('a') as stream:
        stream.write(json.dumps({'kind':'tool_start','value':{'name':'sourcing_read','inputs':failed['request']}})+'\n')
    assert runtime.verify_mcp_attempt(folder,scope)['completed_executions']==1
    failed['response']={'data':{'value':42}}
    (folder/'mcp/tools.jsonl').write_text(''.join(json.dumps(c)+'\n' for c in records))
    with pytest.raises(ValueError,match='path denied'):
        runtime.verify_mcp_attempt(folder,scope)


def test_real_graph_can_use_fifty_model_rounds_without_hidden_recursion_cap(tmp_path):
    from deepagents import create_deep_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage,HumanMessage
    from langchain_core.tools import tool
    seen=[]
    @tool
    def discover_record(number:int)->dict:
        """Unit-only domain record discovery; no business or external runtime evidence."""
        seen.append(number)
        return {'data':{'number':number}}
    outputs=[AIMessage(content='',tool_calls=[{'id':str(n),'name':'discover_record','args':{'number':n}}]) for n in range(50)]
    outputs.append(AIMessage(content='{"answer":42}'))
    class ToolBoundFake(GenericFakeChatModel):
        def bind_tools(self,tools,**kwargs):return self
    model=ToolBoundFake(messages=iter(outputs))
    graph=create_deep_agent(model=model,tools=[discover_record],middleware=runtime.actor_middlewares('solve',tmp_path))
    result=asyncio.run(graph.ainvoke({'messages':[HumanMessage(content='Unit budget characterization')]},
        config={'recursion_limit':runtime.session_limits('solve')['recursion_limit']}))
    assert seen==list(range(50))
    assert runtime.parse_json_answer(result['messages'][-1].content)=={'answer':42}


def test_skill_generation_has_the_same_no_progress_protection(tmp_path):
    middleware=runtime.actor_middlewares('skill',tmp_path)[-1]
    result=asyncio.run(middleware.abefore_model({'messages':tool_history(10)},None))
    assert result['jump_to']=='end'
    assert runtime.read(tmp_path/'termination.json')['kind']=='no_progress'


@pytest.mark.parametrize('cleanup_failure',[False,True])
def test_cancelled_graph_records_actual_context_exit_and_preserves_interrupt(tmp_path,monkeypatch,cleanup_failure):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from langgraph.store.memory import InMemoryStore
    import deepagents,api_view.web_config,langchain_mcp_adapters.tools
    config=tmp_path/'config.json';runtime.write(config,{'mongo_uri':'mongodb://unit','database':'unit'})
    resources=SimpleNamespace(store=InMemoryStore(),checkpointer=None,close=lambda:None)
    resources.start=lambda:resources
    monkeypatch.setattr(api_view.web_config,'MongoResources',lambda settings:resources)
    class Graph:
        async def astream(self,*args,**kwargs):
            raise asyncio.CancelledError('operator stop')
            yield
    monkeypatch.setattr(deepagents,'create_deep_agent',lambda **kwargs:Graph())
    async def tools(session):return [SimpleNamespace(name=n) for n in ['sourcing_read','computation_execute','computation_status','finish_session']]
    monkeypatch.setattr(langchain_mcp_adapters.tools,'load_mcp_tools',tools)
    class Client:
        @asynccontextmanager
        async def session(self,name):yield self
        async def call_tool(self,name,args):
            if cleanup_failure:raise ValueError('cleanup failed')
            return SimpleNamespace(isError=False,structuredContent={'recycled':True,'container_absent':True})
    monkeypatch.setattr(runtime,'mcp_client',lambda *args:Client())
    folder=tmp_path/'actor'
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.actor_session(None,config,folder,[],None,'private'))
    assert runtime.read(folder/'stdio-cleanup.json')['closed'] is True
    if cleanup_failure:assert runtime.read(folder/'actor-cleanup-error.json')['type']=='ValueError'


@pytest.mark.parametrize('stop_phase',['cleanup','context_exit'])
def test_operator_stop_wins_over_ordinary_actor_or_context_error(tmp_path,monkeypatch,stop_phase):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from langgraph.store.memory import InMemoryStore
    import deepagents,api_view.web_config,langchain_mcp_adapters.tools
    config=tmp_path/'config.json';runtime.write(config,{'mongo_uri':'mongodb://unit','database':'unit'})
    resources=SimpleNamespace(store=InMemoryStore(),checkpointer=None,close=lambda:None)
    resources.start=lambda:resources
    monkeypatch.setattr(api_view.web_config,'MongoResources',lambda settings:resources)
    class Graph:
        async def astream(self,*args,**kwargs):
            if stop_phase=='cleanup':raise ValueError('Actor failed before operator stop')
            raise asyncio.CancelledError('operator stop')
            yield
    monkeypatch.setattr(deepagents,'create_deep_agent',lambda **kwargs:Graph())
    async def tools(session):return [SimpleNamespace(name=n) for n in ['sourcing_read','computation_execute','computation_status','finish_session']]
    monkeypatch.setattr(langchain_mcp_adapters.tools,'load_mcp_tools',tools)
    class Client:
        @asynccontextmanager
        async def session(self,name):
            yield self
            if stop_phase=='context_exit':raise RuntimeError('stdio exit failed after stop')
        async def call_tool(self,name,args):
            if stop_phase=='cleanup':raise asyncio.CancelledError('operator stop during cleanup')
            return SimpleNamespace(isError=False,structuredContent={'recycled':True,'container_absent':True})
    monkeypatch.setattr(runtime,'mcp_client',lambda *args:Client())
    folder=tmp_path/'actor'
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.actor_session(None,config,folder,[],None,'private'))
    assert runtime.read(folder/'stdio-cleanup.json')['closed'] is (stop_phase=='cleanup')
    assert runtime.read(folder/('actor-error.json' if stop_phase=='cleanup' else 'context-exit-error.json'))['type'] in {'ValueError','RuntimeError'}
