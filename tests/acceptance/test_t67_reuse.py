from pathlib import Path
import importlib.util,json
import pytest
ROOT=Path(__file__).resolve().parents[2]
@pytest.fixture
def reuse():
    path=ROOT/'scripts/planning/gdpevo_v4_reuse.py'
    assert path.is_file(),'reuse source validation missing'
    spec=importlib.util.spec_from_file_location('reuse',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_pre_model_signature_ignores_only_unselected_postprocessing(reuse):
    a='SYSTEM="same"\ndef actor_session():\n return 1\ndef normalize_skill_body(x):\n return x\n'
    b=a.replace('return x','return x.strip()')
    assert reuse.pre_model_signature(a)==reuse.pre_model_signature(b)
    assert reuse.pre_model_signature(a)!=reuse.pre_model_signature(a.replace('"same"','"different"'))

def test_original_manifest_hashes_guard_actual_source_and_boundaries(reuse,tmp_path):
    source=tmp_path/'old';source.mkdir();(source/'data.json').write_text('{"value":42}')
    manifest={'data.json':reuse.digest(source/'data.json')};(source/'manifest.json').write_text(json.dumps(manifest))
    assert reuse.verify_manifest(source)==manifest
    (source/'data.json').write_text('{"value":43}')
    with pytest.raises(ValueError,match='hash'):reuse.verify_manifest(source)
    (source/'manifest.json').write_text(json.dumps({'../escape.json':'x'}))
    with pytest.raises(ValueError,match='escape'):reuse.verify_manifest(source)

def test_recovery_only_accepts_complete_actual_skill_and_specific_old_failure(reuse):
    failed={'status':'failed','error':{'causes':[{'type':'ValueError','message':'generated SKILL content/front matter missing'}]}}
    reuse.validate_old_status(failed,2)
    with pytest.raises(ValueError):reuse.validate_old_status({'status':'failed','error':{'causes':[{'type':'RuntimeError','message':'network failure'}]}},2)
    with pytest.raises(ValueError):reuse.validate_old_status({'status':'failed'},1)

def test_cleanup_observation_must_match_real_scope_and_historical_false(reuse):
    proof={'kind':'operator-observed-postprocessing-recovery','owner':'o','thread':'t','sandbox_id':'s','process_absent':True,'container_absent':True,'historical_stdio_closed':False}
    scope={'owner':'o','thread':'t'};cleanup={'recycled':True,'container_absent':True,'sandbox_id':'s'}
    assert reuse.validate_observation(proof,scope,cleanup,{'closed':False}) is True
    with pytest.raises(ValueError):reuse.validate_observation({**proof,'owner':'foreign'},scope,cleanup,{'closed':False})
    with pytest.raises(ValueError):reuse.validate_observation({**proof,'process_absent':False},scope,cleanup,{'closed':False})

def test_accounting_does_not_double_count_reused_calls_or_unknown_bill(reuse):
    current={'model_calls':4,'input_tokens':30,'output_tokens':6,'cost_estimate_cny':.4,'actual_cost_cny':None,'usage_complete':True}
    reused={'model_calls':13,'input_tokens':763671,'output_tokens':11217,'cost_estimate_cny':7.175898,'actual_cost_cny':None,'usage_complete':True}
    failed={'model_calls':11,'cost_estimate_cny':4.031811,'actual_cost_cny':None,'usage_complete':True}
    result=reuse.accounting(current,reused,[failed])
    assert result['effective_calibration_model_calls']==17
    assert result['all_attempt_model_calls']==28
    assert abs(result['all_attempt_cost_estimate_cny']-11.607709)<1e-9
    assert result['actual_cost_cny'] is None


def test_reused_body_must_equal_terminal_model_output(reuse,tmp_path):
    supervision=[{'task_id':'train_001','answer':{'winner':'supplier-a'}}]
    scope={'owner':'old-owner','thread':'old-thread','kind':'skill'}
    reuse.write(tmp_path/'messages.json',[{'type':'ai','content':'Actual method','response_metadata':{'finish_reason':'stop'}}])
    event={'kind':'model_input','value':[[{'type':'human','content':json.dumps(supervision)}]]}
    (tmp_path/'raw-trace.jsonl').write_text(json.dumps(event)+'\n')
    reuse.validate_input_and_trace(tmp_path,'Actual method',supervision,scope,str.strip)
    with pytest.raises(ValueError,match='terminal'):
        reuse.validate_input_and_trace(tmp_path,'Altered method',supervision,scope,str.strip)


def test_prepaid_source_ledger_uses_failed_row_scope_and_rejects_unknown_usage(reuse,tmp_path):
    source=tmp_path/'source';run=source/'run';run.mkdir(parents=True)
    rows=[{'session':1,'status':'generated','model_calls_before':0,'model_calls_after':1},
          {'session':2,'status':'failed','model_calls_before':1,'model_calls_after':2}]
    ledger=[]
    for index in (1,2):
        folder=run/f'skills/session-{index}';folder.mkdir(parents=True)
        reuse.write(folder/'scope.json',{'owner':f'owner-{index}','thread':f'thread-{index}','kind':'skill'})
        events=[{'kind':'model_input','value':[[{'type':'human','content':'example'}]]},
                {'kind':'model_output','value':{'llm_output':{'token_usage':{'prompt_tokens':10,'completion_tokens':3}}}}]
        (folder/'raw-trace.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
        ledger.extend([{'call':index}, {'call':index,'http_status':200,'started_at':'2026-10-13','finished_at':'2026-10-13',
            'observed_model':'configured-model','request_sha256':'a'*64,'usage':{'prompt_tokens':10,'completion_tokens':3}}])
    reuse.write(run/'result.json',{'status':'failed','skills':rows,'model_identity':{'model_id':'configured-model'},
        'usage':{'model_calls':2,'input_tokens':20,'output_tokens':6,'usage_complete':True,'actual_cost_cny':None}})
    (run/'model-calls.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in ledger))
    assert reuse.verify_source_ledger(source)['model_calls']==2
    ledger[-1]['usage']['prompt_tokens']=11
    (run/'model-calls.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in ledger))
    with pytest.raises(ValueError,match='token usage'):
        reuse.verify_source_ledger(source)
