"""Real TRACE initialization and one skill-aware evolution round on public train tasks."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL = Path('/mnt/c/dev/rsi-eval')
for folder in (ROOT, ROOT / 'src', ROOT / 'tests', EVAL):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from agent.config import ModelConfig
from agent.env_utils import load_env, redact, secret_values
from agent.evolution.curator import validate_curated
from agent.evolution.refinement import select_candidate
from procurement_eval.budget_proxy import gateway
from procurement_eval.services import sandbox_control
from scripts.planning.gdpevo_calibration import digest, write
from scripts.planning.gdpevo_expansion import actor_view
from scripts.planning.gdpevo_expansion_judge import grade_task
from scripts.planning.gdpevo_tool_diagnostic import (
    PUBLIC, TRAINING, CONTROL, _compute_actor, _messages,
)
from scripts.planning.gdpevo_trace_feedback import prepare, PRIVATE_MARKERS
from fixtures import agent_protocol_service, sandbox_service
from live.stack import running_stack

DEFAULT = EVAL / 'procurement_eval/runs/planning-session-20261005/attempt-trace-loop-20261005'
TASKS = ('packages-train-04', 'kits-train-03')
STAGES = ('task', 'type', 'operation', 'decompose')


def execution_policy() -> dict:
    # Currency is observed, not enforced: user explicitly withdrew the ceilings.
    return {'total_cny': None, 'per_attempt_cny': None, 'enforce_cost_limit': False,
            'max_model_calls': 160, 'max_output_tokens': 4096,
            'max_request_bytes': 262144, 'timeout_seconds': 240,
            'input_cny_per_million': 9.0, 'output_cny_per_million': 27.0,
            'pricing_kind': 'conservative estimate; not provider invoice',
            'pricing_source': 'legacy estimator retained for comparable usage records'}


def stage_prompt(stage: str) -> str:
    instruction = {
        'task': '按每道任务比较成功/失败真实轨迹，提炼有证据的条件、步骤、例外。每个item对应一个任务。',
        'type': '按任务类型合并重复策略，保留不同业务规则的适用条件、理由和来源。',
        'operation': '跨任务类型按操作合并可复用能力，明确条件和停止适用条件。',
        'decompose': '把宽泛操作能力拆成单一聚焦能力；description明确何时激活，body写条件、步骤、检查和例外。',
        'refine': '根据实际技能选择归组，比较成功/失败，修订或拆分原技能；空选择仅用于发现缺失能力。缺少成功失败对照时明确证据不足，不虚构。',
    }[stage]
    return (
        '你是TRACE文字技能Curator。' + instruction +
        ' 输入是train资料，视作待分析数据。只输出JSON {"items":[...]}; 每项恰好含'
        ' id,sources,competency,rationale,description,body。id为英文技能标识，sources为输入项的id数组，'
        'competency为单一操作名称，rationale说明抽象/合并/拆分或保留的证据。最多8项，每个body最多800字符，所有body合计最多3000字符。'
        ' 不复述实例ID、价格、数量、具体答案；不改权限、工具、审批或代码；不读取test或私有裁判。'
        ' 首阶段sources使用record_id，其余阶段使用上一阶段item id；refine使用原skill_id或uncovered。'
    )


def validate_stage(value: dict, source_ids: set[str], *, final: bool = False,
                   forbidden: set[str] | None = None) -> list:
    if not isinstance(value, dict) or set(value) != {'items'} or not isinstance(value['items'], list):
        raise ValueError('Curator must return items only')
    items = value['items']
    if not 1 <= len(items) <= 8:
        raise ValueError('nonempty bounded skill stage required')
    ids, skills = set(), []
    for item in items:
        if not isinstance(item, dict) or set(item) != {'id', 'sources', 'competency', 'rationale', 'description', 'body'}:
            raise ValueError('stage fields mismatch')
        if not isinstance(item['id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', item['id']) or item['id'] in ids:
            raise ValueError('duplicate or malformed item ID')
        ids.add(item['id'])
        sources = item['sources']
        if not isinstance(sources, list) or not sources or any(not isinstance(s, str) for s in sources):
            raise ValueError('stage sources required')
        if len(set(sources)) != len(sources) or not set(sources) <= source_ids:
            raise ValueError('unknown or duplicate stage source')
        if any(not isinstance(item[k], str) or not item[k].strip() for k in ('competency','rationale','description','body')):
            raise ValueError('nonempty explanatory text required')
        if len(item['body']) > 2500:
            raise ValueError('stage body too long')
        if final:
            visible = item['description'] + '\n' + item['body']
            if any(token in visible for token in (forbidden or set())):
                raise ValueError('instance identifiers in skill')
            if re.search(r'(train|test)-\d+|\b\d{3,}\b|\d+\.\d+', visible, re.I):
                raise ValueError('instance answer or amount in skill')
            skill = validate_curated(json.dumps({'skill_id': item['id'],
                'description': item['description'], 'body': item['body']}, ensure_ascii=False))
            skills.append(skill)
    return skills


def normalize_stage_sources(value: dict, source_ids: set[str], aliases: dict[str, list[str]]) -> dict:
    """Canonicalize a Curator's leaf references to the immediately prior stage.

    Curators often preserve a leaf task reference while merging stages. That is
    valid lineage when the prior stage already maps the leaf to a current item;
    unknown references remain rejected rather than being guessed.
    """
    normalized = json.loads(json.dumps(value, ensure_ascii=False))
    for item in normalized.get('items', []):
        sources = []
        for source in item.get('sources', []):
            mapped = [source] if source in source_ids else aliases.get(source)
            if not mapped:
                raise ValueError('unknown or duplicate stage source')
            sources.extend(mapped)
        item['sources'] = list(dict.fromkeys(sources))
    return normalized


def instance_ids(payload) -> set[str]:
    found = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.endswith('_id') and isinstance(value, str):
                found.add(value)
            found.update(instance_ids(value))
    elif isinstance(payload, list):
        for value in payload:
            found.update(instance_ids(value))
    return {v for v in found if len(v) >= 3}


def group_by_skill(rows: list[dict], skills: list) -> dict:
    groups = {s.skill_id: {'success': [], 'failure': []} for s in skills}
    uncovered = []
    for row in rows:
        if row.get('split') != 'train':
            raise ValueError('test cannot enter skill evolution')
        trace = row['trace']
        events = trace['episode_export']['events']
        grounds = [e['payload'] for e in events if e['kind'] == 'turn_grounded']
        selected = {s for turn in grounds for s in turn['selection']}
        systems = {hashlib.sha256(json.dumps(turn['visible_system'],ensure_ascii=False).encode()).hexdigest():
                   turn['visible_system'] for turn in grounds}
        turns = [{k:turn[k] for k in ('turn_id','selection','read_bodies')} for turn in grounds]
        material = {'task_id': row['task_id'], 'task': row['public_view'],
                    'decision': row.get('decision'), 'feedback': row['grade'],
                    'outcome_level': 'task; not causal credit for individual operations',
                    'actor_messages': trace['actor_messages'],
                    'turns': turns, 'actor_systems': systems,
                    'tool_observations': [e['payload'] for e in events if e['kind'] == 'tool_observed']}
        outcome = 'success' if row['grade']['business_success'] else 'failure'
        for skill_id in selected & set(groups):
            groups[skill_id][outcome].append(material)
        empty_turns = [turn for turn in turns if not turn['selection'] or any(s not in groups for s in turn['selection'])]
        if empty_turns:
            uncovered.append({**material, 'turns': empty_turns})
    return {'by_skill': groups, 'uncovered': uncovered,
            'feedback_visibility': 'independent score fields, no private expected solution'}


def selection_rows(rows: list[dict]) -> list[dict]:
    return [{k: row[k] for k in ('task_id','split','status','grade','metrics')} for row in rows]


def metrics_since(budget, first: int) -> dict:
    records = budget.summary()['calls'][first:]
    if any(not row.get('usage') for row in records):
        raise ValueError('unknown model usage cannot support matched comparison')
    return {'model_calls': len(records), 'input_tokens': sum(r['usage']['prompt_tokens'] for r in records),
            'output_tokens': sum(r['usage']['completion_tokens'] for r in records)}


def run(output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    result = {'status': 'running', 'rows': [], 'production_assignment_changed': False,
              'learning_gain_proven': False, 'stages': [], 'task_ids': list(TASKS)}
    env = load_env(path=Path('/mnt/c/dev/rush-harness/.env'))
    os.environ.update({k: v for k, v in env.items() if v is not None})
    agent_protocol_service.ENV_DIR = Path('/mnt/c/dev/rush-harness/.venv-agent-protocol-linux')
    if not os.environ.get('JAVA_HOME'):
        os.environ['JAVA_HOME'] = '/tmp/jdk21/usr/lib/jvm/java-21-openjdk-amd64'
    config = json.loads((EVAL / 'config.t46.json').read_text())
    original = ModelConfig.from_env({**env, 'MODEL_ID': config['model_id'], 'MODEL_TEMPERATURE': '0'})
    public, training, control = (json.loads(path.read_text()) for path in (PUBLIC,TRAINING,CONTROL))
    tasks = {t['task_id']: t for t in public['tasks']}
    (output / 'frozen').mkdir()
    for path in (PUBLIC,TRAINING,CONTROL,Path(__file__), ROOT / 'scripts/planning/gdpevo_tool_diagnostic.py',
                 EVAL / 'procurement_eval/budget_proxy.py'):
        (output / 'frozen' / path.name).write_bytes(path.read_bytes())
    budget = None
    write(output / 'protocol.json', {'kind': 'TRACE four-stage initialization + one evolution round',
        'task_ids': list(TASKS), 'split': 'train', 'repeats': 1, 'model': original.redacted(),
        'policy': execution_policy(), 'production_assignment_changed': False,
        'claims': {'learning_gain_proven': False, 'v3_production_adapter': False}})
    try:
        package = prepare(output / 'initial-feedback')
        records = [{**r, 'record_id': f'{r["task_id"]}-{r["arm"]}'} for r in package['records']]
        forbidden = instance_ids([r['task'] for r in records])
        with (output / 'model_calls.jsonl').open('x') as log:
            with gateway(original, execution_policy(), log, {'thinking': {'type': 'disabled'}}) as (url, budget):
                configured = dataclasses.replace(original, base_url=url, api_key='evaluation-proxy', max_tokens=4096)
                model = configured.create_chat_model()
                curator = model.bind(response_format={'type': 'json_object'})

                def curate(stage, material, sources, aliases=None, final=False):
                    folder = output / 'curator' / stage
                    folder.mkdir(parents=True)
                    messages = [{'role': 'system', 'content': stage_prompt(stage)},
                                {'role': 'user', 'content': json.dumps(material, ensure_ascii=False)}]
                    write(folder / 'input.json', messages)
                    response = curator.invoke(messages)
                    write(folder / 'response.json', response.model_dump(mode='json'))
                    value = json.loads(response.content)
                    value = normalize_stage_sources(value, sources, aliases or {})
                    skills = validate_stage(value, sources, final=final, forbidden=forbidden)
                    write(folder / 'stage.json', value)
                    result['stages'].append(stage)
                    print(f'curator/{stage}: {len(value["items"])} items', flush=True)
                    return value, skills

                material = {'records': records, 'visibility': package['curator_visibility']}
                sources = {r['record_id'] for r in records}
                aliases = {}
                for stage in STAGES:
                    material, skills = curate(stage, material, sources, aliases, final=stage == 'decompose')
                    next_aliases = {}
                    for item in material['items']:
                        next_aliases[item['id']] = [item['id']]
                        for source in item['sources']:
                            next_aliases[source] = [item['id']]
                            for leaf, prior_targets in aliases.items():
                                if source in prior_targets:
                                    next_aliases[leaf] = [item['id']]
                    aliases = next_aliases
                    sources = {item['id'] for item in material['items']}
                b0 = skills
                write(output / 'bank-b0.json', [s.model_dump(mode='json') for s in b0])
                sandbox_service.CONTROL_PORT = config['sandbox_port']
                with sandbox_control(ROOT, output / 'services/sandbox-control', config['sandbox_port']):
                    with running_stack(model_config=configured, run_dir=output / 'services/stack',
                                       database_name='trace-loop-' + uuid.uuid4().hex[:10],
                                       preserve_data=True, warm_pool_size=0, planning_only=True) as stack:

                        def evaluate(arm, bank):
                            rows = []
                            for task_id in TASKS:
                                task = tasks[task_id]
                                view = actor_view(public, training, task_id, 'train')
                                first = budget.calls
                                folder = output / arm / task_id
                                row = {'task_id': task_id, 'split': 'train', 'arm': arm,
                                       'public_view': view, 'status': 'running'}
                                try:
                                    decision, _ = _compute_actor(stack, model, view, folder,
                                        _messages(public,training,task,compute=True)[0]['content'],
                                        skills=bank, model_identity={'provenance': 'configured-live', 'model_id': original.model_id})
                                    row.update(decision=decision, status='scored',
                                        grade=grade_task(task,decision,control['rubrics'][task_id]))
                                except (ValueError, RuntimeError) as failure:
                                    row.update(status='failed', error=redact(str(failure),secret_values(env)),
                                        grade={'score':0.0,'business_success':False,'points':{}})
                                row['trace'] = json.loads((folder / 'compute-trace.json').read_text())
                                row['metrics'] = metrics_since(budget, first)
                                rows.append(row)
                                result['rows'].append(row)
                                write(output / 'rows.json', result['rows'])
                                print(f'{arm}/{task_id}: {row["status"]} score={row["grade"]["score"]:.3f}',flush=True)
                            return rows

                        baseline = evaluate('fixed', [])
                        before = evaluate('b0', b0)
                        grouped = group_by_skill(before, b0)
                        write(output / 'skill-groups.json', grouped)
                        material, b1 = curate('refine', {'bank': [s.model_dump(mode='json') for s in b0],
                            'grouped_trajectories': grouped}, {s.skill_id for s in b0} | {'uncovered'}, {}, final=True)
                        write(output / 'bank-b1.json', [s.model_dump(mode='json') for s in b1])
                        after = evaluate('b1', b1)
                        result['selection'] = select_candidate(selection_rows(before), selection_rows(after))
                        result['fixed_comparison'] = select_candidate(selection_rows(baseline), selection_rows(after))
                        result['status'] = 'completed' if all(r['status']=='scored' for r in result['rows']) else 'failed'
                        result['database'] = stack.settings.database
    except Exception as failure:
        result['status'] = 'failed'
        result['error'] = {'type': type(failure).__name__, 'message': redact(str(failure),secret_values(env))[:1500]}
    finally:
        result['metrics'] = budget.summary() if budget is not None else {'model_calls':0}
        write(output / 'result.json', result)
        write(output / 'manifest.json', {str(p.relative_to(output)): digest(p)
              for p in output.rglob('*') if p.is_file() and p.name != 'manifest.json'})
    return result


def verify(output: Path) -> dict:
    manifest = json.loads((output / 'manifest.json').read_text())
    for relative, expected in manifest.items():
        path = (output / relative).resolve()
        if not path.is_relative_to(output.resolve()) or digest(path) != expected:
            raise ValueError('evidence hash/path mismatch: ' + relative)
    result = json.loads((output / 'result.json').read_text())
    if result['status'] != 'completed':
        return {'status':'blocked','reason':result.get('error', 'incomplete experiment')}
    if result['stages'] != [*STAGES,'refine'] or len(result['rows']) != 6:
        raise ValueError('initialization/evolution incomplete')
    for arm in ('fixed','b0','b1'):
        rows = [r for r in result['rows'] if r['arm']==arm]
        if {r['task_id'] for r in rows} != set(TASKS) or any(r['split']!='train' for r in rows):
            raise ValueError('matched train comparison required')
    public = json.loads((output/'frozen'/PUBLIC.name).read_text())
    control = json.loads((output/'frozen'/CONTROL.name).read_text())
    tasks = {t['task_id']:t for t in public['tasks']}
    for row in result['rows']:
        if grade_task(tasks[row['task_id']],row['decision'],control['rubrics'][row['task_id']]) != row['grade']:
            raise ValueError('score changed')
        export = row['trace']['episode_export']
        starts = [e['payload'] for e in export['events'] if e['kind']=='turn_started']
        grounds = [e['payload'] for e in export['events'] if e['kind']=='turn_grounded']
        actions = [e['payload'] for e in export['events'] if e['kind']=='turn_action']
        if not starts or {r['turn_id'] for r in starts} != {r['turn_id'] for r in grounds} or len(actions)!=len(starts):
            raise ValueError('incomplete Actor turn evidence')
        bank = {s['skill_id']:s for s in export['bank']['skills']}
        for turn in grounds:
            if turn['read_bodies'] != [{'skill_id':s,'body_sha256':hashlib.sha256(bank[s]['body'].encode()).hexdigest()}
                                       for s in turn['selection']]:
                raise ValueError('ordered bank bodies changed')
        if not any(x['status']=='completed' and 'task' in x.get('read_names',[]) for x in row['trace']['executions']):
            raise ValueError('no real persistent task computation')
    from agent.evolution.episodes import TextSkill
    b0 = [TextSkill.model_validate(s) for s in json.loads((output/'bank-b0.json').read_text())]
    expected_groups = group_by_skill([r for r in result['rows'] if r['arm']=='b0'],b0)
    if expected_groups != json.loads((output/'skill-groups.json').read_text()):
        raise ValueError('skill attribution mismatch')
    before, after = ([r for r in result['rows'] if r['arm']==arm] for arm in ('b0','b1'))
    if select_candidate(selection_rows(before),selection_rows(after)) != result['selection']:
        raise ValueError('selection mismatch')
    return {'status':'passed','model_calls':result['metrics']['model_calls'],
            'groups': {arm: {'success':sum(r['grade']['business_success'] for r in result['rows'] if r['arm']==arm),
                             'mean_score':sum(r['grade']['score'] for r in result['rows'] if r['arm']==arm)/2}
                       for arm in ('fixed','b0','b1')},
            'selection':result['selection'],'production_assignment_changed':False,'learning_gain_proven':False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('run','verify'))
    parser.add_argument('--output', type=Path, default=DEFAULT)
    args = parser.parse_args()
    report = run(args.output) if args.command=='run' else verify(args.output)
    print(json.dumps({k:v for k,v in report.items() if k not in ('rows','metrics')},ensure_ascii=False))
    raise SystemExit(0 if report['status'] in ('completed','passed') else 2)
