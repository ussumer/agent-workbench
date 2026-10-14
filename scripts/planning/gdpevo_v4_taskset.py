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
EXCLUDED = frozenset({'ls', 'read_file', 'write_file', 'edit_file', 'delete', 'glob', 'grep', 'execute', 'task', 'write_todos'})
SOURCING_READ_DESCRIPTION = '''Read the real Java sourcing environment with HTTP GET. Pass an exact
relative path below; do not pass a base URL or append query strings to path.
Allowed GET paths:
/api/sourcing/programs
/api/sourcing/programs/{program_id}
/api/sourcing/forecasts
/api/sourcing/quotes
/api/sourcing/documents/{document_id}
/api/sourcing/engineering
/api/sourcing/contracts
/api/sourcing/payments
/api/sourcing/suppliers
List-query params: program_id, supplier_id, query (strings), offset (integer >= 0),
limit (integer, 1 <= limit <= 100). Pass these in the params object. Pagination
defaults are offset=0 and limit=50; use total to determine whether another page exists.
List response: {"data":{"items":[...],"total":...,"offset":...,"limit":...},"request_id":...}.
Detail response: {"data":{...},"request_id":...}. Document details contain raw
records and source versions. No answer, score, or financial aggregate endpoint exists.
Example call: path="/api/sourcing/programs/PRG-021", params={}.
Example filtered list: path="/api/sourcing/quotes", params={"program_id":"PRG-021","offset":0,"limit":50}.
Use only these paths; aliases such as /programs or /sourcing are not accepted.'''

def validate_published_sourcing_tool(tools):
    tool = next((item for item in tools if item.name == 'sourcing_read'), None)
    description = getattr(tool, 'description', '') or ''
    required = ['/api/sourcing/' + name for name in ('programs', 'forecasts', 'quotes', 'documents',
                'engineering', 'contracts', 'payments', 'suppliers')]
    required += ['program_id', 'supplier_id', 'query', 'offset', 'limit', '1 <= limit <= 100',
                 'items', 'total', 'request_id', '/api/sourcing/programs/PRG-021']
    if any(item not in description for item in required):
        raise ValueError('published sourcing_read description omits exact route/query contract')
    properties = getattr(tool, 'inputSchema', {}).get('properties', {})
    if not {'path', 'params'} <= set(properties):
        raise ValueError('published sourcing_read argument schema incomplete')
    return {'passed': True, 'description_sha256': hashlib.sha256(description.encode()).hexdigest(),
            'documented_routes': required[:8]}

def failure_details(error, known_secrets=()):
    import traceback
    from agent.env_utils import redact, secret_values
    secrets = set(secret_values(os.environ)) | set(known_secrets)
    causes, seen = [], set()
    def unwrap(current):
        if id(current) in seen:
            return
        seen.add(id(current))
        nested = getattr(current, 'exceptions', ())
        if nested:
            for child in nested:
                unwrap(child)
        elif current.__cause__ is not None:
            unwrap(current.__cause__)
        else:
            causes.append({'type': type(current).__name__, 'message': redact(str(current), secrets)[:1600]})
    unwrap(error)
    return {'type': type(error).__name__, 'message': redact(str(error), secrets)[:1600],
            'causes': causes, 'traceback': redact(''.join(traceback.format_exception(error)), secrets)[-12000:]}

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

def prepare(group=GROUP, output=DEFAULT_OUTPUT, config_path=None, reuse_from=None):
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
    for path in (Path(__file__).resolve(), ROOT / 'uv.lock', CONFIG, ROOT / 'scripts/planning/gdpevo_v4_control.py',
                 ROOT / 'scripts/planning/gdpevo_v4_reuse.py'):
        if path.is_file():
            frozen[str(path)] = digest(path)
    runtime_identity = {}
    if config_path is not None:
        config = read(config_path)
        if not re.fullmatch('sha256:[a-f0-9]{64}', config.get('sandbox_control_image', '')):
            raise ValueError('prepare requires immutable official control image')
        if config.get('business_seed_sha256') != digest(group / 'env/seed.json'):
            raise ValueError('operator seed identity differs from prepared environment')
        frozen[str(config_path.resolve())] = digest(config_path)
        runtime_identity = {key: config[key] for key in ('sandbox_control_image', 'business_image', 'business_seed_sha256')}
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
        'recursion_limit': 120, 'repairs_or_retries': 0, 'runtime_identity': runtime_identity})
    write(output / 'source-hashes.json', frozen)
    write(output / 'source-inventories.json', inventories)
    if reuse_from is not None:
        if config_path is None:
            raise ValueError('explicit skill reuse requires a frozen runtime config')
        from scripts.planning.gdpevo_v4_reuse import bind_reuse
        bind_reuse(output, Path(reuse_from).resolve(), config, normalize_skill_body, Path(__file__).resolve())
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
            'image_id': containers[0]['Image'], 'internal': network['Internal'],
            'mounts': containers[0].get('Mounts', []), 'options': network.get('Options', {})}

@contextlib.contextmanager
def isolated_control(config, directory):
    """Delegate only T67 to the proved, locked official-server dual-NIC topology."""
    from scripts.planning.gdpevo_v4_control import isolated_control as deployed_control
    with deployed_control(config, directory) as network:
        yield network

def actor_tools(tools):
    visible = {'sourcing_read', 'computation_execute', 'computation_status'}
    if {t.name for t in tools} != visible | {'finish_session'}:
        raise ValueError('unexpected MCP capability')
    return [tool for tool in tools if tool.name in visible]

def mcp_json(result):
    if getattr(result, 'isError', False):
        raise ValueError('MCP server returned a tool error')
    structured = getattr(result, 'structuredContent', None)
    if structured is not None:
        return structured
    text = ''.join(item.text for item in result.content if getattr(item, 'type', None) == 'text')
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('MCP tool result is not an object')
    return value

def validate_mcp_doctor(domain, execution, status, restored):
    if not domain.get('data', {}).get('items'):
        raise ValueError('authenticated real Java domain read missing')
    if execution.get('status') != 'completed' or execution.get('stdout', '').strip() != '42':
        raise ValueError('real OpenSandbox computation did not return 42')
    if 'doctor_probe' not in execution.get('saved_names', []) or status.get('version', 0) < 1 or not status.get('data', {}).get('doctor_probe'):
        raise ValueError('Mongo committed doctor state absent')
    if restored.get('status') != 'completed' or restored.get('stdout', '').strip() != '42':
        raise ValueError('OpenSandbox reloaded Mongo state did not return 42')
    return {'passed': True, 'authenticated_java_read': True, 'sandbox_stdout': '42',
            'mongo_version': status['version'], 'mongo_state_reloaded': True}

def validate_business_identity(inspected, served_seed_sha256, config):
    if not re.fullmatch('sha256:[a-f0-9]{64}', config.get('business_image', '')) or inspected.get('Image') != config['business_image']:
        raise ValueError('actual served Java image differs from frozen operator image')
    if served_seed_sha256 != config.get('business_seed_sha256'):
        raise ValueError('actual served Java seed differs from frozen source seed')
    return {'container_id': inspected['Id'], 'image_id': inspected['Image'], 'seed_sha256': served_seed_sha256}

def business_identity(config):
    inspected = json.loads(docker('inspect', config['business_container']))[0]
    seed_sha = docker('exec', config['business_container'], 'sha256sum', '/opt/sourcing/seed.json').split()[0]
    value = validate_business_identity(inspected, seed_sha, config)
    if not inspected.get('State', {}).get('Running') or inspected['HostConfig'].get('PortBindings'):
        raise ValueError('Java business environment not running privately')
    value['app_sha256'] = docker('exec', config['business_container'], 'sha256sum', '/opt/sourcing/app.jar').split()[0]
    return value

def business_read(config, path, params=None):
    from scripts.planning.gdpevo_v4_control import business_http
    response = business_http(config, path, params or {}, os.environ[config['business_token_env']])
    if response['status_code'] != 200:
        raise ValueError('real Java HTTP failed status ' + str(response['status_code']))
    return response['body']

def register_sourcing_read(server, config, trace):
    @server.tool(description=SOURCING_READ_DESCRIPTION)
    def sourcing_read(path: str, params: dict | None = None) -> dict:
        path, params = validate_domain_request(path, params or {})
        value = business_read(config, path, params)
        trace('sourcing_read', {'path': path, 'params': params}, value)
        return value

def mcp_client(config_path, output, owner, thread, network):
    from langchain_mcp_adapters.client import MultiServerMCPClient
    config = read(config_path)
    return MultiServerMCPClient({'business': {'transport': 'stdio', 'command': sys.executable,
        'args': [str(Path(__file__)), 'mcp-worker', '--runtime-config', str(config_path),
                 '--output', str(output), '--owner', owner, '--thread', thread, '--network', network],
        'env': {config['business_token_env']: os.environ[config['business_token_env']], 'PYTHONUTF8': '1',
                'T67_CONTROL_CONTAINER': config['active_control_container']}}})

async def mcp_doctor(config_path, directory, network):
    """No model: same real MCP server and production computation protocol as Actor."""
    owner, thread = 't67-doctor-' + uuid.uuid4().hex[:12], 't67-doctor-' + uuid.uuid4().hex[:12]
    directory.mkdir(parents=True, exist_ok=False)
    client = mcp_client(config_path, directory / 'mcp', owner, thread, network)
    async with client.session('business') as session:
        tools = await session.list_tools()
        actor_tools(tools.tools)
        try:
            description = validate_published_sourcing_tool(tools.tools)
            write(directory / 'published-mcp-tools.json', [tool.model_dump(mode='json') for tool in tools.tools])
            domain = mcp_json(await session.call_tool('sourcing_read', {'path': '/api/sourcing/programs', 'params': {'limit': 1}}))
            execution = mcp_json(await session.call_tool('computation_execute',
                {'code': 'value=6*7; save_state("doctor_probe", {"value": value}); print(value)'}))
            status = mcp_json(await session.call_tool('computation_status', {}))
            restored = mcp_json(await session.call_tool('computation_execute',
                {'code': 'print(load_state("doctor_probe")["value"])', 'read_names': ['doctor_probe']}))
            value = validate_mcp_doctor(domain, execution, status, restored)
            value['sourcing_description'] = description
            write(directory / 'mcp-replies.json', {'domain': domain, 'execution': execution, 'status': status, 'restored': restored})
        finally:
            close = mcp_json(await session.call_tool('finish_session', {}))
            if close.get('recycled') is not True or close.get('container_absent') is not True:
                raise ValueError('explicit MCP doctor cleanup failed')
    value.update(owner=owner, thread=thread, evidence=str(directory), stdio_closed=True,
                 tool_names=['sourcing_read', 'computation_execute', 'computation_status'])
    return value


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
                'retry_policy': RetryPolicy.disabled()})
    return ProxyConfig.from_env({**os.environ, 'OPENSANDBOX_DOMAIN': f"127.0.0.1:{config['sandbox_port']}",
        'OPENSANDBOX_BASE_URL': f"http://127.0.0.1:{config['sandbox_port']}", 'OPENSANDBOX_IMAGE': config['sandbox_image'],
        'OPENSANDBOX_READY_TIMEOUT_SECONDS': '30'})

def isolation_ports(config):
    from urllib.parse import urlparse
    mongo = urlparse(config['mongo_uri'])
    business = urlparse(config['business_url'])
    if mongo.scheme != 'mongodb' or business.scheme not in {'http', 'https'}:
        raise ValueError('probe requires explicit local Mongo and Java HTTP configuration')
    return list(dict.fromkeys([config['sandbox_port'], mongo.port or 27017,
                              business.port or (443 if business.scheme == 'https' else 80), 443]))

def probe_isolation(config, output, network):
    from agent.backends.custom_opensandbox import create_backend
    backend, report = create_backend(sandbox_config(config), metadata={'purpose': 't67-isolation-doctor'})
    try:
        evidence = inspect_sandbox(backend.id, network)
        control = read(output / 'control-runtime.json')
        hosts = [control['management_ip'], control['execution_ip'], 'host.docker.internal', '1.1.1.1']
        ports = isolation_ports(config)
        code = 'hosts=' + repr(hosts) + '; ports=' + repr(ports) + '\n' + '''import json,socket
results=[]
for host in hosts:
 for port in ports:
  s=socket.socket();s.settimeout(.3)
  try: s.connect((host,port));results.append([host,port])
  except OSError: pass
  finally:s.close()
print(json.dumps(results))
'''
        response = backend.execute('python3 -c ' + __import__('shlex').quote(code))
        write(output / 'isolation-probe.json', {'inspection': evidence, 'response': dataclasses.asdict(response), 'hosts': hosts, 'ports': ports})
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
        required = {'model_config', 'env_file', 'mongo_uri', 'sandbox_template', 'sandbox_control_image',
                    'sandbox_port', 'sandbox_image', 'business_url', 'business_token_env',
                    'business_container', 'business_image', 'business_seed_sha256'}
        if not required <= set(config):
            raise ValueError('runtime config missing: ' + ','.join(sorted(required - set(config))))
        protocol = read(output / 'protocol.json')
        identity_keys = ('sandbox_control_image', 'business_image', 'business_seed_sha256')
        if protocol.get('runtime_identity') != {key: config[key] for key in identity_keys}:
            raise ValueError('immutable operator runtime identity was not frozen by prepare')
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
        if not os.environ.get(config['business_token_env']):
            raise ValueError('business auth token missing')
        served = business_identity(config)
        result['checks']['business_identity'] = served
        directory = output / ('doctor-' + uuid.uuid4().hex[:10])
        with isolated_control(config, directory) as network:
            control = read(directory / 'control-runtime.json')
            if control['image_id'] != config['sandbox_control_image']:
                raise ValueError('actual official control image differs from frozen image')
            result['checks']['control_identity'] = control
            active = {**config, 'active_control_container': control['container_name'],
                      'database': 'rush_harness_test_t67_doctor_' + uuid.uuid4().hex[:12]}
            child = directory / 'runtime.json'
            write(child, active)
            health = business_read(active, '/health')
            expected = {'service': 'java-sourcing-erp', 'status': 'ready', 'database': 'up', 'state_mode': 'read_only'}
            if any(health.get('data', {}).get(k) != v for k, v in expected.items()):
                raise ValueError('Java business environment seed/database not ready')
            result['checks']['business_health'] = health
            result['checks']['sandbox_isolation'] = probe_isolation(active, directory, network)
            result['checks']['mcp_protocol'] = asyncio.run(mcp_doctor(child, directory / 'mcp-doctor', network))
            if business_identity(config) != served:
                raise ValueError('served Java boundary changed during doctor')
        cleanup = read(directory / 'control-cleanup.json')
        if cleanup.get('status') != 'passed' or cleanup.get('errors'):
            raise ValueError('trusted control cleanup failed')
        result['checks']['control_cleanup'] = cleanup
        result.update(status='ready', config_sha256=digest(config_path), source_hashes_sha256=digest(output / 'source-hashes.json'))
    except Exception as error:
        from agent.env_utils import redact, secret_values
        result['reasons'].append(type(error).__name__ + ': ' + redact(str(error), secret_values(os.environ))[:600])
    write(output / ('doctor-result-' + uuid.uuid4().hex[:10] + '.json'), result)
    return result


def mcp_worker(config_path, output, owner, thread, network):
    """Owner/thread/control relay are process-bound. Actor never supplies trusted controls."""
    from mcp.server.fastmcp import FastMCP
    from api_view.web_config import MongoResources, PersistenceSettings
    from agent.persistence.repository import ApplicationRepository
    from agent.backends.sandbox_manager import SandboxManager, MongoSandboxRegistry, OpenSandboxFactory
    from agent.planning.computation import ComputationService
    config = read(config_path)
    resources = MongoResources(PersistenceSettings(mongo_uri=config['mongo_uri'], database=config['database'])).start()
    manager = SandboxManager(factory=OpenSandboxFactory(sandbox_config(config), marker='t67-' + owner),
        registry=MongoSandboxRegistry(resources.database), warm_pool_size=0, replenish_in_background=False)
    manager.start()
    service = ComputationService(resources.database, manager.get_or_create)
    ApplicationRepository(resources.database).ensure_thread(owner_user_id=owner, thread_id=thread, title='T67 isolated Actor')
    output.mkdir(parents=True, exist_ok=True)
    closed = False
    sandbox_id = None
    def cleanup():
        nonlocal closed
        if closed:
            return read(output / 'cleanup.json')
        write(output / 'executions.json', list(service.executions.find({'owner_user_id': owner, 'thread_id': thread}, {'_id': 0})))
        recycled = manager.recycle(owner)
        shutdown = manager.shutdown()
        remaining = docker('ps', '-aq', '--filter', 'name=sandbox-' + str(sandbox_id)).strip() if sandbox_id else ''
        value = {'recycled': recycled, 'shutdown': shutdown, 'container_absent': not remaining,
                 'owner': owner, 'thread': thread, 'sandbox_id': sandbox_id}
        write(output / 'cleanup.json', value)
        resources.close()
        closed = True
        return value
    def trace(kind, request, response):
        with (output / 'tools.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps({'kind': kind, 'request': request, 'response': response,
                                 'owner': owner, 'thread': thread}, ensure_ascii=False, default=str) + '\n')
    server = FastMCP('t67-business-computation')
    register_sourcing_read(server, config, trace)
    @server.tool()
    def computation_execute(code: str, read_names: list[str] | None = None, session: str = 'analysis', timeout: int = 60) -> dict:
        """Execute Python in OpenSandbox. load_state/save_state store declared JSON in Mongo."""
        proxy = manager.get_or_create(owner)
        write(output / 'sandbox-inspection.json', inspect_sandbox(proxy.id, network))
        value = service.execute(owner, thread, code, read_names=read_names or [], session=session, timeout=timeout)
        trace('computation_execute', {'code': code, 'read_names': read_names, 'session': session, 'timeout': timeout}, value)
        write(output / 'executions.json', list(service.executions.find({'owner_user_id': owner, 'thread_id': thread}, {'_id': 0})))
        return value
    @server.tool()
    def computation_status(session: str = 'analysis') -> dict:
        """Inspect your committed computation data version and metadata."""
        value = service.status(owner, thread, session=session)
        trace('computation_status', {'session': session}, value)
        return value
    @server.tool()
    def finish_session() -> dict:
        """Operator-only lifecycle cleanup; this tool is filtered out of Actor capabilities."""
        return cleanup()
    try:
        # Normal SDK readiness and actual isolated container exist before any model call.
        proxy = manager.get_or_create(owner)
        sandbox_id = proxy.id
        write(output / 'sandbox-inspection.json', inspect_sandbox(sandbox_id, network))
        server.run('stdio')
    finally:
        cleanup()


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
            from agent.env_utils import redact, secret_values
            with self.path.open('a', encoding='utf-8') as log:
                raw = json.dumps({'kind': kind, 'value': value}, ensure_ascii=False, default=str)
                log.write(redact(raw, secret_values(os.environ)) + '\n')
        def on_chat_model_start(self, serialized, messages, **kwargs):
            self.save('model_input', [[m.model_dump(mode='json') for m in batch] for batch in messages])
        def on_llm_end(self, response, **kwargs):
            self.save('model_output', response.model_dump(mode='json'))
        def on_llm_error(self, error, **kwargs):
            self.save('model_error', type(error).__name__)
        def on_tool_start(self, serialized, input_str, **kwargs):
            self.save('tool_start', {'name': serialized.get('name'), 'input': input_str,
                                    'inputs': kwargs.get('inputs')})
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

async def actor_session(model, config_path, directory, task_input, skill, network, *, kind='solve'):
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend
    from langchain_core.messages import HumanMessage
    from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
    from api_view.web_config import MongoResources, PersistenceSettings
    from agent.persistence.scoped_store import UserScopedStore
    owner, thread = 't67-' + uuid.uuid4().hex[:20], 't67-' + uuid.uuid4().hex[:20]
    config = read(config_path)
    resources = MongoResources(PersistenceSettings(mongo_uri=config['mongo_uri'], database=config['database'])).start()
    directory.mkdir(parents=True)
    write(directory / 'scope.json', {'owner': owner, 'thread': thread, 'network': network, 'kind': kind})
    recorder = recorder_class()(directory / 'raw-trace.jsonl')
    client = mcp_client(config_path, directory / 'mcp', owner, thread, network)
    messages = []
    result = {'owner': owner, 'thread': thread}
    closed = False
    try:
        async with client.session('business') as mcp:
            from langchain_mcp_adapters.tools import load_mcp_tools
            tools = actor_tools(await load_mcp_tools(mcp))
            system = SYSTEM + ('\nTrain-derived skill:\n' + skill if skill else '')
            if kind == 'skill':
                system = SKILL_SYSTEM
            try:
                graph = create_deep_agent(model=model, tools=tools, backend=StateBackend(),
                    checkpointer=resources.checkpointer, store=UserScopedStore(resources.store, owner),
                    system_prompt=system, middleware=[ModelCallLimitMiddleware(run_limit=30, exit_behavior='end'),
                    ToolCallLimitMiddleware(run_limit=36, exit_behavior='end')])
                payload = {'messages': [HumanMessage(content=json.dumps(task_input, ensure_ascii=False))]}
                scope = {'configurable': {'thread_id': thread, 'owner_user_id': owner}, 'recursion_limit': 120, 'callbacks': [recorder]}
                async for state in graph.astream(payload, config=scope, stream_mode='values'):
                    messages = state.get('messages', [])
                    write(directory / 'messages.json', [m.model_dump(mode='json') for m in messages])
                if not messages:
                    raise ValueError('Actor returned no messages')
                content = text_content(messages[-1].content)
                (directory / 'final-content.txt').write_text(content, encoding='utf-8')
                if kind == 'skill':
                    body = normalize_skill_body(content)
                    (directory / 'SKILL.md').write_text(body + '\n', encoding='utf-8')
                    result['skill_sha256'] = digest(directory / 'SKILL.md')
                else:
                    write(directory / 'submission.json', parse_json_answer(content))
                    result['submission'] = str(directory / 'submission.json')
            finally:
                cleanup = mcp_json(await mcp.call_tool('finish_session', {}))
                if cleanup.get('recycled') is not True or cleanup.get('container_absent') is not True:
                    raise ValueError('explicit Actor MCP cleanup failed')
        closed = True
        return result
    finally:
        write(directory / 'stdio-cleanup.json', {'closed': closed, 'owner': owner, 'thread': thread})
        resources.close()

SKILL_SYSTEM = '''Generate a reusable SKILL.md package from the five complete train inputs and
correct answers. Derive transferable procurement procedures and worked fewshot examples.
Use sourcing_read to inspect at least one actual source record and computation_execute to
check a financial relationship before generalizing it. Use only the current supplied train
examples and allowed business tools. No test input/answer, private evaluator, host/source
filesystem or control API is available. Return only SKILL.md markdown starting with YAML
front matter containing name and description. Preserve fact/convention distinctions.'''

def normalize_skill_body(content):
    """Remove one unambiguous output wrapper; never repair or rewrite the actual skill."""
    import yaml
    raw = text_content(content).strip()
    def valid(body):
        match = re.fullmatch(r'---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)(.*)', body, re.S)
        if not match or not match[2].strip():
            return False
        try:
            front = yaml.safe_load(match[1])
        except yaml.YAMLError:
            return False
        return isinstance(front, dict) and bool(front.get('name')) and bool(front.get('description'))
    candidates = [raw] if valid(raw) else []
    lines = raw.splitlines(keepends=True)
    fence = re.compile(r'[ \t]*(`{3,})([A-Za-z0-9_-]*)[ \t]*')
    markers = [fence.fullmatch(line.rstrip('\r\n')) for line in lines]
    openers = []
    for index, marker in enumerate(markers):
        if not marker or marker[2].lower() not in {'markdown', 'md', ''}:
            continue
        following = next((line.strip() for line in lines[index + 1:] if line.strip()), '')
        if following == '---':
            openers.append(index)
    def balanced(inner):
        stack = []
        for marker in inner:
            if marker:
                size, tag = len(marker[1]), marker[2]
                if tag or not stack:
                    stack.append(size)
                elif size >= stack[-1]:
                    stack.pop()
        return not stack
    for number, opening in enumerate(openers):
        boundary = openers[number + 1] if number + 1 < len(openers) else len(lines)
        closes = [index for index in range(opening + 1, boundary) if markers[index]
                  and not markers[index][2] and len(markers[index][1]) >= len(markers[opening][1])]
        if not closes:
            continue
        closing = closes[-1]
        # The last outer close preserves both labelled and unlabeled nested code blocks.
        if not balanced(markers[opening + 1:closing]):
            continue
        body = ''.join(lines[opening + 1:closing]).strip()
        if valid(body):
            candidates.append(body)
    if len(candidates) > 1:
        raise ValueError('multiple valid SKILL candidates; generation is ambiguous')
    if not candidates:
        raise ValueError('generated SKILL content/front matter missing or wrapper incomplete')
    return candidates[0]


def generate_skill(model, directory, supervision, *, config_path, network):
    return asyncio.run(actor_session(model, config_path, directory, supervision, None, network, kind='skill'))


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
    output = value.get('llm_output') or {}
    usage = output.get('token_usage') or output.get('usage')
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

def verify_origin_ledgers(output, run_dir, result, scopes):
    origins = {scope.get('ledger_source', 'current') for scope in scopes}
    if origins - {'current', 'reused-run2'}:
        raise ValueError('unrecognized model ledger origin')
    current = [scope for scope in scopes if scope.get('ledger_source', 'current') == 'current']
    observed = {'current': verify_model_ledger(run_dir, result, current)}
    historical = [scope for scope in scopes if scope.get('ledger_source') == 'reused-run2']
    if historical:
        bound = read(output / 'reuse-source.json')
        source_run = Path(bound['source_stage']) / 'run'
        old_result = read(source_run / 'result.json')
        if result.get('reused_usage') != bound['reused_usage'] or bound['reused_usage'] != old_result['usage']:
            raise ValueError('historical usage differs from bound immutable ledger')
        if result.get('prior_failed_usage') != bound['prior_failed_usage']:
            raise ValueError('prior failed usage differs from bound source')
        old_scopes = [{**scope, 'directory': str(source_run / 'skills' / f"session-{scope.get('source_session', scope['session'])}")}
                      for scope in historical]
        observed['reused-run2'] = verify_model_ledger(source_run, old_result, old_scopes)
        observed['prior_failed_usage'] = bound['prior_failed_usage']
        from scripts.planning.gdpevo_v4_reuse import accounting
        expected_accounting = accounting(result['usage'], result['reused_usage'], result['prior_failed_usage'])
        if result.get('usage_accounting') != expected_accounting:
            raise ValueError('current/reused/prior failed cost accounting differs')
        observed['usage_accounting'] = expected_accounting
    elif read(output / 'protocol.json').get('reuse'):
        raise ValueError('bound reused skill ledger scopes absent')
    return observed

def remaining_skill_sessions(skills):
    if not skills:
        return [1, 2, 3]
    if len(skills) != 2 or {row.get('session') for row in skills} != {1, 2} or any(row.get('ledger_source') != 'reused-run2' for row in skills):
        raise ValueError('continuation requires exactly two bound reused skills')
    return [3]

def verify_skills(output, result):
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
        body = normalize_skill_body(path.read_text(encoding='utf-8'))
        provenance = read(directory / 'provenance.json')
        reused = skill.get('ledger_source') == 'reused-run2'
        if reused:
            from scripts.planning.gdpevo_v4_reuse import verify_reused_skill
            verify_reused_skill(output, directory, skill, normalize_skill_body)
        elif provenance.get('train_only') is not True or provenance.get('supervision_sha256') != digest(output / 'fewshot-input.json'):
            raise ValueError('skill train-only provenance missing')
        events = jsonl(directory / 'raw-trace.jsonl')
        first = next(e['value'][0] for e in events if e.get('kind') == 'model_input')
        human = [m for m in first if m.get('type') == 'human']
        if len(human) != 1 or json.loads(human[0]['content']) != supervision:
            raise ValueError('skill generation did not use exactly frozen train-only examples')
        messages = read(directory / 'messages.json')
        if not messages or messages[-1].get('type') != 'ai':
            raise ValueError('successful skill generation messages absent')
        final = normalize_skill_body(messages[-1].get('content', ''))
        if body != final:
            raise ValueError('SKILL file differs from actual model generation')
        scope = read(directory / 'scope.json')
        if scope.get('kind') != 'skill' or scope.get('owner') != skill.get('owner') or scope.get('thread') != skill.get('thread'):
            raise ValueError('fresh independent skill runtime scope absent')
        verify_mcp_attempt(directory, skill, recovery=output if reused else None)
        scopes.append({**skill, 'directory': str(directory)})
    return scopes

def normalized_tool_request(kind, request):
    if not isinstance(request, dict):
        raise ValueError('MCP callback tool arguments missing')
    if kind == 'sourcing_read':
        path, params = validate_domain_request(request['path'], request.get('params') or {})
        return {'path': path, 'params': params}
    if kind == 'computation_execute':
        return {'code': request['code'], 'read_names': request.get('read_names') or [],
                'session': request.get('session', 'analysis'), 'timeout': request.get('timeout', 60)}
    if kind == 'computation_status':
        return {'session': request.get('session', 'analysis')}
    raise ValueError('forbidden/non-MCP capability in Actor trace')

def verify_mcp_attempt(directory, row, recovery=None):
    from collections import Counter
    import ast
    tools = jsonl(directory / 'mcp/tools.jsonl')
    events = jsonl(directory / 'raw-trace.jsonl')
    starts = [e['value'] for e in events if e.get('kind') == 'tool_start']
    if not starts or len(starts) != len(tools):
        raise ValueError('MCP/callback tool trace counts differ or empty')
    requests, observed = [], []
    for call in tools:
        kind = call.get('kind')
        requests.append((kind, json.dumps(normalized_tool_request(kind, call['request']), sort_keys=True)))
        if call.get('owner') != row.get('owner') or call.get('thread') != row.get('thread'):
            raise ValueError('MCP tool scope differs from independent session')
    for callback in starts:
        arguments = callback.get('inputs')
        if arguments is None:
            try:
                arguments = json.loads(callback.get('input', ''))
            except ValueError:
                arguments = ast.literal_eval(callback.get('input', ''))
        kind = callback.get('name')
        observed.append((kind, json.dumps(normalized_tool_request(kind, arguments), sort_keys=True)))
    if Counter(requests) != Counter(observed):
        raise ValueError('MCP/callback tool arguments differ')
    if not any(t['kind'] == 'sourcing_read' for t in tools):
        raise ValueError('no real domain discovery')
    executions = read(directory / 'mcp/executions.json')
    completed = [e for e in executions if e.get('status') == 'completed']
    if not completed or any(e.get('owner_user_id') != row.get('owner') or e.get('thread_id') != row.get('thread') for e in executions):
        raise ValueError('completed/scoped OpenSandbox computation mechanism absent')
    inspection = read(directory / 'mcp/sandbox-inspection.json')
    if not inspection.get('container_id') or not inspection.get('network_id') or inspection.get('internal') is not True:
        raise ValueError('actual sandbox isolation inspection absent')
    cleanup = read(directory / 'mcp/cleanup.json')
    if cleanup.get('recycled') is not True or cleanup.get('container_absent') is not True:
        raise ValueError('sandbox recycle failed/unverified')
    recovered = False
    if read(directory / 'stdio-cleanup.json').get('closed') is not True:
        if recovery is None:
            raise ValueError('MCP stdio session exit unverified')
        if row.get('ledger_source') != 'reused-run2' or row.get('source_session') != 2 or row.get('status') != 'reused_recovered':
            raise ValueError('historical recovery cannot relax fresh Actor/runtime cleanup')
        from scripts.planning.gdpevo_v4_reuse import verify_reused_skill
        proof = verify_reused_skill(Path(recovery), directory, row, normalize_skill_body)
        if (proof.get('kind') != 'operator-observed-postprocessing-recovery' or proof.get('historical_stdio_closed') is not False
                or proof.get('process_absent') is not True or proof.get('container_absent') is not True
                or proof.get('owner') != row.get('owner') or proof.get('thread') != row.get('thread')
                or proof.get('sandbox_id') != cleanup.get('sandbox_id')):
            raise ValueError('strict historical postprocessing recovery evidence absent')
        recovered = True
    return {'completed_executions': len(completed), 'computation_contribution': 'unassessed',
            'historical_stdio_recovery': recovered}

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
    if protocol.get('reuse'):
        bound = read(output / 'reuse-source.json')
        result['reused_usage'] = bound['reused_usage']
        result['prior_failed_usage'] = bound['prior_failed_usage']
    budget = None
    try:
        if protocol.get('reuse'):
            from scripts.planning.gdpevo_v4_reuse import import_skills
            result['skills'] = import_skills(output, run_dir, normalize_skill_body)
            for imported in result['skills']:
                verify_mcp_attempt(run_dir / 'skills' / f"session-{imported['session']}", imported, recovery=output)
            write(run_dir / 'result.json', result)
        remaining = remaining_skill_sessions(result['skills'])
        with (run_dir / 'model-calls.jsonl').open('x', encoding='utf-8') as log:
            with gateway(original, policy(identity), log, identity.get('model_extra_body')) as (url, budget):
                model = dataclasses.replace(original, base_url=url, api_key='evaluation-proxy', max_tokens=8192).create_chat_model()
                model.max_retries = 0
                register_profile(model)
                with isolated_control(config, run_dir / 'sandbox-control') as network:
                    control = read(run_dir / 'sandbox-control/control-runtime.json')
                    active = {**config, 'active_control_container': control['container_name']}
                    write(run_config, active)
                    result['served_business'] = business_identity(config)
                    result['control_identity'] = control
                    probe_isolation(active, run_dir / 'sandbox-control', network)
                    for repeat in remaining:
                        skill_dir = run_dir / 'skills' / f'session-{repeat}'
                        skill_row = {'session': repeat, 'status': 'started', 'ledger_source': 'current', 'model_calls_before': budget.calls}
                        result['skills'].append(skill_row)
                        write(run_dir / 'result.json', result)
                        try:
                            skill_row.update(generate_skill(model, skill_dir, read(output / 'fewshot-input.json'),
                                                            config_path=run_config, network=network))
                            verify_mcp_attempt(skill_dir, skill_row)
                            write(skill_dir / 'provenance.json', {'train_only': True,
                                  'supervision_sha256': digest(output / 'fewshot-input.json')})
                            skill_row.update(status='generated', path=str(skill_dir / 'SKILL.md'),
                                             sha256=digest(skill_dir / 'SKILL.md'), model_calls_after=budget.calls)
                        except Exception as error:
                            skill_row.update(status='failed', error=failure_details(error, [original.api_key]), model_calls_after=budget.calls)
                            raise
                        finally:
                            write(run_dir / 'result.json', result)
                    failures = 0
                    for entry in protocol['schedule']:
                        task = next(t for t in protocol['tasks'] if t['task_id'] == entry['task_id'])
                        directory = run_dir / 'attempts' / f"r{entry['repeat']}-{entry['task_id']}-{entry['arm']}"
                        row = {**entry, 'status': 'started', 'ledger_source': 'current',
                               'grade': {'score': 0.0, 'points': []}, 'model_calls_before': budget.calls}
                        started = time.monotonic()
                        try:
                            skill = None if entry['arm'] == 'base' else (run_dir / 'skills' / f"session-{entry['repeat']}" / 'SKILL.md').read_text(encoding='utf-8')
                            row.update(asyncio.run(actor_session(model, run_config, directory, actor_input(output, entry['task_id']), skill, network)))
                            try:
                                verify_mcp_attempt(directory, row)
                            except Exception:
                                row['mandatory_evidence_failed'] = True
                                raise
                            row['grade'] = score(task, directory / 'submission.json', Path(protocol['task_group']), directory)
                            row['status'] = 'scored'
                            failures = 0
                        except Exception as error:
                            row.update(status='failed', error=failure_details(error, [original.api_key]))
                            failures += 1
                        row.update(model_calls_after=budget.calls, elapsed_seconds=round(time.monotonic() - started, 3))
                        result['rows'].append(row)
                        write(directory / 'result.json', row)
                        write(run_dir / 'result.json', result)
                        if row.get('mandatory_evidence_failed'):
                            raise RuntimeError('mandatory session mechanism/cleanup evidence failed; stop before more paid calls')
                        if failures >= 2:
                            raise RuntimeError('two consecutive infrastructure/format failures; stop for discussion')
                    if business_identity(config) != result['served_business']:
                        raise ValueError('served Java seed/image changed during matched sessions')
                    result['status'] = 'completed'
                result['control_cleanup'] = read(run_dir / 'sandbox-control/control-cleanup.json')
                if result['control_cleanup']['status'] != 'passed':
                    raise ValueError('trusted control cleanup failed')
    except Exception as error:
        result.update(status='failed', error=failure_details(error, [original.api_key]))
    finally:
        result['usage'] = budget.summary() if budget else {'model_calls': 0, 'actual_cost_cny': None}
        if protocol.get('reuse'):
            from scripts.planning.gdpevo_v4_reuse import accounting
            result['usage_accounting'] = accounting(result['usage'], result['reused_usage'], result['prior_failed_usage'])
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
        runtime_identity = protocol.get('runtime_identity', {})
        if runtime_identity != {key: config[key] for key in ('sandbox_control_image', 'business_image', 'business_seed_sha256')}:
            raise ValueError('run immutable runtime identity differs from frozen prepare')
        control = result.get('control_identity', {})
        if control.get('image_id') != config['sandbox_control_image'] or not re.fullmatch('[a-f0-9]{64}', control.get('source_snapshot_sha256', '')):
            raise ValueError('actual locked official control image/source evidence absent')
        if control.get('server_version') != '0.2.3' or result.get('control_cleanup', {}).get('status') != 'passed':
            raise ValueError('official control version/cleanup not verified')
        served = result.get('served_business', {})
        if served.get('image_id') != config['business_image'] or served.get('seed_sha256') != config['business_seed_sha256']:
            raise ValueError('actual served Java image/seed differs from frozen source')
        if served != checks.get('business_identity'):
            raise ValueError('served Java boundary differs from ready doctor')
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
    session_identities = []
    for skill in result.get('skills', []):
        if skill.get('session') in {1, 2, 3}:
            session_identities.append((skill, run_dir / 'skills' / f"session-{skill['session']}"))
    rescored = {'base': [], 'fewshot': []}
    mechanisms = []
    for row in rows:
        label = f"r{row.get('repeat')}-{row.get('task_id')}-{row.get('arm')}"
        directory = run_dir / 'attempts' / label
        session_identities.append((row, directory))
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
        value['reconciled_usage_by_origin'] = verify_origin_ledgers(output, run_dir, result, scopes)
        value['reconciled_usage'] = value['reconciled_usage_by_origin']['current']
        value['reused_usage'] = result.get('reused_usage')
        value['prior_failed_usage'] = result.get('prior_failed_usage')
        value['usage_accounting'] = result.get('usage_accounting')
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        value['reasons'].append('real model call evidence blocked: ' + str(error))
    try:
        owners, threads, containers = set(), set(), set()
        expected_image = ready['checks']['sandbox_isolation']['image_id']
        for session, folder in session_identities:
            inspector = read(folder / 'mcp/sandbox-inspection.json')
            owner, thread, container = session.get('owner'), session.get('thread'), inspector.get('container_id')
            if not owner or not thread or not container or owner in owners or thread in threads or container in containers:
                raise ValueError('generation/Actor sessions reused owner/thread/sandbox identity')
            if inspector.get('image_id') != expected_image:
                raise ValueError('execution image changed between ready doctor and matched sessions')
            owners.add(owner); threads.add(thread); containers.add(container)
        if len(owners) != 33:
            raise ValueError('all 33 independent runtime session identities missing')
    except (OSError, ValueError, KeyError, TypeError, UnboundLocalError) as error:
        value['reasons'].append('independent runtime identity blocked: ' + str(error))
    value['denominator'] = {'base': 15, 'fewshot': 15, 'rescored': sum(map(len, rescored.values())),
                            'failed_or_unverified_runtime': 30 - len(mechanisms)}
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
    parser.add_argument('--reuse-skills-from', type=Path)
    parser.add_argument('--owner')
    parser.add_argument('--thread')
    parser.add_argument('--network')
    args = parser.parse_args()
    if args.command == 'mcp-worker':
        mcp_worker(args.runtime_config, args.output, args.owner, args.thread, args.network)
        return
    try:
        if args.command == 'prepare':
            value = prepare(args.group, args.output, args.runtime_config, args.reuse_skills_from)
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



