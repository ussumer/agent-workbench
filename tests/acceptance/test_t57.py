import json
from pathlib import Path
from scripts.planning.gdpevo_v3_closed_loop import build_curator_records

SESSION=Path('/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261004/attempt-gdpevo-v3-expanded-20261004')

def _report(): return json.loads((SESSION/'report.json').read_text())

def test_three_arms_cover_same_twenty_test_tasks():
 r=_report(); groups=r['groups']; assert set(groups)=={'fixed-v3','raw-v3','curated-v3'}
 rows=r['rows']; expected={x['task_id'] for x in rows if x['group']=='fixed-v3'}
 assert len(expected)==20
 for g in groups: assert {x['task_id'] for x in rows if x['group']==g}==expected

def test_curator_and_production_boundary():
 r=_report(); assert r['claims']['production_actor_run'] is False
 assert r['claims']['test_feedback'] is False if 'test_feedback' in r['claims'] else True
 bank=json.loads((SESSION/'bank.json').read_text()); assert bank['production_assignment_changed'] is False and bank['candidate_promoted'] is False

def test_failed_and_format_attempts_are_retained():
 r=_report(); assert any(x['status']!='scored' for x in r['rows'])
 assert all(x['metrics']['model_calls']==1 for x in r['rows'])

def test_manifest_and_frozen_training_origin():
 m=json.loads((SESSION/'manifest.json').read_text())
 for p,h in m.items():
  import hashlib
  assert hashlib.sha256((SESSION/p).read_bytes()).hexdigest()==h
 assert 'reused_train_attempts' in r_claims()

def r_claims(): return _report()['claims']

def test_curator_join_uses_train_policy_evidence_and_rejects_test():
    training=json.loads(Path('fixtures/planning/gdpevo-procurement-v3-training.json').read_text())
    task_id=next(iter(training['tasks']))
    public=json.loads(Path('fixtures/planning/gdpevo-procurement-v3.json').read_text())
    task=next(t for t in public['tasks'] if t['task_id']==task_id)
    record={'task':task,'attempts':[{'model_output':'{}','grade':{'score':0.0,'business_success':False,'points':{}},'feedback':{}}]}
    joined=build_curator_records(training,[record])
    assert joined[0]['policy_evidence']==training['tasks'][task_id]['policy_evidence']
    test_task=next(t for t in public['tasks'] if t['split']=='test')
    try:
        build_curator_records(training,[{'task':test_task,'attempts':[]}])
    except ValueError as exc:
        assert 'missing train policy evidence' in str(exc)
    else:
        raise AssertionError('test task entered Curator records')

def test_compact_curator_preserves_policy_scope_and_attempts():
    from scripts.planning.gdpevo_v3_recurate import BASE, _curator_input
    training=json.loads((BASE/'frozen/gdpevo-procurement-v3-training.json').read_text())
    records=json.loads((BASE/'training-records.json').read_text())
    material=_curator_input(training,records)
    assert len(material['train_records'])==20
    assert len(json.dumps(material,ensure_ascii=False).encode())<120000
    by_id={r['task_id']:r for r in material['train_records']}
    for original in records:
        tid=original['task']['task_id']; row=by_id[tid]
        assert len(row['attempts'])==len(original['attempts'])
        policies=material['policies_by_group'][row['group_id']]
        for rule in training['tasks'][tid]['policy_evidence']:
            assert rule['rule_id'] in row['rule_ids']
            for field in ('condition','policy','fields','stop'):
                assert policies[rule['rule_id']][field]==rule[field]
        for a,b in zip(row['attempts'],original['attempts'],strict=True):
            assert a['decision']==b['model_output'] and a['grade']==b['grade']
