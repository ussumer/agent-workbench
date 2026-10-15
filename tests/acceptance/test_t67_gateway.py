"""Real local HTTP lifecycle tests. No provider/model/service calls."""
import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import Request,urlopen
import pytest
from scripts.planning import gdpevo_v4_taskset as runtime
from scripts.planning import gdpevo_v4_gateway as proxy


@pytest.mark.parametrize('interrupted,http_status',[(False,200),(True,200),(True,503)])
def test_gateway_drains_late_response_before_closing_ledger(tmp_path,interrupted,http_status):
    entered=threading.Event();release=threading.Event();leaving=threading.Event();closed=threading.Event()
    errors=[];result={}
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            entered.set()
            if not release.wait(10):raise TimeoutError('test response was not released')
            body=json.dumps({'model':'local-test-model','usage':{'prompt_tokens':100,'completion_tokens':10}}).encode()
            self.send_response(http_status);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
    upstream_thread=threading.Thread(target=upstream.serve_forever,daemon=True);upstream_thread.start()
    model=SimpleNamespace(model_id='local-test-model',base_url='http://127.0.0.1:'+str(upstream.server_port),api_key='synthetic-key')
    spec={'max_model_calls':4,'max_request_bytes':10000,'max_output_tokens':64,'input_cny_per_million':1,
          'output_cny_per_million':2,'per_attempt_cny':10,'timeout_seconds':5,'pricing_kind':'local-test'}
    path=tmp_path/'calls.jsonl';client_threads=[]
    def request(url):
        try:
            payload=json.dumps({'model':'local-test-model','messages':[{'role':'user','content':'local test'}]}).encode()
            with urlopen(Request(url+'/chat/completions',data=payload,headers={'Content-Type':'application/json'}),timeout=8) as response:response.read()
        except Exception as error:
            if http_status==200:errors.append(error)
    def run_scope():
        try:
            with path.open('w') as log:
                try:
                    with proxy.gateway(model,spec,log) as (url,budget):
                        result['budget']=budget
                        client=threading.Thread(target=request,args=(url,));client_threads.append(client);client.start()
                        if not entered.wait(5):raise TimeoutError('no upstream request')
                        leaving.set()
                        if interrupted:raise KeyboardInterrupt('operator stop')
                except KeyboardInterrupt:result['stopped']=True
        except BaseException as error:errors.append(error)
        finally:closed.set()
    worker=threading.Thread(target=run_scope);worker.start()
    try:
        assert leaving.wait(6),'request never reached local upstream'
        premature=closed.wait(1.2)
    finally:
        release.set();worker.join(8)
        for client in client_threads:client.join(8)
        upstream.shutdown();upstream.server_close();upstream_thread.join(2)
    assert not premature,'gateway closed the model-call ledger while a response was still in flight'
    assert not worker.is_alive() and not errors
    assert result.get('stopped',False) is interrupted
    ledger=[json.loads(line) for line in path.read_text().splitlines()]
    assert len(ledger)==2 and ledger[-1]['call']==1 and ledger[-1]['http_status']==http_status
    summary=result['budget'].summary()
    assert summary['gateway_requests_drained'] is True
    assert summary['model_calls']==1
    assert summary['usage_complete'] is (http_status==200)
    if http_status==200:
        assert ledger[-1]['usage']=={'prompt_tokens':100,'completion_tokens':10}
        assert summary['input_tokens']==100 and summary['output_tokens']==10
    else:
        assert ledger[-1]['usage'] is None and summary['actual_cost_cny'] is None
