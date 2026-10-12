import importlib.util
import json
from pathlib import Path
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
import pytest
ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'gdpevo-construction/v4-20261012/task_factory/task_group/task_group_025/env/judge_api.py'

@pytest.fixture
def api():
    assert SOURCE.exists(), 'train-only feedback implementation missing'
    spec=importlib.util.spec_from_file_location('t67_train_judge',SOURCE)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

class StubJudge:
    def __init__(self, enabled): self.enabled=enabled
    def evaluate(self, task_id, prediction):
        if not task_id.startswith('train_'): raise ValueError('hidden answer must not escape')
        return {'score':.25,'correct':False,'train_only':True}

@pytest.fixture
def live(api):
    servers=[]
    def start(enabled):
        server=ThreadingHTTPServer(('127.0.0.1',0),api.handler_for(StubJudge(enabled),'unit-secret'))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();servers.append((server,thread))
        return 'http://127.0.0.1:'+str(server.server_port)
    yield start
    for server,thread in servers: server.shutdown();server.server_close();thread.join()

def post(url, value, token='unit-secret'):
    request=urllib.request.Request(url+'/api/judge',data=json.dumps(value).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=3) as response: return response.status,json.load(response)
    except urllib.error.HTTPError as response: return response.code,json.load(response)

def test_disabled_has_no_feedback_route(live):
    assert post(live(False),{'task_id':'train_001','prediction':{}})[0]==404

def test_feedback_authentication(live):
    assert post(live(True),{'task_id':'train_001','prediction':{}},'wrong')[0]==401

def test_test_id_has_no_hidden_details(live):
    status,body=post(live(True),{'task_id':'test_001','prediction':{}})
    assert status==400 and body=={'error':'invalid_train_submission'}

def test_feedback_whitelist(live):
    status,body=post(live(True),{'task_id':'train_001','prediction':{}})
    assert status==200 and set(body)=={'score','correct','train_only'}

def test_payload_cannot_enable_judge(live):
    assert post(live(True),{'task_id':'train_001','prediction':{},'enabled':True})[0]==400

def test_phase_disabled_before_private_file_access(api,tmp_path):
    with pytest.raises(api.JudgeError): api.TrainJudge(tmp_path,False).evaluate('train_001',{})

def test_private_test_and_traversal_rejected_before_io(api,tmp_path):
    for task in ['test_001','../train_001','train_999']:
        with pytest.raises(api.JudgeError): api.TrainJudge(tmp_path,True).evaluate(task,{})

def test_missing_operator_token_rejected(api):
    with pytest.raises(api.JudgeError): api.handler_for(StubJudge(True),'')
