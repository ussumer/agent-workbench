"""Bounded v3 text-vs-computation diagnostic.

The grader and task controls stay outside the Actor.  The compute arm receives only
the public train view and a durable JSON copy of that view in ComputationService;
the Actor must write its own Python and use load_state/save_state.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVAL = Path('/mnt/c/dev/rsi-eval')
for folder in (ROOT, ROOT / 'src', EVAL):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from deepagents import create_deep_agent  # noqa: E402
from deepagents.backends import StateBackend  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402

from agent.config import ModelConfig  # noqa: E402
from agent.env_utils import load_env, redact, secret_values  # noqa: E402
from agent.planning.computation import ComputationService  # noqa: E402
from agent.persistence.repository import ApplicationRepository  # noqa: E402
from agent.persistence.scoped_store import UserScopedStore  # noqa: E402
from agent.tools.planning_computation import build_computation_tools  # noqa: E402
from fixtures import agent_protocol_service, erp_service, mcp_service, mongo_service, sandbox_service  # noqa: E402
from procurement_eval.services import sandbox_control  # noqa: E402
from live.stack import running_stack  # noqa: E402
from procurement_eval.budget_proxy import gateway  # noqa: E402
from procurement_eval.core import archive_source, fingerprint  # noqa: E402
from scripts.planning.gdpevo_calibration import digest, write  # noqa: E402
from scripts.planning.gdpevo_expansion import actor_view  # noqa: E402
from scripts.planning.gdpevo_expansion_judge import grade_task  # noqa: E402
from scripts.planning.gdpevo_v3_training import actor_messages  # noqa: E402
from scripts.planning.live_baseline import reserve_attempt, settle_attempt  # noqa: E402

PUBLIC = ROOT / 'fixtures/planning/gdpevo-procurement-v3.json'
TRAINING = ROOT / 'fixtures/planning/gdpevo-procurement-v3-training.json'
CONTROL = ROOT / 'fixtures/planning/private/gdpevo-procurement-v3-control.json'
DEFAULT_OUTPUT = EVAL / 'procurement_eval/runs/planning-session-20261005/attempt-v3-tool-diagnostic-20261005'
OWNER = 'diagnostic-v3'


def _messages(public: dict, training: dict, task: dict, *, compute: bool) -> list[dict[str, str]]:
    messages = actor_messages(public, training, task, 'fixed-v3')
    if compute:
        messages[0]['content'] += (
            '\n本实验允许使用 computation_status/computation_execute。任务公开资料已保存为名为 task 的持久JSON；'
            '先用 read_names=["task"] 的 computation_execute 读取它，再由你自己编写Python计算候选、预算和约束。'
            '不得假设有固定planner，不得读取私有裁判；计算成功后仍必须输出完整JSON。'
        )
    return messages


def _parse(content: Any) -> dict:
    if isinstance(content, list):
        content = ''.join(str(part.get('text', part)) if isinstance(part, dict) else str(part) for part in content)
    if not isinstance(content, str):
        raise ValueError('model output is not text')
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError('decision must be an object')
    return value


def _seed(service: ComputationService, owner: str, thread: str, task: dict) -> dict:
    code = 'save_state("task", ' + repr(task) + ')'
    return service.execute(owner, thread, code, operation_id=uuid.uuid4().hex)


def _compute_actor(stack: Any, model: Any, task_view: dict, output: Path) -> tuple[dict, dict]:
    output.mkdir(parents=True, exist_ok=True)
    thread = 'v3-compute-' + uuid.uuid4().hex[:12]
    ApplicationRepository(stack.database).ensure_thread(owner_user_id=OWNER, thread_id=thread,
                                                       title='v3 computation diagnostic')
    service = ComputationService(stack.database, stack.manager.get_or_create)
    seed = _seed(service, OWNER, thread, task_view)
    graph = create_deep_agent(
        model=model,
        tools=build_computation_tools(service),
        backend=StateBackend(),
        checkpointer=stack.resources.checkpointer,
        store=UserScopedStore(stack.store, OWNER),
        system_prompt='你是采购计算诊断Actor。遵守系统提示，只用公开task和计算工具。',
    )
    config = {'configurable': {'thread_id': thread, 'owner_user_id': OWNER}}
    result = graph.invoke({'messages': [HumanMessage(content=json.dumps(task_view, ensure_ascii=False))]}, config=config)
    content = result['messages'][-1].content
    decision = _parse(content)
    executions = list(stack.database.planning_kernel_executions.find(
        {'owner_user_id': OWNER, 'thread_id': thread}, {'_id': 0}))
    evidence = {'thread_id': thread, 'seed': seed, 'executions': executions,
                'actor_messages': [m.model_dump(mode='json') if hasattr(m, 'model_dump') else m
                                   for m in result['messages']],
                'decision': decision}
    write(output / 'compute-trace.json', evidence)
    return decision, {'thread_id': thread, 'seed': seed, 'executions': executions,
                      'message_count': len(result['messages'])}


def run(output: Path = DEFAULT_OUTPUT, *, task_ids: tuple[str, ...] = ('packages-train-04', 'kits-train-03')) -> dict:
    if output.exists():
        raise FileExistsError(output)
    env = load_env(path=Path('/mnt/c/dev/rush-harness/.env'))
    os.environ.update({k: v for k, v in env.items() if v is not None})
    public, training, control = (json.loads(path.read_text()) for path in (PUBLIC, TRAINING, CONTROL))
    tasks = {task['task_id']: task for task in public['tasks']}
    selected = [tasks[task_id] for task_id in task_ids]
    if any(task['split'] != 'train' for task in selected):
        raise ValueError('diagnostic only accepts train tasks')
    output.mkdir(parents=True)
    (output / 'frozen').mkdir()
    for path in (PUBLIC, TRAINING, CONTROL, Path(__file__)):
        (output / 'frozen' / path.name).write_bytes(path.read_bytes())
    model_config = ModelConfig.from_env({**env, 'MODEL_ID': json.loads((EVAL / 'config.t46.json').read_text())['model_id'],
                                         'MODEL_TEMPERATURE': '0'})
    reservation = reserve_attempt(EVAL / 'procurement_eval/runs/planning-session-20261004/budget-ledger.json', 2.0, 50.0)
    log_path = output / 'model_calls.jsonl'
    attempt = {'kind': 'v3-text-vs-opensandbox', 'owner': OWNER, 'model_id': model_config.model_id,
               'task_ids': list(task_ids), 'arms': ['text', 'compute'], 'model_calls': 0,
               'production_assignment_changed': False, 'model_identity': model_config.redacted(),
               'status': 'running', 'reservation_id': reservation['reservation_id'], 'rows': []}
    budget = None
    try:
        configuration = json.loads((EVAL / 'config.t46.json').read_text())
        sandbox_service.CONTROL_PORT = configuration['sandbox_port']
        run_dir = output / 'services'
        proxy_spec = {'total_cny': 50.0, 'per_attempt_cny': 2.0, 'max_model_calls': 4,
                      'max_output_tokens': 4096, 'max_request_bytes': 131072, 'timeout_seconds': 240,
                      'input_cny_per_million': 9.0, 'output_cny_per_million': 27.0,
                      'pricing_kind': 'conservative DeepSeek estimate', 'pricing_source': 'configured evaluation policy'}
        with log_path.open('x') as log:
            with gateway(model_config, proxy_spec, log, {'thinking': {'type': 'disabled'}}) as (url, budget):
                model = dataclasses.replace(model_config, base_url=url, api_key='evaluation-proxy', max_tokens=4096)
                with sandbox_control(ROOT, run_dir / 'sandbox-control', configuration['sandbox_port']):
                    with running_stack(model_config=model, run_dir=run_dir / 'stack', database_name='v3-tool-diagnostic',
                                       preserve_data=True, warm_pool_size=0, planning_only=True) as stack:
                        for task in selected:
                            task_view = actor_view(public, training, task['task_id'], 'train')
                            for arm in ('text', 'compute'):
                                row = {'task_id': task['task_id'], 'arm': arm, 'model_calls_before': budget.calls}
                                if arm == 'text':
                                    response = model.invoke(_messages(public, training, task, compute=False))
                                    decision = _parse(response.content)
                                    row['messages'] = _messages(public, training, task, compute=False)
                                else:
                                    decision, trace = _compute_actor(stack, model, task_view, output / f'{task["task_id"]}-{arm}')
                                    row['trace'] = trace
                                    row['messages'] = _messages(public, training, task, compute=True)
                                row['decision'] = decision
                                row['grade'] = grade_task(task, decision, control['rubrics'][task['task_id']])
                                row['model_calls_after'] = budget.calls
                                attempt['rows'].append(row)
                                write(output / 'rows.json', attempt['rows'])
                        attempt['status'] = 'completed'
                        attempt['stack_database'] = stack.settings.database
                        attempt['erp_orders_before'] = []
                        attempt['erp_orders_after'] = []
                        attempt['runtime_source'] = fingerprint(ROOT)
    except Exception as failure:
        attempt['status'] = 'failed'
        attempt['errors'] = [{'type': type(failure).__name__, 'message': redact(str(failure), secret_values(env))[:1000]}]
    finally:
        attempt['model_calls'] = budget.calls if budget is not None else 0
        attempt['metrics'] = budget.summary() if budget is not None else {'model_calls': 0, 'reserved_upper_cny': 0.0}
        settle_attempt(EVAL / 'procurement_eval/runs/planning-session-20261004/budget-ledger.json', reservation['reservation_id'],
                       attempt['metrics'], 50.0)
        write(output / 'result.json', attempt)
        write(output / 'manifest.json', {str(path.relative_to(output)): digest(path) for path in output.rglob('*')
                                         if path.is_file() and path.name != 'manifest.json'})
    return attempt


def verify(attempt: Path) -> dict:
    result = json.loads((attempt / 'result.json').read_text())
    manifest = json.loads((attempt / 'manifest.json').read_text())
    for relative, expected in manifest.items():
        if digest(attempt / relative) != expected:
            raise ValueError('evidence hash mismatch: ' + relative)
    if result['status'] != 'completed' or len(result['rows']) != 4:
        return {'status': 'blocked', 'reason': 'diagnostic incomplete', 'model_calls': result.get('model_calls', 0),
                'learning_gain_proven': False}
    for row in result['rows']:
        if row['arm'] == 'compute':
            executions = row['trace']['executions']
            if not any(x.get('status') == 'completed' for x in executions):
                raise ValueError('compute arm lacks completed OpenSandbox execution')
    return {'status': 'passed', 'rows': len(result['rows']), 'model_calls': result['model_calls'],
            'groups': {arm: {'mean_score': sum(r['grade']['score'] for r in result['rows'] if r['arm'] == arm) / 2,
                             'business_success': sum(r['grade']['business_success'] for r in result['rows'] if r['arm'] == arm)}
                       for arm in ('text', 'compute')}, 'learning_gain_proven': False,
            'production_assignment_changed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('run', 'verify'))
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(run(args.output) if args.command == 'run' else verify(args.output), ensure_ascii=False))
