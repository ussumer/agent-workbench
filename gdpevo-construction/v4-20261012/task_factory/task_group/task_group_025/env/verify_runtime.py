"""Trusted construction runtime driver; prints no token and calls no model."""
import os,json,secrets,subprocess
from pathlib import Path
root=Path(__file__).resolve().parent
scratch=root.parents[2]/'scratch'/'env-builder'
network='gdpevo-group025-v4'
container='gdpevo-sourcing-025-envbuild-attempt3'
volume='gdpevo-sourcing-025-envbuild-attempt3-data'
def call(args,**kw):
    return subprocess.run(args,check=True,text=True,**kw)
token=secrets.token_urlsafe(32)
env=dict(os.environ,ERP_SERVICE_TOKEN=token)
if subprocess.run(['docker','network','inspect',network],capture_output=True).returncode:
    call(['docker','network','create',network],stdout=subprocess.DEVNULL)
if subprocess.run(['docker','volume','inspect',volume],capture_output=True).returncode:
    call(['docker','volume','create',volume],stdout=subprocess.DEVNULL)
call(['docker','run','-d','--name',container,'--network',network,'--network-alias','task-env','--mount',f'type=volume,src={volume},dst=/data','--env','ERP_SERVICE_TOKEN','--cap-drop','ALL','--security-opt','no-new-privileges','rush-harness/sourcing-env:group025-v4'],env=env,stdout=subprocess.DEVNULL)
probe=['docker','run','--rm','--network',network,'--env','ERP_SERVICE_TOKEN','--mount',f'type=bind,src={root / "probe_http.py"},dst=/probe.py,readonly','python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea','python','/probe.py']
with (scratch/'http-first-attempt3.json').open('w') as out:call(probe,env=env,stdout=out)
call(['docker','restart',container],stdout=subprocess.DEVNULL)
with (scratch/'http-restart-attempt3.json').open('w') as out:call(probe,env=env,stdout=out)
a=json.loads((scratch/'http-first-attempt3.json').read_text());b=json.loads((scratch/'http-restart-attempt3.json').read_text())
assert a['program021_data_sha256']==b['program021_data_sha256'],'restart changed business data'
inspect=json.loads(call(['docker','inspect',container],capture_output=True).stdout)[0]
report=dict(status='passed',model_calls=0,container=container,network=network,volume=volume,no_published_ports=not inspect['HostConfig']['PortBindings'],mount_types=[m['Type'] for m in inspect['Mounts']],network_aliases=inspect['NetworkSettings']['Networks'][network]['Aliases'],first=a,restart=b)
assert report['no_published_ports'] and report['mount_types']==['volume']
(scratch/'runtime-report-final.json').write_text(json.dumps(report,indent=2)+'\n')
(scratch/'container-final.log').write_text(call(['docker','logs',container],capture_output=True).stdout)
print(json.dumps(report,indent=2))

