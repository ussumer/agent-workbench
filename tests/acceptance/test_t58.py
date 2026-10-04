import json
from copy import deepcopy
import pytest
from scripts.planning.gdpevo_v3_scoped import BASE, GROUPS, scoped_curator_input

@pytest.fixture
def learning():
    return json.loads((BASE/'frozen/gdpevo-procurement-v3-training.json').read_text()), json.loads((BASE/'training-records.json').read_text())

@pytest.mark.parametrize('group',GROUPS)
def test_curator_keeps_full_group_rules_and_paid_attempts(learning,group):
    training,records=learning
    material=scoped_curator_input(training,records,group)
    source=[r for r in records if r['task']['group_id']==group]
    assert len(source)==len(material['train_records'])==5
    assert len(json.dumps(material,ensure_ascii=False).encode())<110000
    rules={r['rule_id']:r for r in material['policies']}
    for old,entry in zip(source,material['train_records'],strict=True):
        assert entry['task_id']==old['task']['task_id']
        for rule in training['tasks'][entry['task_id']]['policy_evidence']:
            assert rule['rule_id'] in entry['rule_ids']
            for field in ('condition','policy','fields','stop'):assert rules[rule['rule_id']][field]==rule[field]
        for a,b in zip(old['attempts'],entry['attempts'],strict=True):
            assert a['model_output']==b['decision'] and a['grade']==b['grade']
    assert not any(r['task']['task_id'] in json.dumps(material) for r in records if r['task']['group_id']!=group)

def test_curator_rejects_test_and_incomplete_group(learning):
    training,records=learning
    bad=deepcopy(records);bad[0]['task']['split']='test'
    with pytest.raises(ValueError):scoped_curator_input(training,bad,'quotes')
    with pytest.raises(ValueError):scoped_curator_input(training,records[1:],'quotes')

def test_curator_rejects_unknown_group_and_missing_policy(learning):
    training,records=learning
    with pytest.raises(ValueError):scoped_curator_input(training,records,'unknown')
    training=deepcopy(training);training['tasks'][records[0]['task']['task_id']]['policy_evidence']=[]
    with pytest.raises(ValueError):scoped_curator_input(training,records,'quotes')
