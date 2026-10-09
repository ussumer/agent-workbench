"""Experiment boundaries; no scripted business answers or model calls."""
import copy
import json
from types import SimpleNamespace

import pytest

from scripts.planning.persistent_computation_ablation import (
    PairedService, ARMS, hash_value, load_inputs, pair_schedule, prepare, stages, summary,
    verify, write,
)


class Sessions:
    def __init__(self, delegate):
        self.delegate = delegate

    def update_one(self, query, update):
        row = self.delegate.row
        matched = all(row.get(k) == v for k, v in query.items())
        if matched:
            row.update(update['$set'])
        return SimpleNamespace(matched_count=int(matched))


class Delegate:
    def __init__(self):
        self.row = {'_id': 'session', 'active': None, 'version': 0, 'values_json': '{}'}
        self.sessions = Sessions(self)
        self.calls = []

    def _scope(self, owner, thread, session):
        return {'owner_user_id': owner, 'thread_id': thread, 'session': session}

    def _session(self, scope):
        return copy.deepcopy(self.row)

    def status(self, *args, **kwargs):
        return {'data': json.loads(self.row['values_json']), 'version': self.row['version']}

    def execute(self, owner, thread, code, read_names, **kwargs):
        values = json.loads(self.row['values_json'])
        assert all(n in values for n in read_names)
        self.calls.append(code)
        update = json.loads(code)
        values.update(update)
        self.row.update(values_json=json.dumps(values), version=self.row['version']+1)
        return {'status': 'completed', 'saved_names': list(update), 'operation_id': str(len(self.calls))}


class Manager:
    def __init__(self):
        self.recycled = []

    def recycle(self, owner):
        self.recycled.append(owner)
        return True

    def get_or_create(self, owner):
        return SimpleNamespace(id='fresh-'+str(len(self.recycled)))


def wrapper(arm):
    delegate, manager = Delegate(), Manager()
    service = PairedService(delegate, manager, arm)
    service.update_source('u', 't', {'task': {'input': {'commitments': [{'order_id': 'old'}]}}})
    return service, delegate, manager


@pytest.mark.parametrize('arm', ARMS)
def test_only_derived_values_depend_on_treatment(arm):
    service, delegate, manager = wrapper(arm)
    original = copy.deepcopy(service.source)
    service.execute('u', 't', '{"normalized": {"price": 123}}', read_names=['task'])
    values = json.loads(delegate.row['values_json'])
    assert values['task'] == original
    assert ('normalized' in values) == (arm == 'A')
    assert manager.recycled == ['u']
    assert service.events[0]['committed']['normalized'] == hash_value({'price':123})


def test_A_loads_actual_prior_writer_while_B_requires_rebuild():
    a, da, _ = wrapper('A')
    a.execute('u','t','{"intermediate": 4}',read_names=['task'])
    a.execute('u','t','{"final": 8}',read_names=['task','intermediate'])
    assert a.events[1]['prior_writers'] == {'intermediate':'1'}
    b, db, _ = wrapper('B')
    b.execute('u','t','{"intermediate": 4}',read_names=['task'])
    b.execute('u','t','{"final": 8}',read_names=['task'])
    assert set(b.events[1]['before']) == {'task'}
    assert set(json.loads(db.row['values_json'])) == {'task'}


@pytest.mark.parametrize('arm', ARMS)
def test_source_refresh_preserves_orders_and_discards_model_source_overwrite(arm):
    service, delegate, _ = wrapper(arm)
    service.execute('u','t','{"task": {"bad": true}, "derived": 1}',read_names=['task'])
    assert json.loads(delegate.row['values_json'])['task'] == service.source
    new = copy.deepcopy(service.source); new['task']['input']['revision'] = 2
    service.update_source('u','t',new)
    values = json.loads(delegate.row['values_json'])
    assert values['task']['task']['input']['commitments'] == [{'order_id':'old'}]
    assert values['task']['task']['input']['revision'] == 2


def test_refuses_eviction_during_active_operation():
    service, delegate, _ = wrapper('B')
    delegate.row['active']='unfinished'
    with pytest.raises(RuntimeError,match='active'):
        service.status('u','t')


def test_twenty_tasks_three_repeats_and_balanced_arm_order():
    _, tasks, _, _ = load_inputs()
    schedule = pair_schedule(tasks)
    assert len(schedule)==60
    assert len({(p['task_id'],p['repeat']) for p in schedule})==60
    assert sum(p['order']==['A','B'] for p in schedule)==30
    assert len(pair_schedule(tasks,1)) == 20
    with pytest.raises(ValueError): pair_schedule(tasks,2)


def test_revision_changes_only_budget_and_revision():
    _, tasks, _, _ = load_inputs()
    original=copy.deepcopy(tasks)
    for task in tasks:
        a,b,c=stages(task)
        assert a==b==task
        assert c['input']['commitments']==task['input']['commitments']
        assert c['input']['revision']==a['input']['revision']+1
        assert c['input']['budget_cents']==a['input']['budget_cents']*9//10
        c['input']['budget_cents']=a['input']['budget_cents'];c['input']['revision']=a['input']['revision']
        assert c==a
    assert tasks==original


def test_prepare_freezes_inputs_disables_learning_and_never_includes_answers(tmp_path):
    path=prepare(tmp_path/'attempt')
    protocol=json.loads((path/'protocol.json').read_text())
    assert all(protocol[k] is False for k in ['curator','selector','learning','learned_skills'])
    text=(path/'inputs.json').read_text()
    assert 'correct_answer' not in text and 'rubrics' not in text
    assert not list((path/'frozen').rglob('*answers.json'))
    with pytest.raises(FileExistsError):prepare(path)


def test_summary_counts_failed_turn_in_denominator_and_does_not_pick_repeat():
    def turn(ok,score):
        return {'grade':{'business_success':ok,'score':score},
                'elapsed_seconds':1.,
                'metrics':{'episode_model_calls':1,'input_tokens':4,'output_tokens':2}}
    rows=[{'arm':a,'task_id':'one','repeat':1,'turns':[turn(True,1),turn(True,1),turn(a=='B',int(a=='B'))],'events':[]} for a in ARMS]
    x=summary(rows)
    assert x['A']['trajectories']==1 and x['A']['whole_success']==0
    assert x['B']['whole_success']==1
    assert x['paired'][0]['final_score_delta']==-1


def test_verifier_blocks_missing_execution_and_compares_actual_erp_orders(tmp_path):
    import hashlib
    path = prepare(tmp_path/'attempt', repeats=1)
    inputs = json.loads((path/'inputs.json').read_text())
    rows = []
    for task in inputs['tasks']:
        for arm in ARMS:
            turns = []
            for current in inputs['stages'][task['task_id']]:
                view = {'environment': inputs['environment'], 'task': current}
                turns.append({'input':view, 'input_hash':hash_value(view),
                    'status':'environment_failed',
                    'grade':{'score':0., 'business_success':False},
                    'metrics':{'episode_model_calls':0,'input_tokens':0,'output_tokens':0},
                    'elapsed_seconds':0.})
            rows.append({'arm':arm,'task_id':task['task_id'],'repeat':1,
                         'turns':turns,'events':[],'executions':[],
                         'tool_names':[],'tool_schemas':[]})
    write(path/'rows.json', rows)
    write(path/'report.json', {'summary':summary(rows)})
    write(path/'erp-before.json', {'request_id':'before','data':{'items':[],'total':0}})
    write(path/'erp-after.json', {'request_id':'after','data':{'items':[],'total':0}})
    def freeze():
        write(path/'manifest.json', {str(f.relative_to(path)):hashlib.sha256(f.read_bytes()).hexdigest()
            for f in path.rglob('*') if f.is_file() and f.name!='manifest.json'})
    freeze()
    result = verify(path)
    assert result['status']=='blocked'
    assert len(result['without_completed_execution'])==40
    assert result['business_effect_established'] is False
    write(path/'erp-after.json', {'request_id':'after','data':{'items':[{'order_id':'new'}],'total':1}})
    freeze()
    with pytest.raises(ValueError,match='ERP orders changed'):
        verify(path)
