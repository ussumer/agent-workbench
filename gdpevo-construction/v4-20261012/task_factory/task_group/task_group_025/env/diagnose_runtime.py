import subprocess,json,os
c='gdpevo-sourcing-025-envbuild-attempt2'
i=json.loads(subprocess.check_output(['docker','inspect',c]))[0]
token=next(x.split('=',1)[1] for x in i['Config']['Env'] if x.startswith('ERP_SERVICE_TOKEN='))
e=dict(os.environ,ERP_SERVICE_TOKEN=token)
code='''import json,os,urllib.request,urllib.error,socket
print('task-env DNS',socket.gethostbyname_ex('task-env')[2])
for p in ['/health','/api/sourcing/programs?offset=1&limit=2']:
 try:
  r=urllib.request.urlopen(urllib.request.Request('http://task-env:9025'+p,headers={'X-Service-Token':os.environ['ERP_SERVICE_TOKEN']}));b=json.load(r);print(p,r.status,b)
 except urllib.error.HTTPError as x:print(p,x.code,json.load(x))
'''
subprocess.run(['docker','run','--rm','--network','gdpevo-group025-v4','--env','ERP_SERVICE_TOKEN','python:3.12-slim','python','-c',code],env=e,check=True)
