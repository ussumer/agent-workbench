from pathlib import Path
import importlib.util,tomllib,json
import pytest
ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('control',ROOT/'scripts/planning/gdpevo_v4_control.py')
@pytest.fixture
def control():
    assert SPEC.origin and Path(SPEC.origin).exists(),'trusted control deployment missing'
    module=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(module);return module

def test_management_only_bind_and_loopback_eip(control):
    template=(ROOT/'infra/sandbox/sandbox.toml').read_text(encoding='utf-8')
    cfg=tomllib.loads(control.control_config(template,'exec-test','172.28.0.2',18125))
    assert cfg['server']['host']=='172.28.0.2'
    assert cfg['server']['port']==18125 and cfg['server']['eip']=='127.0.0.1:18125'
    assert cfg['docker']['network_mode']=='exec-test'
    assert cfg['storage']['allowed_host_paths']==[]
    assert cfg['store']['path']=='/tmp/control.db'

@pytest.mark.parametrize('ip',['0.0.0.0','127.0.0.1','::','invalid'])
def test_unsafe_management_address_denied(control,ip):
    with pytest.raises(ValueError):control.control_config('', 'private',ip,18125)

def test_control_mounts_never_include_host_source_or_model_credentials(control,tmp_path):
    cfg={'sandbox_port':18125,'sandbox_control_image':'sha256:'+'a'*64}
    args=control.control_create_args(cfg,tmp_path/'sandbox.toml','mgmt','172.28.0.2','control')
    assert '127.0.0.1:18125:18125' in args
    assert '/usr/local/bin/python' in args
    assert 'sha256:'+'a'*64 in args
    mounts=[args[i+1] for i,v in enumerate(args) if v=='--mount']
    assert len(mounts)==2
    assert any('docker.sock' in x for x in mounts)
    assert any('sandbox.toml' in x and 'readonly' in x for x in mounts)
    assert not any('site-packages' in x or '/.env' in x for x in mounts)

def test_mutable_control_image_denied(control,tmp_path):
    with pytest.raises(ValueError,match='immutable'):
        control.control_create_args({'sandbox_port':18125,'sandbox_control_image':'python:latest'},tmp_path/'sandbox.toml','mgmt','172.28.0.2','ctrl')

def test_cleanup_removes_only_owned_network_sandboxes(control,monkeypatch):
    calls=[]
    def fake(*args):
        calls.append(args)
        if args[:2]==('ps','-a'):return 'ctrl\nsandbox-abc\n'
        if args[0]=='inspect':return json.dumps([{'Name':'/sandbox-abc','NetworkSettings':{'Networks':{'exec':{}}}}])
        return ''
    monkeypatch.setattr(control,'docker',fake)
    result=control.cleanup_control('ctrl','exec','mgmt')
    assert result['status']=='passed'
    assert ('rm','-f','sandbox-abc') in calls
    assert ('rm','-f','ctrl') in calls
    assert ('network','rm','exec') in calls
    assert ('network','rm','mgmt') in calls

def test_cleanup_never_masks_original_failure(control,monkeypatch):
    def fake(*args):raise RuntimeError('cleanup failure')
    monkeypatch.setattr(control,'docker',fake)
    result=control.cleanup_control('ctrl','exec','mgmt')
    assert result['status']=='failed' and result['errors']

def test_cleanup_includes_stopped_owned_sandboxes(control,monkeypatch):
    calls=[]
    def fake(*args):
        calls.append(args)
        if args[:2]==('network','inspect'):return json.dumps([{'Containers':{'c':{'Name':'ctrl'}}}])
        if args[:2]==('ps','-a'):return 'ctrl\nsandbox-stopped\n'
        if args[0]=='inspect':return json.dumps([{'Name':'/sandbox-stopped','State':{'Status':'exited'},'NetworkSettings':{'Networks':{'exec':{}}}}])
        return ''
    monkeypatch.setattr(control,'docker',fake)
    result=control.cleanup_control('ctrl','exec','mgmt')
    assert ('rm','-f','sandbox-stopped') in calls
    assert result['removed_sandboxes']==['sandbox-stopped']
