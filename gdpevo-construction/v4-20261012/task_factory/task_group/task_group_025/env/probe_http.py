"""Run inside an independent network-only HTTP probe container. No model or gold."""
import json, os, time, urllib.request, urllib.error, hashlib
base='http://task-env:9025'
token=os.environ['ERP_SERVICE_TOKEN']
def request(path,method='GET',auth=True):
    req=urllib.request.Request(base+path,method=method,headers={'X-Service-Token':token} if auth else {})
    try:
        with urllib.request.urlopen(req,timeout=10) as r:return r.status,json.load(r)
    except urllib.error.HTTPError as e:return e.code,json.load(e)
for attempt in range(60):
    try:
        status,health=request('/health',auth=False)
        if status==200 and health['data']['database']=='up':break
    except (OSError,ValueError):pass
    time.sleep(1)
else:raise RuntimeError('Java/H2 environment did not become ready within60seconds')
checks=[]
def check(name,condition):
    assert condition,name
    checks.append(name)
s,b=request('/api/sourcing/programs',auth=False);check('unauthenticated rejected',s==401)
s,b=request('/api/sourcing/programs?offset=1&limit=2');check('stable pagination',s==200 and b['data']['total']==150 and b['data']['items'][0]['program_id']=='PRG-002')
counts={}
for d in ['programs','suppliers','forecasts','quotes','engineering','contracts','payments']:
    s,b=request('/api/sourcing/'+d+'?limit=1');check('database domain '+d,s==200);counts[d]=b['data']['total']
for suffix in ['021','034','047','058','069','083','096','107','118','129']:
    p='PRG-'+suffix
    s,b=request('/api/sourcing/programs/'+p);check('target '+p,s==200 and len(b['data']['supplier_ids'])==3)
    program=b['data']
    for field in ['finance_policy_document_id','fx_document_id']:
        s,b=request('/api/sourcing/documents/'+program[field]);check('document '+p+' '+field,s==200)
    s,b=request('/api/sourcing/forecasts?program_id='+p);check('forecast versions '+p,s==200 and b['data']['total']==16 and {r['revision'] for r in b['data']['items']}=={1,2})
    s,b=request('/api/sourcing/quotes?program_id='+p);check('quote variants and versions '+p,s==200 and b['data']['total']==12 and len({r['supplier_id'] for r in b['data']['items']})==3)
    quotes=b['data']['items']
    s,b=request('/api/sourcing/quotes?program_id='+p+'&supplier_id='+program['supplier_ids'][0]);check('supplier filter '+p,s==200 and b['data']['total']==4)
    for row in quotes:
        check('commercial conventions '+row['document_id'],all(k in row for k in ['currency','uom','effective_period','approval_status','price_tiers']))
        check('no computed decision '+row['document_id'],not any(k in row for k in ['winner','expected_npv','rubric','task_id']))
for path,method,expected in [('/api/sourcing/programs','POST',403),('/api/erp/v1/orders','POST',403),('/api/erp/v1/suppliers','GET',404),('/api/judge','GET',404),('/api/sourcing/programs?limit=0','GET',400),('/api/sourcing/programs/PRG-999','GET',404)]:
    s,b=request(path,method);check('boundary '+path+' '+method,s==expected)
s,b=request('/api/sourcing/programs/PRG-021')
print(json.dumps({'status':'passed','probe_runtime':'independent Docker python3.12 HTTP client','model_calls':0,'checks_passed':len(checks),'counts':counts,'program021_data_sha256':hashlib.sha256(json.dumps(b['data'],sort_keys=True).encode()).hexdigest(),'health':health['data']},indent=2))
