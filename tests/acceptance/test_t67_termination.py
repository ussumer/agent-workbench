from pathlib import Path
import asyncio,json
import pytest
from tests.acceptance.test_t67_runtime import unit_run_fixture


def test_keyboard_interrupt_generating_skill_records_stopped_and_all_calls(tmp_path,monkeypatch):
    runtime,output,config,budget=unit_run_fixture(tmp_path,monkeypatch)
    def interrupted(model,directory,supervision,**kwargs):
        directory.mkdir(parents=True)
        runtime.write(directory/'scope.json',{'owner':'owned-skill','thread':'thread-skill','kind':'skill'})
        budget.calls+=1
        raise KeyboardInterrupt('operator stop')
    monkeypatch.setattr(runtime,'generate_skill',interrupted)
    try:
        result=runtime.run(output,config)
    except (KeyboardInterrupt,asyncio.CancelledError):
        pytest.fail('run leaked operator interruption instead of recording stopped receipt')
    assert result['status']=='stopped'
    assert result['skills'][0]['status']=='interrupted'
    assert result['skills'][0]['owner']=='owned-skill'
    assert result['usage']['model_calls']==1
    receipt=runtime.read(output/'run/stop-receipt.json')
    assert receipt['active_kind']=='skill' and receipt['usage']['model_calls']==1
    assert receipt['control_cleanup']['status']=='passed'
    assert 'stop-receipt.json' in runtime.read(output/'run/manifest.json')


def test_cancelled_actor_keeps_partial_scope_in_rows_and_stops_non_successfully(tmp_path,monkeypatch):
    runtime,output,config,budget=unit_run_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(runtime,'verify_mcp_attempt',lambda *a,**kw:{})
    async def cancelled(model,config_path,directory,*args,**kwargs):
        directory.mkdir(parents=True)
        runtime.write(directory/'scope.json',{'owner':'owned-actor','thread':'thread-actor','kind':'solve'})
        budget.calls+=1
        runtime.write(directory/'mcp/cleanup.json',{'recycled':False,'container_absent':False})
        runtime.write(directory/'stdio-cleanup.json',{'closed':False})
        raise asyncio.CancelledError('operator stop')
    monkeypatch.setattr(runtime,'actor_session',cancelled)
    try:
        result=runtime.run(output,config)
    except (KeyboardInterrupt,asyncio.CancelledError):
        pytest.fail('run leaked operator interruption instead of recording stopped receipt')
    assert result['status']=='stopped'
    assert len(result['rows'])==1
    row=result['rows'][0]
    assert row['status']=='interrupted' and row['owner']=='owned-actor'
    assert row['model_calls_before']==3 and row['model_calls_after']==4
    receipt=runtime.read(output/'run/stop-receipt.json')
    assert receipt['active_kind']=='solve'
    assert receipt['actor_cleanup']['mcp']['container_absent'] is False
    assert receipt['actor_cleanup']['stdio']['closed'] is False


def test_stopped_cli_exit_is_not_success(tmp_path,monkeypatch,capsys):
    from scripts.planning import gdpevo_v4_taskset as runtime
    monkeypatch.setattr(runtime,'run',lambda *a:{'status':'stopped','calibration_complete':False})
    monkeypatch.setattr('sys.argv',['runtime','run','--output',str(tmp_path),'--runtime-config',str(tmp_path/'unused')])
    with pytest.raises(SystemExit) as error:runtime.main()
    assert error.value.code==2
    assert json.loads(capsys.readouterr().out)['status']=='stopped'


def test_interrupt_after_scoring_preserves_completed_business_result(tmp_path,monkeypatch):
    runtime,output,config,budget=unit_run_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(runtime,'verify_mcp_attempt',lambda *a,**kw:{})
    async def completed(model,config_path,directory,*args,**kwargs):
        directory.mkdir(parents=True)
        runtime.write(directory/'scope.json',{'owner':'completed-owner','thread':'completed-thread','kind':'solve'})
        budget.calls+=1
        return {'owner':'completed-owner','thread':'completed-thread'}
    monkeypatch.setattr(runtime,'actor_session',completed)
    monkeypatch.setattr(runtime,'score',lambda *args:{'score':1.0,'points':[]})
    original_write=runtime.write;interrupted=False
    def interrupt_after_saved_row(path,value):
        nonlocal interrupted
        original_write(path,value)
        if path==output/'run/result.json' and value.get('rows') and value['rows'][0]['status']=='scored' and not interrupted:
            interrupted=True
            raise KeyboardInterrupt('stop after completed result saved')
    monkeypatch.setattr(runtime,'write',interrupt_after_saved_row)
    result=runtime.run(output,config)
    assert result['status']=='stopped' and len(result['rows'])==1
    assert result['rows'][0]['status']=='scored'
    assert result['rows'][0]['grade']['score']==1.0


def test_interrupt_after_generation_preserves_completed_skill(tmp_path,monkeypatch):
    runtime,output,config,budget=unit_run_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(runtime,'verify_mcp_attempt',lambda *a,**kw:{})
    original_write=runtime.write;interrupted=False
    def interrupt_after_saved_skill(path,value):
        nonlocal interrupted
        original_write(path,value)
        if path==output/'run/result.json' and value.get('skills') and value['skills'][0]['status']=='generated' and not interrupted:
            interrupted=True
            raise KeyboardInterrupt('stop after generated skill saved')
    monkeypatch.setattr(runtime,'write',interrupt_after_saved_skill)
    result=runtime.run(output,config)
    assert result['status']=='stopped' and len(result['skills'])==1
    assert result['skills'][0]['status']=='generated'
    assert Path(result['skills'][0]['path']).is_file()
