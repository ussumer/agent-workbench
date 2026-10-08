"""Synthetic business and privacy checks. No model, services or write tools."""
from copy import deepcopy
import hashlib
import json

import pytest
from scripts.planning.gdpevo_expansion import (
    PUBLIC,TRAINING,CONTROL,ANSWERS,PRESERVED,ROOT,load,actor_view,mutate,verify_business,validate,
)
from scripts.planning.gdpevo_expansion_judge import grade_task,canonical,independent_solve,reference_solve

pytestmark=pytest.mark.unit
PUBLIC_DATA,TRAIN_DATA,CONTROL_DATA,GOLD=load()
TASKS={t['task_id']:t for t in PUBLIC_DATA['tasks']}


@pytest.mark.parametrize('task_id',list(TASKS))
def test_fixed_manual_business_answer_and_all_integer_optima(task_id):
    task=TASKS[task_id]
    result=verify_business(task,CONTROL_DATA['manual_checks'][task_id]['expected'])
    assert all(count<=100_000 for count in result['states'].values())
    for answer in GOLD['tasks'][task_id]:
        assert grade_task(task,answer,CONTROL_DATA['rubrics'][task_id])['business_success']


@pytest.mark.parametrize('cf',CONTROL_DATA['counterfactuals'],ids=lambda cf:cf['id'])
def test_single_factor_really_changes_business_result_with_manual_and_independent_proof(cf):
    original=TASKS[cf['task_id']]
    assert len(cf['changes'])==1
    changed=mutate(original,cf['changes'][0])
    before=independent_solve(original); after=verify_business(changed,cf['manual_expected'])
    signature=lambda r:(r['disposition'],r['objective'],canonical(r['decisions']))
    assert signature(before)!=signature(after)
    assert changed['input']!=original['input']
    for key in original:
        if key!='input': assert changed[key]==original[key]


def test_five_groups_combined_counts_preserve_old_seed_and_results():
    report=validate()
    assert (report['new_main_tasks'],report['combined_main_tasks'])==(40,50)
    assert (report['new_counterfactuals'],report['combined_counterfactuals'])==(40,48)
    assert report['rubric_points']==240
    for path,digest in json.loads(PRESERVED.read_text()).items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest


@pytest.mark.parametrize('gid',['quotes','packages','kits','revisions'])
def test_train_scopes_and_test_rule_hybrids_have_actual_policy_anchors(gid):
    local=[t for t in TASKS.values() if t['group_id']==gid]
    train=[t for t in local if t['split']=='train']; test=[t for t in local if t['split']=='test']
    assert len(train)==len(test)==5
    matrix=CONTROL_DATA['matrix']; train_combos={frozenset(matrix[t['task_id']]['rule_ids']) for t in train}
    assert len({frozenset(matrix[t['task_id']]['rule_ids']) for t in test}-train_combos)>=3
    for task in local:
        for point in CONTROL_DATA['rubrics'][task['task_id']]:
            assert point['binary'] and point['weight'] in (1,2,3)
            for rule,anchors in point['rule_anchors'].items():
                assert anchors
                for tid in anchors:
                    policies=TRAIN_DATA['tasks'][tid]['policy_evidence']
                    assert rule in {p['rule_id'] for p in policies}


@pytest.mark.parametrize('task_id',list(TASKS))
def test_actor_only_sees_one_permitted_task_with_no_gold_or_rubric(task_id):
    task=TASKS[task_id]; stage=task['split']; view=actor_view(PUBLIC_DATA,TRAIN_DATA,task_id,stage)
    assert view['task']==task
    assert ('training_materials' in view)==(stage=='train')
    text=json.dumps(view)
    for key in ('"expected"','"rubrics"','"gold"','"manual_checks"','"counterfactuals"'):
        assert key not in text
    for other in TASKS:
        if other!=task_id: assert other not in text
    with pytest.raises(ValueError,match='stage differ'):
        actor_view(PUBLIC_DATA,TRAIN_DATA,task_id,'test' if stage=='train' else 'train')


@pytest.mark.parametrize('outcome',['procurement','source_selection','freight_audit','commitment_ledger','approval_boundary','erp_claim_conflicts'])
def test_mutating_one_business_output_loses_exactly_one_point(outcome):
    tid='packages-test-01'; task=TASKS[tid]; answer=deepcopy(GOLD['tasks'][tid][0])
    if outcome=='procurement': answer['allocation']=[]
    elif outcome=='source_selection':
        key=next(k for k in answer['source_selection'] if k.startswith('AUDIT-'))
        answer['source_selection'][key]['quote_id']='audit-draft'
    elif outcome=='freight_audit': answer['freight_audit']['under-edge']+=1
    elif outcome=='commitment_ledger': answer['commitment_ledger']=[{'order_id':'fake','part_id':'PE','quantity':1,'paid_cents':1}]
    elif outcome=='approval_boundary': answer['approval_request']['revision']+=1
    else: answer['erp_claim_conflicts']=[]
    result=grade_task(task,answer,CONTROL_DATA['rubrics'][tid])
    assert {k.split(':')[-1] for k,passed in result['points'].items() if not passed}=={outcome}


def test_wrong_copied_allocation_is_not_deducted_again_as_approval_or_freight_skill():
    tid='packages-test-01'; answer=deepcopy(GOLD['tasks'][tid][0]); answer['allocation']=[]; answer['approval_request']['lines']=[]; answer['freight_cents']={}
    result=grade_task(TASKS[tid],answer,CONTROL_DATA['rubrics'][tid])
    assert [p for p,v in result['points'].items() if not v]==[tid+':procurement']


def test_equal_objective_alternative_and_array_order_are_accepted():
    cf=next(c for c in CONTROL_DATA['counterfactuals'] if c['id']=='packages-cf-03')
    task=mutate(TASKS[cf['task_id']],cf['changes'][0]); answers=GOLD['counterfactuals'][cf['id']]
    assert len(answers)==2
    for answer in answers:
        candidate=deepcopy(answer)
        for field in ('allocation','commitment_ledger','erp_claim_conflicts'):
            candidate[field].reverse()
        candidate['approval_request']['lines'].reverse()
        assert grade_task(task,candidate,CONTROL_DATA['rubrics'][cf['task_id']])['business_success']


def test_boolean_is_not_an_integer_pack_or_revision():
    tid='quotes-test-01'; answer=deepcopy(GOLD['tasks'][tid][0]); answer['allocation'][0]['packs']=True
    assert not grade_task(TASKS[tid],answer,CONTROL_DATA['rubrics'][tid])['business_success']
    answer=deepcopy(GOLD['tasks'][tid][0]); answer['approval_request']['revision']=True
    assert not grade_task(TASKS[tid],answer,CONTROL_DATA['rubrics'][tid])['business_success']


def test_enumeration_bound_fails_closed_without_claiming_infeasible():
    with pytest.raises(ValueError,match='bound exceeded'):
        independent_solve(TASKS['packages-test-04'],limit=1)


def test_decision_classes_distinguish_irrelevant_pending_relevant_pending_and_true_infeasibility():
    assert independent_solve(TASKS['quotes-test-02'])['disposition']=='execute'
    assert independent_solve(TASKS['quotes-test-03'])['disposition']=='needs_information'
    assert independent_solve(TASKS['quotes-test-05'])['disposition']=='infeasible'
    assert independent_solve(TASKS['revisions-test-02'])['disposition']=='execute'


def test_non_greedy_cart_and_irreversible_revision_examples():
    result=independent_solve(TASKS['packages-test-05'])
    assert result['objective']==((4,),-640)
    result=independent_solve(TASKS['revisions-test-01'])
    assert result['objective']==((),-670)
    assert result['decisions'][0]['allocation']==[{'quote_id':'vf-pack','packs':1}]
    assert GOLD['tasks']['revisions-test-01'][0]['commitment_ledger'][0]['quantity']==4


def test_private_data_is_distinct_and_no_model_calibration_claim():
    assert len({PUBLIC,TRAINING,CONTROL,ANSWERS})==4
    assert CONTROL.parent.name==ANSWERS.parent.name=='private'
    assert CONTROL_DATA['claims']=={'model_calls':0,'calibrated':False,'learning_gain_proven':False,'production_adapter_ready':False}
