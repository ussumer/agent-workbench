"""Zero-model protocol checks using the real four scoped Curator outputs."""
import hashlib
import json
from copy import deepcopy

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from agent.evolution.episodes import EvidenceError
from agent.evolution.orchestration import PlanningTraceMiddleware
from scripts.planning.gdpevo_dynamic import export_bank
from scripts.planning.gdpevo_v3_scoped import GROUPS, SESSION
from scripts.planning.live_baseline import load_skill_bank


def write_bank(tmp_path, payload):
    path = tmp_path / 'bank.json'
    path.write_text(json.dumps(payload, ensure_ascii=False))
    return path


def test_real_scoped_bank_exports_all_four_exact_bodies(tmp_path):
    payload = export_bank(SESSION, tmp_path / 'export.json')
    skills = load_skill_bank(tmp_path / 'export.json')
    assert {s.skill_id for s in skills} == {'planning_' + g + '_strategy' for g in GROUPS}
    original = json.loads((SESSION / 'skills.json').read_text())
    assert {s.skill_id: s.body for s in skills} == {s['skill_id']: s['body'] for s in original.values()}
    assert payload['production_assignment_changed'] is False


@pytest.mark.parametrize('change', ('body', 'duplicate', 'scope', 'production'))
def test_bank_refuses_corrupt_or_unsafe_input(tmp_path, change):
    payload = export_bank(SESSION, tmp_path / 'export.json')
    if change == 'body':
        payload['skills'][0]['body'] += 'tampered'
    elif change == 'duplicate':
        payload['skills'].append(deepcopy(payload['skills'][0]))
    elif change == 'scope':
        payload['skills'][0]['scope'] = 'course'
    else:
        payload['production_assignment_changed'] = True
    with pytest.raises(ValueError):
        load_skill_bank(write_bank(tmp_path, payload))


class EvidenceFixture:
    """A declared recording fixture; this test does not claim Mongo persistence."""
    def __init__(self, skills):
        self.skills = [{**s.model_dump(), 'body_sha256': hashlib.sha256(s.body.encode()).hexdigest()}
                       for s in skills]
        self.events = []

    def episode(self, owner, episode):
        return {'thread_id': 'thread', 'run_ids': ['run'], 'bank_id': 'bank', 'bank_sha256': 'hash'}

    def bank(self, owner, bank):
        return {'_id': 'bank', 'sha256': 'hash', 'skills': self.skills}

    def append(self, owner, episode, run, kind, payload):
        self.events.append((kind, payload))


class RequestFixture:
    def __init__(self, messages, system=None):
        self.messages = messages
        self.system_message = system or SystemMessage(content='fixed base prompt')
        self.tools = []

    def override(self, **kwargs):
        return RequestFixture(self.messages, kwargs['system_message'])


def test_each_turn_uses_new_observations_and_drops_previous_bodies(tmp_path):
    export_bank(SESSION, tmp_path / 'export.json')
    skills = load_skill_bank(tmp_path / 'export.json')
    store = EvidenceFixture(skills)
    middleware = PlanningTraceMiddleware(store, None)
    config = {'configurable': {'owner_user_id': 'owner', 'thread_id': 'thread',
                              'planning_episode_id': 'episode', 'application_run_id': 'run'}}
    request = RequestFixture([HumanMessage(content='compare quote and package constraints')])
    first = middleware.prepare(request, config)
    selected = [skills[1].skill_id, skills[0].skill_id]
    grounded = middleware.ground(request, first, json.dumps(selected))
    content = grounded.system_message.content
    assert content.index(skills[1].body) < content.index(skills[0].body)
    observation = ToolMessage(content='new version: computation failed; committed state unchanged',
                              tool_call_id='computation-one')
    request.messages.append(observation)
    second = middleware.prepare(request, config)
    final = middleware.ground(request, second, '[]')
    assert final.system_message.content == 'fixed base prompt'
    assert request.system_message.content == 'fixed base prompt'
    starts = [payload for kind, payload in store.events if kind == 'turn_started']
    assert len(starts) == 2 and starts[0]['turn_id'] != starts[1]['turn_id']
    assert len(starts[1]['catalog']) == 4
    assert 'committed state unchanged' in str(starts[1]['selector_input'])
    records = [payload for kind, payload in store.events if kind == 'turn_grounded']
    assert records[0]['selection'] == selected and records[1]['read_bodies'] == []
    assert records[0]['read_bodies'][0]['body_sha256'] == hashlib.sha256(skills[1].body.encode()).hexdigest()


@pytest.mark.parametrize('selection', ('["missing"]', '["a","a"]', '{}', 'oops'))
def test_bad_selection_never_creates_grounded_actor_request(tmp_path, selection):
    export_bank(SESSION, tmp_path / 'export.json')
    store = EvidenceFixture(load_skill_bank(tmp_path / 'export.json'))
    middleware = PlanningTraceMiddleware(store, None)
    request = RequestFixture([])
    prepared = ('owner', 'episode', 'run', store.bank('owner', 'bank'), 'turn', [])
    with pytest.raises(EvidenceError):
        middleware.ground(request, prepared, selection)
    assert store.events == []


def test_setup_failure_is_archived_and_releases_budget(tmp_path, monkeypatch):
    from scripts.planning import live_baseline as runner

    settlements = []
    monkeypatch.setattr(runner, 'reserve_attempt', lambda *args: {'reservation_id': 'setup-test'})
    monkeypatch.setattr(runner, 'settle_attempt', lambda *args: settlements.append(args))
    monkeypatch.setattr(runner, 'load_env', lambda **kwargs: {})
    def fail_config(*args, **kwargs):
        raise ValueError('missing configured model')
    monkeypatch.setattr(runner.ModelConfig, 'from_env', fail_config)
    config = tmp_path / 'config.json'
    config.write_text('{"model_id":"configured-model"}')
    output = tmp_path / 'failed-setup'
    assert runner.run(output, config_path=config) == 1
    result = json.loads((output / 'result.json').read_text())
    assert result['terminal_status'] == 'failed' and result['model_calls'] == 0
    assert result['errors'][0]['message'] == 'missing configured model'
    assert settlements[0][2]['reserved_upper_cny'] == 0
    manifest = json.loads((output / 'evidence-manifest.json').read_text())
    assert 'result.json' in manifest and 'started.json' in manifest


def test_unfinished_real_actor_cannot_pass_evidence_gate():
    from scripts.planning.gdpevo_dynamic_evidence import ATTEMPT, verify

    report = verify(ATTEMPT)
    assert report['status'] == 'blocked' and report['real_actor_verified'] is False
    assert report['completed_turn_protocol_verified'] is True
    assert report['runtime_completed'] is False and report['model_calls'] == 16
    assert report['learning_gain_proven'] is False


def test_archived_budget_failure_keeps_stream_cause_without_proposal():
    from scripts.planning.gdpevo_dynamic_evidence import ATTEMPT
    from scripts.planning.live_baseline import finalize_planning_result

    historical = json.loads((ATTEMPT / 'actor/result.json').read_text())
    attempt = {'frames': historical['frames'], 'errors': [], 'terminal_status': 'failed'}
    assert historical['final_goal']['proposal'] is None
    with pytest.raises(RuntimeError, match='OPENAIRATELIMITERROR'):
        finalize_planning_result(attempt, historical['final_goal'])
    assert attempt['primary_failure']['origin'] == 'planning_stream'
    assert 'configured model budget exhausted' in attempt['errors'][0]['message']
    assert attempt['final_goal'] == historical['final_goal']
    assert 'final_judge' not in attempt


def test_completed_stream_without_proposal_is_still_failure():
    from scripts.planning.live_baseline import finalize_planning_result

    attempt = {'frames': [[{'event': 'done', 'envelope': {'payload': {'status': 'completed'}}}]],
               'errors': []}
    with pytest.raises(RuntimeError, match='without a final proposal'):
        finalize_planning_result(attempt, None)
    assert attempt.get('terminal_status') != 'completed'


@pytest.mark.parametrize('selector', (False, True))
def test_request_budget_allows_full_planned_actor_turns_and_then_stops(selector):
    from io import StringIO
    from procurement_eval.budget_proxy import Budget
    from scripts.planning.live_baseline import request_budget

    spec = request_budget(actor_turn_allowance=16, selector_enabled=selector,
                          prepare=False, total_cny=50, per_attempt_cny=2)
    budget = Budget(spec, StringIO())
    usage = b'{"usage":{"prompt_tokens":1,"completion_tokens":1}}'
    for turn in range(16):
        if selector:
            budget.finish(budget.reserve(1), usage, 200)
        budget.finish(budget.reserve(1), usage, 200)
    assert budget.calls == (32 if selector else 16)
    with pytest.raises(ValueError, match='budget exhausted'):
        budget.reserve(1)
    assert budget.summary()['denied_model_requests'] == 1


def test_unknown_usage_retains_reservations_and_can_stop_before_turn_allowance():
    from io import StringIO
    from procurement_eval.budget_proxy import Budget
    from scripts.planning.live_baseline import request_budget

    spec = request_budget(actor_turn_allowance=16, selector_enabled=True,
                          prepare=False, total_cny=50, per_attempt_cny=2)
    budget = Budget(spec, StringIO())
    for _ in range(21):
        budget.reserve(1)
    with pytest.raises(ValueError, match='budget exhausted'):
        budget.reserve(1)
    assert budget.calls < spec['max_model_calls']
    assert budget.summary()['usage_complete'] is False
    assert budget.summary()['reserved_upper_cny'] <= 2


def test_extra_selector_allowance_preserves_money_limit_and_prepare_no_calls():
    from io import StringIO
    from procurement_eval.budget_proxy import Budget
    from scripts.planning.live_baseline import request_budget

    spec = request_budget(actor_turn_allowance=16, selector_enabled=True,
                          prepare=False, total_cny=50, per_attempt_cny=0.01)
    with pytest.raises(ValueError, match='budget exhausted'):
        Budget(spec, StringIO()).reserve(1)
    spec = request_budget(actor_turn_allowance=16, selector_enabled=True,
                          prepare=True, total_cny=50, per_attempt_cny=2)
    with pytest.raises(ValueError, match='budget exhausted'):
        Budget(spec, StringIO()).reserve(1)


@pytest.mark.parametrize('value', (0, -1, True, 1.5))
def test_invalid_actor_allowance_is_rejected_before_execution(value):
    from scripts.planning.live_baseline import request_budget

    with pytest.raises(ValueError, match='positive integer'):
        request_budget(actor_turn_allowance=value, selector_enabled=True,
                       prepare=False, total_cny=50, per_attempt_cny=2)
