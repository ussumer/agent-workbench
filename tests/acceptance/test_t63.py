"""Lineage, real-selection attribution, and observation-only cost policy."""
import copy
import io
import json

import pytest

from scripts.planning.gdpevo_trace_loop import (
    execution_policy, group_by_skill, instance_ids, normalize_stage_sources, stage_prompt, validate_stage,
    validate_trace_skill,
)
from agent.evolution.episodes import TextSkill
from procurement_eval.budget_proxy import Budget


def stage():
    return {'items':[{'id':'integer_allocation','sources':['task-source'],
        'competency':'整数方案比较','rationale':'真实失败表明贪心省略非单调运费候选',
        'description':'整数采购且运费有门槛时激活','body':'枚举可行整包方案，核对累计费用和约束，再比较完整目标。'}]}


def row(selection, *, success=False, split='train'):
    return {'task_id':'task-source','split':split,'public_view':{'task':{'split':split}},
        'grade':{'business_success':success},'decision':{},'trace':{'actor_messages':[],
            'episode_export':{'events':[{'kind':'turn_grounded','payload':{
                'turn_id':'turn-1','selection':selection,'read_bodies':[],
                'visible_system':{'content':'actual visible prompt'}}}]}}}


def test_final_skills_have_lineage_and_focused_body():
    skills = validate_stage(stage(),{'task-source'},final=True)
    assert skills[0].skill_id == 'integer_allocation'
    assert '运费有门槛' in skills[0].description


@pytest.mark.parametrize('sources', [['invented'],['task-source','task-source'],[]])
def test_broken_stage_sources_rejected(sources):
    value = stage(); value['items'][0]['sources'] = sources
    with pytest.raises(ValueError):
        validate_stage(value,{'task-source'})


def test_duplicate_skill_ids_rejected():
    value = stage(); value['items'].append(copy.deepcopy(value['items'][0]))
    with pytest.raises(ValueError,match='duplicate'):
        validate_stage(value,{'task-source'},final=True)


@pytest.mark.parametrize('leak', ['购买报价 supplier_secret','总成本 550','packages-train-04'])
def test_final_bank_rejects_instances(leak):
    value = stage(); value['items'][0]['body'] = leak
    with pytest.raises(ValueError):
        validate_stage(value,{'task-source'},final=True,forbidden={'supplier_secret'})


def test_actual_selection_groups_success_and_failure_without_fabricating_usage():
    skills = [TextSkill(skill_id=s,description='条件',body='检查') for s in ['used','unused']]
    grouped = group_by_skill([row(['used']),row(['used'],success=True)],skills)
    assert len(grouped['by_skill']['used']['failure']) == 1
    assert len(grouped['by_skill']['used']['success']) == 1
    assert grouped['by_skill']['unused'] == {'success':[],'failure':[]}
    failed_id = grouped['by_skill']['used']['failure'][0]
    assert 'not causal' in grouped['trajectories'][failed_id]['outcome_level']


def test_empty_or_unknown_selection_retained_for_missing_capabilities():
    skill = TextSkill(skill_id='known',description='条件',body='检查')
    for selected in ([], ['unknown']):
        groups = group_by_skill([row(selected)],[skill])
        assert len(groups['uncovered']) == 1
        assert not groups['by_skill']['known']['failure']


def test_test_feedback_cannot_enter_evolution():
    with pytest.raises(ValueError,match='test'):
        group_by_skill([row([],split='test')],[])


def test_unlimited_currency_still_records_usage_and_keeps_execution_step_boundary():
    spec = execution_policy(); budget = Budget(spec,io.StringIO())
    reserved = budget.reserve(100000)
    assert reserved['reserved_upper_cny'] > 0
    budget.finish(reserved,json.dumps({'model':'configured','usage':{
        'prompt_tokens':100,'completion_tokens':5}}).encode(),200)
    summary = budget.summary()
    assert summary['usage_complete'] is True and summary['cost_estimate_cny'] > 0
    assert spec['per_attempt_cny'] is None and spec['enforce_cost_limit'] is False
    budget.calls = spec['max_model_calls']
    with pytest.raises(ValueError):
        budget.reserve(100)


def test_legacy_budget_mode_remains_enforced():
    spec = execution_policy(); spec.update(enforce_cost_limit=True,per_attempt_cny=0.001)
    with pytest.raises(ValueError):
        Budget(spec,io.StringIO()).reserve(100000)


def test_identifiers_extracted_for_both_description_and_body_checks():
    assert instance_ids({'task_id':'task-id','quotes':[{'supplier_id':'supplier-id','quote_id':'quote-id'}]}) == {
        'task-id','supplier-id','quote-id'}
    value = stage(); value['items'][0]['description'] = '读取 quote-id 的方案'
    with pytest.raises(ValueError):
        validate_stage(value,{'task-source'},final=True,forbidden={'quote-id'})


def test_approval_authorization_is_domain_text_but_credential_assignment_is_rejected():
    assert validate_trace_skill('approval', '审批', '检查 authorization=historical_only 后不复用旧批准')
    with pytest.raises(ValueError):
        validate_trace_skill('secret', '审批', 'api_key=secret')


def test_four_stages_and_refinement_have_distinct_real_curator_protocols():
    assert '每个item对应一个任务' in stage_prompt('task')
    assert '按任务类型' in stage_prompt('type')
    assert '跨任务类型按操作' in stage_prompt('operation')
    assert '单一聚焦能力' in stage_prompt('decompose')
    assert '实际技能选择归组' in stage_prompt('refine')


def test_stage_leaf_source_is_canonicalized_only_through_recorded_alias():
    value = stage()
    value['items'][0]['sources'] = ['leaf-task']
    normalized = normalize_stage_sources(value, {'type-item'}, {'leaf-task':['type-item']})
    assert normalized['items'][0]['sources'] == ['type-item']
    with pytest.raises(ValueError):
        normalize_stage_sources(value, {'type-item'}, {'other':['type-item']})
