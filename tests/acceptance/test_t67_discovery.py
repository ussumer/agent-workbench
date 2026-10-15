from pathlib import Path
import json
import pytest
from scripts.planning import gdpevo_v4_taskset as runtime


def history(results):
    rows=[]
    for n,result in enumerate(results):
        rows.extend([{'type':'ai','tool_calls':[{'id':str(n),'name':'sourcing_read','args':{'path':'/api/sourcing/documents/guessed-'+str(n)}}]},
                     {'type':'tool','tool_call_id':str(n),'status':result.get('tool_status','success'),'content':json.dumps(result['value'])}])
    return rows


def test_original_cont3_exploration_is_stopped_before_sixty_requested_tools():
    rows=json.loads((runtime.ROOT/'tests/fixtures/t67_cont3_base_messages.json').read_text())
    requested=0
    for n,row in enumerate(rows):
        requested+=len(row.get('tool_calls',[]))
        if runtime.no_progress_reason(rows[:n+1]) is not None:
            assert requested<60
            break
    else:pytest.fail('the actual 83-404 failure trace still escapes the progress guard')


def test_varying_error_paths_and_empty_query_variants_do_not_reset_progress():
    failures=[{'tool_status':'error','value':{'status':'failed','error':'HTTP404:'+str(n)}} for n in range(9)]
    assert runtime.no_progress_reason(history(failures)) is not None
    empties=[{'value':{'data':{'items':[],'total':0,'offset':n,'limit':100},'request_id':str(n)}} for n in range(10)]
    assert runtime.no_progress_reason(history(empties)) is not None


def test_same_records_in_different_wrappers_or_request_params_are_not_new_facts():
    repeats=[{'value':{'data':{'items':[{'document_id':'D-1','value':42}],'total':1,'offset':n,'limit':100},'request_id':str(n)}} for n in range(10)]
    assert runtime.no_progress_reason(history(repeats)) is not None


def test_new_pages_records_and_correction_after_error_remain_allowed():
    fresh=[{'value':{'data':{'items':[{'document_id':'D-'+str(n),'value':n}],'total':20,'offset':n,'limit':1}}} for n in range(20)]
    assert runtime.no_progress_reason(history(fresh)) is None
    failures=[{'tool_status':'error','value':{'error':'404:'+str(n)}} for n in range(5)]
    assert runtime.no_progress_reason(history(failures+fresh[:1]+failures)) is None


def test_document_lookup_does_not_claim_a_nonexistent_list_route():
    with pytest.raises(ValueError,match='document_id'):
        runtime.validate_domain_request('/api/sourcing/documents',{'query':'PRG-084'})
    assert runtime.validate_domain_request('/api/sourcing/documents/ANY-ID',{})==('/api/sourcing/documents/ANY-ID',{})
