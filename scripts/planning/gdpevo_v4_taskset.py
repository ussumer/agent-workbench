"""DS-adapted construction calibration. Real MCP, Mongo and OpenSandbox only.

prepare/doctor/verify never call the model. run makes three independent train-only
SKILL generations and 15 base + 15 matched fewshot test attempts, with no retries.
"""
from __future__ import annotations
import argparse
import asyncio
import contextlib
import dataclasses
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
GROUP = ROOT / 'gdpevo-construction/v4-20261012/task_factory/task_group/task_group_025'
DEFAULT_OUTPUT = ROOT / 'gdpevo-construction/v4-20261012/task_factory/scratch/runtime/staging'
CONFIG = Path('/mnt/c/dev/rsi-eval/config.t46.json') if os.name != 'nt' else Path('C:/dev/rsi-eval/config.t46.json')
for folder in (ROOT, ROOT / 'src', Path('/mnt/c/dev/rsi-eval')):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
LOCKED = {'deepagents': '0.7.14', 'langchain-mcp-adapters': '0.3.2', 'mcp': '1.30.0',
          'opensandbox': '0.1.16', 'pymongo': '4.17.0', 'PyYAML': '6.0.3'}
SYSTEM = '''You are an automotive procurement analyst. Discover authoritative records
using sourcing_read, reconcile versions, and perform financial analysis with
computation_execute in OpenSandbox. You receive only the current task input.
Return the complete answer JSON matching answer_template.json in your final message.
Cite actual document IDs and versions. Do not invent missing records.'''
EXCLUDED = frozenset({'ls', 'read_file', 'write_file', 'edit_file', 'delete', 'glob', 'grep', 'execute', 'task'})

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('manifest path escapes task group')
    if not path.is_file():
        raise ValueError('formal file missing: ' + relative)
    return path

def load_formal(group):
    import yaml
    index = group / 'task_group.yaml'
    if not index.is_file():
        raise ValueError('formal task_group.yaml missing')
    data = yaml.safe_load(index.read_text(encoding='utf-8-sig'))
    if data['task_group']['task_group_id'] != group.name or data['env']['state_mode'] != 'read_only':
        raise ValueError('task group identity/read_only contract mismatch')
    tasks = []
    for split in ('train', 'test'):
        rows = data[split + '_tasks']
        if len(rows) != 5 or {r['task_id'] for r in rows} != {f'{split}_{i:03d}' for i in range(1, 6)}:
            raise ValueError('formal manifest requires exactly 5 train and 5 test')
        for row in rows:
            prefix = split + '_tasks/' + row['task_id'].split('_')[1] + '/input/'
            if row['input'].rstrip('/') != prefix.rstrip('/'):
                raise ValueError('input differs from official layout')
            if row['prompt_txt'] != prefix + 'prompt.txt' or prefix + 'payloads/answer_template.json' not in row['payloads']:
                raise ValueError('prompt/template must be in task input')
            for relative in [row['prompt_txt'], *row['payloads']]:
                if not relative.startswith(prefix):
                    raise ValueError('solver file outside input directory')
                inside(group, relative)
            for relative in [row['notes'], row['answer_json'], *row['eval']['files'], row['eval']['script']]:
                inside(group, relative)
            tasks.append({**row, 'split': split})
    return data, tasks

def freeze_manifest(output):
    write(output / 'manifest.json', {str(p.relative_to(output)): digest(p) for p in output.rglob('*')
                                    if p.is_file() and p.name != 'manifest.json'})

def source_files(folder):
    """Source inventory excludes generated build/runtime state, never business seed/source."""
    excluded = {'target', '__pycache__', '.git', '.pytest_cache'}
    return sorted(p.resolve() for p in folder.rglob('*') if p.is_file()
                  and not any(part in excluded for part in p.relative_to(folder).parts))

def prepare(group=GROUP, output=DEFAULT_OUTPUT):
    data, tasks = load_formal(group)
    if output.exists():
        raise FileExistsError('staging already exists; preserve it and use a new --output')
    output.mkdir(parents=True)
    frozen = {str((group / 'task_group.yaml').resolve()): digest(group / 'task_group.yaml')}
    inventories = {}
    folders = [group / 'env']
    folders += [(group / t['input']).resolve() for t in tasks]
    folders += [inside(group, t['eval']['script']).parent for t in tasks]
    for folder in folders:
        paths = source_files(folder)
        inventories[str(folder.resolve())] = [str(p) for p in paths]
        frozen.update({str(p): digest(p) for p in paths})
    # Runtime/config identities are operator evidence, never copied into Actor inputs.
    for path in (Path(__file__).resolve(), ROOT / 'uv.lock', CONFIG):
        if path.is_file():
            frozen[str(path)] = digest(path)
    supervision = []
    for task in tasks:
        entries = []
        for relative in [task['prompt_txt'], *task['payloads']]:
            path = inside(group, relative)
            name = Path(relative).relative_to(task['input'])
            target = output / 'actor-inputs' / task['task_id'] / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
            entries.append({'path': str(name), 'text': path.read_text(encoding='utf-8-sig')})
        for relative in [task['prompt_txt'], *task['payloads'], task['notes'], task['answer_json'],
                         *task['eval']['files'], task['eval']['script']]:
            frozen[str(inside(group, relative))] = digest(inside(group, relative))
        if task['split'] == 'train':
            supervision.append({'task_id': task['task_id'], 'input': entries, 'answer': read(inside(group, task['answer_json']))})
    schedule = [{'task_id': f'test_{n:03d}', 'repeat': repeat, 'arm': arm,
                 'skill_session': repeat if arm == 'fewshot' else None}
                for repeat in range(1, 4) for n in range(1, 6) for arm in ('base', 'fewshot')]
    write(output / 'fewshot-input.json', supervision)
    write(output / 'protocol.json', {'schema_version': 1, 'kind': 'DS-adapted Stage2 development calibration',
        'status': 'prepared', 'task_group': str(group.resolve()), 'model_identity_source': str(CONFIG),
        'actor_input': 'only target input and domain MCP access; no facts bundle or evaluator',
        'tasks': tasks, 'schedule': schedule, 'skills_sessions': 3, 'sealed': False,
        'official_gpt_calibration': False, 'learning_gain_proven': False, 'runtime_versions': LOCKED,
        'max_model_calls_per_session': 30, 'max_tool_calls': 36, 'max_output_tokens': 8192,
        'recursion_limit': 120, 'repairs_or_retries': 0})
    write(output / 'source-hashes.json', frozen)
    write(output / 'source-inventories.json', inventories)
    freeze_manifest(output)
    return {'status': 'prepared', 'model_calls': 0, 'calibration_complete': False, 'schedule_attempts': 30}

def check_frozen_sources(output):
    for name, expected in read(output / 'source-hashes.json').items():
        path = Path(name)
        if not path.is_file() or digest(path) != expected:
            raise ValueError('frozen source changed: ' + name)
    for folder, expected in read(output / 'source-inventories.json').items():
        if [str(p) for p in source_files(Path(folder))] != expected:
            raise ValueError('frozen source inventory changed: ' + folder)
    for relative, expected in read(output / 'manifest.json').items():
        if digest(output / relative) != expected:
            raise ValueError('staging evidence changed: ' + relative)

def validate_domain_request(path, params):
    if not isinstance(path, str) or '%' in path or '..' in path or '?' in path or '#' in path:
        raise ValueError('domain path denied')
    match = re.fullmatch(r'/api/sourcing/(programs|forecasts|quotes|documents|engineering|contracts|payments|suppliers)(?:/([A-Za-z0-9_-]+))?', path)
    if not match or (match[2] and match[1] not in {'programs', 'documents'}):
        raise ValueError('domain path denied')
    if not isinstance(params, dict) or set(params) - {'program_id', 'supplier_id', 'query', 'offset', 'limit'}:
        raise ValueError('query parameter denied')
    for key, value in params.items():
        if key in {'offset', 'limit'}:
            if type(value) is not int or value < 0 or (key == 'limit' and not 1 <= value <= 100):
                raise ValueError('invalid pagination')
        elif not isinstance(value, str) or len(value) > 120 or any(c in value for c in '\r\n'):
            raise ValueError('invalid query value')
    return path, params

def docker(*argv):
    value = subprocess.run(['docker', *argv], text=True, encoding='utf-8', errors='replace',
                           capture_output=True, timeout=30)
    if value.returncode:
        raise RuntimeError('Docker ' + ' '.join(argv[:2]) + ': ' + value.stderr[-500:])
    return value.stdout

def validate_container_isolation(container, network, name):
    if any(m.get('Type') != 'volume' or m.get('RW', False) for m in container.get('Mounts', [])):
        raise ValueError('sandbox has host/writable mount')
    host = container.get('HostConfig', {})
    if host.get('Privileged') or host.get('PidMode') == 'host':
        raise ValueError('sandbox host privilege denied')
    if set(container['NetworkSettings']['Networks']) != {name} or not network.get('Internal'):
        raise ValueError('sandbox must attach only to the private internal network')

def inspect_sandbox(sandbox_id, network_name):
    containers = json.loads(docker('inspect', 'sandbox-' + sandbox_id))
    network = json.loads(docker('network', 'inspect', network_name))[0]
    validate_container_isolation(containers[0], network, network_name)
    return {'container_id': containers[0]['Id'], 'network_id': network['Id'], 'network': network_name,
            'internal': network['Internal'], 'mounts': containers[0].get('Mounts', []), 'options': network.get('Options', {})}

@contextlib.contextmanager
def isolated_control(config, directory):
    import httpx
    directory.mkdir(parents=True, exist_ok=False)
    name = 't67-' + uuid.uuid4().hex[:16]
    docker('network', 'create', '--internal', '--opt', 'com.docker.network.bridge.gateway_mode_ipv4=isolated', '--label', 'purpose=t67', name)
    process = None
    try:
        template = Path(config['sandbox_template']).read_text(encoding='utf-8')
        template = template.replace('network_mode = "bridge"', f'network_mode = "{name}"')
        port = config['sandbox_port']
        template = template.replace('port = 18080', f'port = {port}')
        template = template.replace('path = "~/.opensandbox/opensandbox.db"', 'path = "' + (directory / 'control.db').as_posix() + '"')
        template = template.replace('allowed_host_paths = ["~/.opensandbox/mounts"]', 'allowed_host_paths = []')
        path = directory / 'sandbox.toml'
        path.write_text(template, encoding='utf-8')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
        with (directory / 'server.log').open('xb') as log:
            process = subprocess.Popen([config['sandbox_server'], '--config', str(path)], cwd=directory,
                stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ, 'OPENSANDBOX_INSECURE_SERVER': 'YES', 'PYTHONUTF8': '1'})
            with httpx.Client(timeout=2, trust_env=False) as client:
                deadline = time.monotonic() + 45
                while True:
                    if process.poll() is not None:
                        raise RuntimeError('private OpenSandbox exited; see server.log')
                    try:
                        if client.get(f'http://127.0.0.1:{port}/health').status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() > deadline:
                        raise RuntimeError('private OpenSandbox readiness timeout')
                    time.sleep(.5)
            yield name
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        docker('network', 'rm', name)

def model_config(config):
    from agent.config import ModelConfig
    from agent.env_utils import load_env
    identity = read(Path(config['model_config']))
    env = load_env(path=Path(config['env_file']))
    env.update({'MODEL_ID': identity['model_id'], 'MODEL_TEMPERATURE': str(identity.get('model_temperature', 0))})
    return ModelConfig.from_env(env), identity

def sandbox_config(config):
    from agent.backends.sandbox_setup import SandboxRuntimeConfig
    from opensandbox.transport.retry import RetryPolicy
    from datetime import timedelta
    class ProxyConfig(SandboxRuntimeConfig):
        def connection_config(self):
            connection = super().connection_config()
            return connection.model_copy(update={'use_server_proxy': True,
                'retry_policy': RetryPolicy(max_retries=0, per_attempt_timeout=timedelta(seconds=5))})
    return ProxyConfig.from_env({**os.environ, 'OPENSANDBOX_DOMAIN': f"127.0.0.1:{config['sandbox_port']}",
        'OPENSANDBOX_BASE_URL': f"http://127.0.0.1:{config['sandbox_port']}", 'OPENSANDBOX_IMAGE': config['sandbox_image'],
        'OPENSANDBOX_READY_TIMEOUT_SECONDS': '30'})

def probe_isolation(config, output, network):
    from agent.backends.custom_opensandbox import create_backend
    backend, report = create_backend(sandbox_config(config), metadata={'purpose': 't67-isolation-doctor'})
    try:
        evidence = inspect_sandbox(backend.id, network)
        code = '''import json,socket
results=[]
for host in ('host.docker.internal','172.17.0.1','1.1.1.1'):
 for port in (27028,18081,9025,443):
  s=socket.socket();s.settimeout(.3)
  try: s.connect((host,port));results.append([host,port])
  except OSError: pass
  finally:s.close()
print(json.dumps(results))
'''
        response = backend.execute('python -c ' + __import__('shlex').quote(code))
        write(output / 'isolation-probe.json', {'inspection': evidence, 'response': dataclasses.asdict(response)})
        if response.exit_code != 0 or json.loads(response.output.strip()) != []:
            raise ValueError('sandbox can reach host/control/external network')
        return evidence
    finally:
        backend.close()

def doctor(output, config_path):
    import importlib.metadata
    result = {'status': 'blocked', 'model_calls': 0, 'calibration_complete': False, 'checks': {}, 'reasons': []}
    try:
        check_frozen_sources(output)
        config = read(config_path)
        required = {'model_config', 'env_file', 'mongo_uri', 'sandbox_template', 'sandbox_server',
                    'sandbox_port', 'sandbox_image', 'business_url', 'business_token_env'}
        if not required <= set(config):
            raise ValueError('runtime config missing: ' + ','.join(sorted(required - set(config))))
        versions = {name: importlib.metadata.version(name) for name in LOCKED}
        if versions != LOCKED:
            raise ValueError('installed runtime versions differ from inspected lock')
        result['checks']['versions'] = versions
        model, identity = model_config(config)
        result['model_identity'] = model.redacted()
        from deepagents import create_deep_agent
        from langchain_mcp_adapters.client import MultiServerMCPClient
        result['checks']['signatures'] = {'create_deep_agent': str(inspect.signature(create_deep_agent)), 'mcp_client': str(inspect.signature(MultiServerMCPClient))}
        from pymongo import MongoClient
        with MongoClient(config['mongo_uri'], serverSelectionTimeoutMS=3000) as mongo:
            mongo.admin.command('ping')
        result['checks']['mongo_ping'] = True
        import httpx
        token = os.environ.get(config['business_token_env'], '')
        if not token:
            raise ValueError('business auth token missing')
        with httpx.Client(timeout=5, trust_env=False) as client:
            health = client.get(config['business_url'].rstrip('/') + '/health')
            if health.status_code != 200:
                raise ValueError('Java business service health failed')
            expected = {'service': 'java-sourcing-erp', 'status': 'ready', 'database': 'up', 'state_mode': 'read_only'}
            if any(health.json().get('data', {}).get(k) != v for k, v in expected.items()):
                raise ValueError('Java business environment seed/database not ready')
        result['checks']['business_health'] = health.json()
        directory = output / ('doctor-' + uuid.uuid4().hex[:10])
        with isolated_control(config, directory) as network:
            result['checks']['sandbox_isolation'] = probe_isolation(config, directory, network)
        result['reasons'].append('runtime staging incomplete: real MCP doctor probe and independent sandbox fewshot generation not yet verified')
        result['config_sha256'] = digest(config_path)
    except Exception as error:
        result['reasons'].append(type(error).__name__ + ': ' + str(error)[:600])
    write(output / ('doctor-result-' + uuid.uuid4().hex[:10] + '.json'), result)
    return result

def mcp_worker(config_path, output, owner, thread, network):
    """Real MCP stdio server. Owner/thread are process-bound, never tool arguments."""
    from mcp.server.fastmcp import FastMCP
    from api_view.web_config import MongoResources, PersistenceSettings
    from agent.persistence.repository import ApplicationRepository
    from agent.backends.sandbox_manager import SandboxManager, MongoSandboxRegistry, OpenSandboxFactory
    from agent.planning.computation import ComputationService
    import httpx
    config = read(config_path)
    resources = MongoResources(PersistenceSettings(mongo_uri=config['mongo_uri'], database=config['database'])).start()
    manager = SandboxManager(factory=OpenSandboxFactory(sandbox_config(config), marker='t67-' + owner),
        registry=MongoSandboxRegistry(resources.database), warm_pool_size=0, replenish_in_background=False)
    manager.start()
    service = ComputationService(resources.database, manager.get_or_create)
    ApplicationRepository(resources.database).ensure_thread(owner_user_id=owner, thread_id=thread, title='T67 isolated Actor')
    server = FastMCP('t67-business-computation')
    output.mkdir(parents=True, exist_ok=True)
    def trace(kind, request, response):
        with (output / 'tools.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps({'kind': kind, 'request': request, 'response': response}, ensure_ascii=False, default=str) + '\n')
    @server.tool()
    def sourcing_read(path: str, params: dict | None = None) -> dict:
        """Read domain sourcing endpoints only; supports pagination and document/program IDs."""
        path, params = validate_domain_request(path, params or {})
        with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
            response = client.get(config['business_url'].rstrip('/') + path, params=params,
                headers={'X-Service-Token': os.environ[config['business_token_env']]})
        if response.status_code != 200:
            raise ValueError('domain request failed HTTP ' + str(response.status_code))
        value = response.json()
        trace('sourcing_read', {'path': path, 'params': params}, value)
        return value
    @server.tool()
    def computation_execute(code: str, read_names: list[str] | None = None, session: str = 'analysis', timeout: int = 60) -> dict:
        """Execute Python in OpenSandbox. load_state/save_state store declared JSON in Mongo."""
        proxy = manager.get_or_create(owner)
        evidence = inspect_sandbox(proxy.id, network)
        write(output / 'sandbox-inspection.json', evidence)
        value = service.execute(owner, thread, code, read_names=read_names or [], session=session, timeout=timeout)
        trace('computation_execute', {'code': code, 'read_names': read_names, 'session': session}, value)
        write(output / 'executions.json', list(service.executions.find({'owner_user_id': owner, 'thread_id': thread}, {'_id': 0})))
        return value
    @server.tool()
    def computation_status(session: str = 'analysis') -> dict:
        """Inspect your committed computation data version and metadata."""
        value = service.status(owner, thread, session=session)
        trace('computation_status', {'session': session}, value)
        return value
    try:
        server.run('stdio')
    finally:
        write(output / 'executions.json', list(service.executions.find({'owner_user_id': owner, 'thread_id': thread}, {'_id': 0})))
        write(output / 'cleanup.json', {'recycled': manager.recycle(owner), 'shutdown': manager.shutdown()})
        resources.close()

def register_profile(model):
    from deepagents import HarnessProfile, GeneralPurposeSubagentProfile, register_harness_profile
    register_harness_profile('openai:' + model.model_name,
        HarnessProfile(excluded_tools=EXCLUDED, general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))

def recorder_class():
    from langchain_core.callbacks import BaseCallbackHandler
    class Recorder(BaseCallbackHandler):
        def __init__(self, path):
            self.path = path
        def save(self, kind, value):
            with self.path.open('a', encoding='utf-8') as log:
                log.write(json.dumps({'kind': kind, 'value': value}, ensure_ascii=False, default=str) + '\n')
        def on_chat_model_start(self, serialized, messages, **kwargs):
            self.save('model_input', [[m.model_dump(mode='json') for m in batch] for batch in messages])
        def on_llm_end(self, response, **kwargs):
            self.save('model_output', response.model_dump(mode='json'))
        def on_llm_error(self, error, **kwargs):
            self.save('model_error', type(error).__name__)
        def on_tool_start(self, serialized, input_str, **kwargs):
            self.save('tool_start', {'name': serialized.get('name'), 'input': input_str})
        def on_tool_end(self, output, **kwargs):
            self.save('tool_end', str(output))
        def on_tool_error(self, error, **kwargs):
            self.save('tool_error', type(error).__name__)
    return Recorder

def text_content(content):
    if isinstance(content, str):
        return content
    return ''.join(item.get('text', '') for item in content if isinstance(item, dict))

def parse_json_answer(content):
    text = text_content(content).strip()
    if text.startswith('```'):
        text = re.sub(r'^\x60{3}(?:json)?\s*|\x60{3}\s*$', '', text).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('answer must be a JSON object')
    return value

def actor_input(output, task_id):
    folder = output / 'actor-inputs' / task_id
    return [{'path': str(p.relative_to(folder)), 'text': p.read_text(encoding='utf-8-sig')}
            for p in sorted(folder.rglob('*')) if p.is_file()]

async def actor_session(model, config_path, directory, task_input, skill, network):
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend
    from langchain_core.messages import HumanMessage
    from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from api_view.web_config import MongoResources, PersistenceSettings
    from agent.persistence.scoped_store import UserScopedStore
    owner, thread = 't67-' + uuid.uuid4().hex[:20], 't67-' + uuid.uuid4().hex[:20]
    config = read(config_path)
    resources = MongoResources(PersistenceSettings(mongo_uri=config['mongo_uri'], database=config['database'])).start()
    directory.mkdir(parents=True)
    recorder = recorder_class()(directory / 'raw-trace.jsonl')
    client = MultiServerMCPClient({'business': {'transport': 'stdio', 'command': sys.executable,
        'args': [str(Path(__file__)), 'mcp-worker', '--runtime-config', str(config_path), '--output', str(directory / 'mcp'),
                 '--owner', owner, '--thread', thread, '--network', network],
        'env': {config['business_token_env']: os.environ[config['business_token_env']], 'PYTHONUTF8': '1'}}})
    messages = []
    try:
        async with client.session('business') as session:
            from langchain_mcp_adapters.tools import load_mcp_tools
            tools = await load_mcp_tools(session)
            if {t.name for t in tools} != {'sourcing_read', 'computation_execute', 'computation_status'}:
                raise ValueError('unexpected MCP capability')
            graph = create_deep_agent(model=model, tools=tools, backend=StateBackend(), checkpointer=resources.checkpointer,
                store=UserScopedStore(resources.store, owner), system_prompt=SYSTEM + ('\nTrain-derived skill:\n' + skill if skill else ''),
                middleware=[ModelCallLimitMiddleware(run_limit=30, exit_behavior='end'), ToolCallLimitMiddleware(run_limit=36, exit_behavior='end')])
            payload = {'messages': [HumanMessage(content=json.dumps(task_input, ensure_ascii=False))]}
            scope = {'configurable': {'thread_id': thread, 'owner_user_id': owner}, 'recursion_limit': 120, 'callbacks': [recorder]}
            async for state in graph.astream(payload, config=scope, stream_mode='values'):
                messages = state.get('messages', [])
                write(directory / 'messages.json', [m.model_dump(mode='json') for m in messages])
            if not messages:
                raise ValueError('Actor returned no messages')
            content = text_content(messages[-1].content)
            (directory / 'final-content.txt').write_text(content, encoding='utf-8')
            write(directory / 'submission.json', parse_json_answer(content))
            return {'owner': owner, 'thread': thread, 'submission': str(directory / 'submission.json')}
    finally:
        resources.close()

def generate_skill(model, directory, supervision):
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend
    from langchain_core.messages import HumanMessage
    from langchain.agents.middleware import ModelCallLimitMiddleware
    directory.mkdir(parents=True)
    graph = create_deep_agent(model=model, tools=[], backend=StateBackend(),
        system_prompt='''Generate a reusable SKILL.md package from the five complete train inputs and
correct answers. Derive transferable procurement procedures and worked fewshot examples.
Output only SKILL.md markdown, starting with YAML front matter containing name and description.
No test input/answer is available. Do not invent source facts.''',
        middleware=[ModelCallLimitMiddleware(run_limit=30, exit_behavior='end')])
    result = graph.invoke({'messages': [HumanMessage(content=json.dumps(supervision, ensure_ascii=False))]},
        config={'callbacks': [recorder_class()(directory / 'raw-trace.jsonl')], 'recursion_limit': 120})
    write(directory / 'messages.json', [m.model_dump(mode='json') for m in result['messages']])
    body = text_content(result['messages'][-1].content).strip()
    if body.startswith('```'):
        body = re.sub(r'^\x60{3}(?:markdown|md)?\s*|\x60{3}\s*$', '', body).strip()
    if not body.startswith('---') or 'description:' not in body or 'name:' not in body:
        raise ValueError('generated skill lacks required front matter')
    (directory / 'SKILL.md').write_text(body + '\n', encoding='utf-8')
    return body

def evaluate_submission(task, submission, group, directory=None):
    script = inside(group, task['eval']['script'])
    result = subprocess.run(['bash', str(script), str(submission.resolve())], cwd=script.parent,
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    if directory is not None:
        (directory / 'evaluator-stdout.txt').write_text(result.stdout, encoding='utf-8')
        (directory / 'evaluator-stderr.txt').write_text(result.stderr, encoding='utf-8')
    if result.returncode:
        raise ValueError('private evaluator failed exit ' + str(result.returncode))
    grade = json.loads(result.stdout)
    if not isinstance(grade.get('score'), (float, int)) or not 0 <= grade['score'] <= 1 or not isinstance(grade.get('points'), list):
        raise ValueError('private evaluator score contract invalid')
    return grade

def score(task, submission, group, directory):
    return evaluate_submission(task, submission, group, directory)

def verify_scored_submission(task, submission, group, stored_grade):
    """Private evaluator re-execution, with no mutation of original attempt evidence."""
    actual = evaluate_submission(task, submission, group)
    if actual != stored_grade:
        raise ValueError('stored grade differs from independent private rescore')
    return actual

def jsonl(path):
    if not path.is_file() or not path.read_text(encoding='utf-8-sig').strip():
        raise ValueError('empty/missing trace: ' + str(path))
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]

def callback_usage(value):
    usage = (value.get('llm_output') or {}).get('usage')
    if usage:
        pair = (usage.get('prompt_tokens'), usage.get('completion_tokens'))
    else:
        message = value['generations'][0][0]['message']
        meta = message.get('usage_metadata') or {}
        pair = (meta.get('input_tokens'), meta.get('output_tokens'))
    if any(type(x) is not int or x < 0 for x in pair):
        raise ValueError('callback token usage missing/invalid')
    return pair

def verify_model_ledger(run_dir, result, scopes):
    """Reconcile the durable gateway reservation/finish ledger with every session callback."""
    records = jsonl(run_dir / 'model-calls.jsonl')
    reserved, finished = {}, {}
    for record in records:
        number = record.get('call')
        if type(number) is not int or number < 1:
            raise ValueError('invalid model call identity')
        target = finished if 'http_status' in record else reserved
        if number in target:
            raise ValueError('duplicate gateway call reservation/finish')
        target[number] = record
    count = result.get('usage', {}).get('model_calls')
    if type(count) is not int or count <= 0 or set(reserved) != set(range(1, count + 1)) or set(finished) != set(reserved):
        raise ValueError('model gateway ledger missing/nonzero calls or unknown finishes')
    model_id = result.get('model_identity', {}).get('model_id')
    if not model_id:
        raise ValueError('configured model identity absent')
    covered = set()
    for scope in scopes:
        before, after = scope.get('model_calls_before'), scope.get('model_calls_after')
        if type(before) is not int or type(after) is not int or not 0 <= before < after <= count:
            raise ValueError('each generation/Actor session needs nonzero scoped model calls')
        ids = set(range(before + 1, after + 1))
        if ids & covered:
            raise ValueError('model call scopes overlap')
        covered |= ids
        events = jsonl(Path(scope['directory']) / 'raw-trace.jsonl')
        inputs = [e for e in events if e.get('kind') == 'model_input']
        outputs = [e for e in events if e.get('kind') == 'model_output']
        if len(inputs) != len(ids) or len(outputs) != len(ids) or any(not e.get('value') for e in inputs):
            raise ValueError('gateway/callback model call counts differ or trace empty')
        if any(e.get('kind') in {'model_error', 'tool_error'} for e in events):
            raise ValueError('failed callback retained; calibration cannot pass')
        for number, event in zip(sorted(ids), outputs):
            call = finished[number]
            if call.get('http_status') != 200 or not call.get('started_at') or not call.get('finished_at'):
                raise ValueError('failed/unknown gateway call status')
            if call.get('observed_model') != model_id or not re.fullmatch('[a-f0-9]{64}', call.get('request_sha256', '')):
                raise ValueError('observed model/request fingerprint missing or mismatched')
            usage = call.get('usage') or {}
            if callback_usage(event['value']) != (usage.get('prompt_tokens'), usage.get('completion_tokens')):
                raise ValueError('gateway/callback token usage differs')
    if covered != set(reserved):
        raise ValueError('unassigned model calls; all attempts/generations must count')
    totals = {'model_calls': count, 'input_tokens': sum(c['usage']['prompt_tokens'] for c in finished.values()),
              'output_tokens': sum(c['usage']['completion_tokens'] for c in finished.values())}
    if any(result['usage'].get(k) != v for k, v in totals.items()) or result['usage'].get('usage_complete') is not True:
        raise ValueError('usage summary differs from actual gateway ledger')
    return {**totals, 'unknown_usage_calls': 0, 'actual_cost_cny': result['usage'].get('actual_cost_cny')}

def verify_skills(output, result):
    import yaml
    skills = result.get('skills', [])
    if len(skills) != 3 or {s.get('session') for s in skills} != {1, 2, 3}:
        raise ValueError('three actual generated SKILL packages absent')
    scopes = []
    supervision = read(output / 'fewshot-input.json')
    for skill in skills:
        directory = output / 'run/skills' / f"session-{skill['session']}"
        path = directory / 'SKILL.md'
        if not path.is_file() or skill.get('sha256') != digest(path):
            raise ValueError('generated SKILL file/hash missing')
        body = path.read_text(encoding='utf-8').strip()
        parts = body.split('---', 2)
        front = yaml.safe_load(parts[1]) if len(parts) == 3 and not parts[0] else None
        if not isinstance(front, dict) or not front.get('name') or not front.get('description') or not parts[-1].strip():
            raise ValueError('generated SKILL content/front matter missing')
        provenance = read(directory / 'provenance.json')
        if provenance.get('train_only') is not True or provenance.get('supervision_sha256') != digest(output / 'fewshot-input.json'):
            raise ValueError('skill train-only provenance missing')
        events = jsonl(directory / 'raw-trace.jsonl')
        first = next(e['value'][0] for e in events if e.get('kind') == 'model_input')
        human = [m for m in first if m.get('type') == 'human']
        if len(human) != 1 or json.loads(human[0]['content']) != supervision:
            raise ValueError('skill generation did not use exactly frozen train-only examples')
        messages = read(directory / 'messages.json')
        if not messages or messages[-1].get('type') != 'ai':
            raise ValueError('successful skill generation messages absent')
        final = text_content(messages[-1].get('content', '')).strip()
        final = re.sub(r'^\x60{3}(?:markdown|md)?\s*|\x60{3}\s*$', '', final).strip()
        if body != final:
            raise ValueError('SKILL file differs from actual model generation')
        scopes.append({**skill, 'directory': str(directory)})
    return scopes

def verify_mcp_attempt(directory, row):
    tools = jsonl(directory / 'mcp/tools.jsonl')
    events = jsonl(directory / 'raw-trace.jsonl')
    starts = [e['value'] for e in events if e.get('kind') == 'tool_start']
    if not starts or len(starts) != len(tools):
        raise ValueError('MCP/callback tool trace counts differ or empty')
    for call, callback in zip(tools, starts):
        kind = call.get('kind')
        if kind not in {'sourcing_read', 'computation_execute', 'computation_status'} or callback.get('name') != kind:
            raise ValueError('forbidden/non-MCP capability in Actor trace')
        if kind == 'sourcing_read':
            validate_domain_request(call['request']['path'], call['request']['params'])
    if not any(t['kind'] == 'sourcing_read' for t in tools):
        raise ValueError('no real domain discovery')
    executions = read(directory / 'mcp/executions.json')
    completed = [e for e in executions if e.get('status') == 'completed']
    if not completed or any(e.get('owner_user_id') != row.get('owner') or e.get('thread_id') != row.get('thread') for e in executions):
        raise ValueError('completed/scoped OpenSandbox computation mechanism absent')
    inspection = read(directory / 'mcp/sandbox-inspection.json')
    if not inspection.get('container_id') or not inspection.get('network_id') or inspection.get('internal') is not True:
        raise ValueError('actual sandbox isolation inspection absent')
    if read(directory / 'mcp/cleanup.json').get('recycled') is not True:
        raise ValueError('sandbox recycle failed/unverified')
    return {'completed_executions': len(completed), 'computation_contribution': 'unassessed'}

def policy(identity):
    pricing = identity['budget']
    return {'enforce_cost_limit': False, 'total_cny': None, 'per_attempt_cny': None, 'max_model_calls': 990,
        'max_output_tokens': 8192, 'max_request_bytes': 1000000, 'timeout_seconds': 240,
        'input_cny_per_million': pricing['input_cny_per_million'], 'output_cny_per_million': pricing['output_cny_per_million'],
        'pricing_kind': pricing['pricing_kind'], 'pricing_source': pricing['pricing_source']}

def run(output, config_path):
    check_frozen_sources(output)
    readiness = doctor(output, config_path)
    if readiness['status'] != 'ready':
        raise ValueError('doctor blocked: ' + '; '.join(readiness['reasons']))
    run_dir = output / 'run'
    if run_dir.exists():
        raise FileExistsError('attempt exists; never resume or overwrite automatically')
    run_dir.mkdir()
    config = read(config_path)
    config['database'] = 'rush_harness_test_t67_' + uuid.uuid4().hex[:16]
    run_config = run_dir / 'runtime.json'
    write(run_config, config)
    write(run_dir / 'doctor-receipt.json', readiness)
    from procurement_eval.budget_proxy import gateway
    original, identity = model_config(config)
    protocol = read(output / 'protocol.json')
    result = {'status': 'running', 'rows': [], 'skills': [], 'model_identity': original.redacted(),
              'kind': protocol['kind'], 'sealed': False, 'learning_gain_proven': False,
              'source_hashes_sha256': digest(output / 'source-hashes.json')}
    budget = None
    try:
        with (run_dir / 'model-calls.jsonl').open('x', encoding='utf-8') as log:
            with gateway(original, policy(identity), log, identity.get('model_extra_body')) as (url, budget):
                model = dataclasses.replace(original, base_url=url, api_key='evaluation-proxy', max_tokens=8192).create_chat_model()
                model.max_retries = 0
                register_profile(model)
                for repeat in range(1, 4):
                    skill_dir = run_dir / 'skills' / f'session-{repeat}'
                    before = budget.calls
                    generate_skill(model, skill_dir, read(output / 'fewshot-input.json'))
                    write(skill_dir / 'provenance.json', {'train_only': True,
                          'supervision_sha256': digest(output / 'fewshot-input.json')})
                    result['skills'].append({'session': repeat, 'path': str(skill_dir / 'SKILL.md'),
                        'sha256': digest(skill_dir / 'SKILL.md'), 'model_calls_before': before, 'model_calls_after': budget.calls})
                    write(run_dir / 'result.json', result)
                with isolated_control(config, run_dir / 'sandbox-control') as network:
                    probe_isolation(config, run_dir / 'sandbox-control', network)
                    failures = 0
                    for entry in protocol['schedule']:
                        task = next(t for t in protocol['tasks'] if t['task_id'] == entry['task_id'])
                        directory = run_dir / 'attempts' / f"r{entry['repeat']}-{entry['task_id']}-{entry['arm']}"
                        row = {**entry, 'status': 'started', 'grade': {'score': 0.0, 'points': []}, 'model_calls_before': budget.calls}
                        started = time.monotonic()
                        try:
                            skill = None if entry['arm'] == 'base' else (run_dir / 'skills' / f"session-{entry['repeat']}" / 'SKILL.md').read_text(encoding='utf-8')
                            row.update(asyncio.run(actor_session(model, run_config, directory, actor_input(output, entry['task_id']), skill, network)))
                            row['grade'] = score(task, directory / 'submission.json', Path(protocol['task_group']), directory)
                            row['status'] = 'scored'
                            failures = 0
                        except Exception as error:
                            row.update(status='failed', error={'type': type(error).__name__})
                            failures += 1
                        row.update(model_calls_after=budget.calls, elapsed_seconds=round(time.monotonic() - started, 3))
                        result['rows'].append(row)
                        write(directory / 'result.json', row)
                        write(run_dir / 'result.json', result)
                        if failures >= 2:
                            raise RuntimeError('two consecutive infrastructure/format failures; stop for discussion')
                    result['status'] = 'completed'
    except Exception as error:
        result.update(status='failed', error={'type': type(error).__name__, 'message': str(error)[:300]})
    finally:
        result['usage'] = budget.summary() if budget else {'model_calls': 0, 'actual_cost_cny': None}
        write(run_dir / 'result.json', result)
        freeze_manifest(run_dir)
    return result

def verify(output=DEFAULT_OUTPUT):
    value = {'status': 'blocked', 'calibration_complete': False, 'learning_gain_proven': False,
             'sealed': False, 'official_gpt_calibration': False, 'reasons': [],
             'computation_contribution': 'unassessed', 't67_complete': False}
    if not (output / 'protocol.json').is_file():
        value['reasons'].append('formal preparation absent')
        return value
    check_frozen_sources(output)
    protocol = read(output / 'protocol.json')
    group = Path(protocol['task_group'])
    _, formal = load_formal(group)
    if protocol.get('tasks') != formal:
        value['reasons'].append('formal task/scorer version differs from prepared protocol')
    if not read(output / 'source-inventories.json').get(str((group / 'env').resolve())):
        value['reasons'].append('environment source/seed/generator freeze missing')
    run_dir = output / 'run'
    if not (run_dir / 'result.json').is_file():
        value['reasons'].append('three real skill sessions and 30 real Actor attempts not run')
        return value
    result = read(run_dir / 'result.json')
    manifest = read(run_dir / 'manifest.json')
    for relative, expected_hash in manifest.items():
        path = inside(run_dir, relative)
        if digest(path) != expected_hash:
            raise ValueError('run evidence hash mismatch')
    required = {'result.json', 'runtime.json', 'doctor-receipt.json', 'model-calls.jsonl'}
    if not required <= set(manifest):
        value['reasons'].append('run model/doctor/config evidence absent from frozen manifest')
    if result.get('source_hashes_sha256') != digest(output / 'source-hashes.json'):
        value['reasons'].append('run source/environment fingerprint missing or differs')
    try:
        ready = read(run_dir / 'doctor-receipt.json')
        config = read(run_dir / 'runtime.json')
        if ready.get('status') != 'ready' or ready.get('model_calls') != 0:
            raise ValueError('doctor was not zero-generation ready')
        checks = ready.get('checks', {})
        if not checks.get('sandbox_isolation') or not checks.get('mcp_protocol', {}).get('passed'):
            raise ValueError('actual isolation and real MCP doctor evidence missing')
        identity = read(Path(config['model_config']))
        if result.get('model_identity', {}).get('model_id') != identity['model_id']:
            raise ValueError('configured DS identity differs from run model')
    except (OSError, ValueError, KeyError, TypeError) as error:
        value['reasons'].append('doctor/model identity blocked: ' + str(error))
    rows = result.get('rows', [])
    expected = {(f'test_{n:03d}', repeat, arm) for n in range(1, 6) for repeat in range(1, 4) for arm in ('base', 'fewshot')}
    actual = {(r.get('task_id'), r.get('repeat'), r.get('arm')) for r in rows}
    if result.get('status') != 'completed' or len(rows) != 30 or actual != expected:
        value['reasons'].append('complete matched 15+15 denominator missing')
    scopes = []
    try:
        scopes.extend(verify_skills(output, result))
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        value['reasons'].append('real skill generation evidence blocked: ' + str(error))
    rescored = {'base': [], 'fewshot': []}
    mechanisms = []
    for row in rows:
        label = f"r{row.get('repeat')}-{row.get('task_id')}-{row.get('arm')}"
        directory = run_dir / 'attempts' / label
        scopes.append({**row, 'directory': str(directory)})
        if row.get('status') != 'scored':
            value['reasons'].append('failed attempt retained in denominator: ' + label)
            continue
        try:
            task = next(t for t in formal if t['task_id'] == row['task_id'])
            messages = read(directory / 'messages.json')
            if not messages or messages[-1].get('type') != 'ai':
                raise ValueError('actual terminal model message absent')
            submission = directory / 'submission.json'
            if parse_json_answer(messages[-1]['content']) != read(submission):
                raise ValueError('submission differs from actual model output')
            grade = verify_scored_submission(task, submission, group, row['grade'])
            rescored[row['arm']].append(grade['score'])
            mechanisms.append(verify_mcp_attempt(directory, row))
        except (OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.SubprocessError) as error:
            value['reasons'].append('attempt evidence blocked ' + label + ': ' + str(error))
    value['usage'] = result.get('usage', {})
    try:
        value['reconciled_usage'] = verify_model_ledger(run_dir, result, scopes)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        value['reasons'].append('real model call evidence blocked: ' + str(error))
    value['denominator'] = {'base': 15, 'fewshot': 15, 'failed_or_unverified': 30 - sum(map(len, rescored.values()))}
    value['means'] = {arm: sum(scores) / 15 for arm, scores in rescored.items()}
    value['mechanism_evidence'] = mechanisms
    base, fewshot = value['means']['base'], value['means']['fewshot']
    if not (.40 <= base <= .60 and .10 <= fewshot - base <= .30 and fewshot < .80):
        value['reasons'].append('provisional DS construction difficulty/lift targets not met')
    if not value['reasons']:
        value.update(status='calibration_passed', calibration_complete=True)
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('prepare', 'doctor', 'run', 'verify', 'mcp-worker'))
    parser.add_argument('--group', type=Path, default=GROUP)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--runtime-config', type=Path)
    parser.add_argument('--owner')
    parser.add_argument('--thread')
    parser.add_argument('--network')
    args = parser.parse_args()
    if args.command == 'mcp-worker':
        mcp_worker(args.runtime_config, args.output, args.owner, args.thread, args.network)
        return
    try:
        if args.command == 'prepare':
            value = prepare(args.group, args.output)
        elif args.command == 'verify':
            value = verify(args.output)
        elif args.command == 'doctor':
            value = doctor(args.output, args.runtime_config)
        else:
            value = run(args.output, args.runtime_config)
    except Exception as error:
        value = {'status': 'blocked', 'error': {'type': type(error).__name__, 'message': str(error)[:600]}, 'calibration_complete': False}
    print(json.dumps(value, ensure_ascii=False, default=str))
    if value['status'] in {'blocked', 'failed'}:
        raise SystemExit(2)

if __name__ == '__main__':
    main()


