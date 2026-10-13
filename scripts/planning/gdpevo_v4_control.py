"""Trusted official OpenSandbox deployment; Actor stays on the private network."""
import contextlib,hashlib,ipaddress,json,os,re,socket,subprocess,time,uuid
from pathlib import Path


def docker(*argv):
    p=subprocess.run(['docker',*argv],capture_output=True,text=True,timeout=45)
    if p.returncode:raise RuntimeError('Docker '+ ' '.join(argv[:2])+': '+p.stderr[-500:])
    return p.stdout


def write(path,value):
    path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')


def control_config(template,network,management_ip,port):
    address=ipaddress.ip_address(management_ip)
    if address.version!=4 or not address.is_private or address.is_unspecified or address.is_loopback:
        raise ValueError('management IP must be a concrete private IPv4')
    changes={'network_mode = "bridge"':f'network_mode = "{network}"',
             'host = "127.0.0.1"':f'host = "{management_ip}"',
             'port = 18080':f'port = {port}\neip = "127.0.0.1:{port}"',
             'path = "~/.opensandbox/opensandbox.db"':'path = "/tmp/control.db"',
             'allowed_host_paths = ["~/.opensandbox/mounts"]':'allowed_host_paths = []'}
    for old,new in changes.items():
        if template.count(old)!=1:raise ValueError('sandbox template field missing or ambiguous: '+old)
        template=template.replace(old,new)
    return template


def control_create_args(config,path,management,address,name):
    image=config['sandbox_control_image']
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',image):raise ValueError('immutable control image ID required')
    return ['create','--name',name,'--label','purpose=t67','--network',management,'--ip',address,
            '-p',f"127.0.0.1:{config['sandbox_port']}:{config['sandbox_port']}",
            '--mount',f'type=bind,src={path.resolve()},dst=/run/sandbox.toml,readonly',
            '--mount','type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock',
            '-e','OPENSANDBOX_INSECURE_SERVER=YES','--entrypoint','/usr/local/bin/python',image,
            '-m','opensandbox_server.cli','--config','/run/sandbox.toml']


def cleanup_control(control,execution,management):
    result={'status':'passed','errors':[],'removed_sandboxes':[],'control_removed':False,'networks_removed':[]}
    try:
        names=docker('ps','-a','--filter','network='+execution,'--format','{{.Names}}').splitlines()
        for name in names:
            if name==control:continue
            info=json.loads(docker('inspect',name))[0]
            if not name.startswith('sandbox-') or set(info['NetworkSettings']['Networks'])!={execution}:
                raise ValueError('unexpected endpoint on owned execution network; retain '+name)
            docker('rm','-f',name);result['removed_sandboxes'].append(name)
    except Exception as e:result['errors'].append(str(e))
    try:docker('rm','-f',control);result['control_removed']=True
    except Exception as e:result['errors'].append(str(e))
    for name in (execution,management):
        try:docker('network','rm',name);result['networks_removed'].append(name)
        except Exception as e:result['errors'].append(str(e))
    if result['errors']:result['status']='failed'
    return result


@contextlib.contextmanager
def isolated_control(config,directory):
    import httpx
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    image=config['sandbox_control_image']
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',image):raise ValueError('immutable control image ID required')
    metadata=json.loads(docker('image','inspect',image))[0]
    labels=metadata.get('Config',{}).get('Labels') or {}
    source=labels.get('t67.source_snapshot_sha256','')
    if metadata['Id']!=image or not re.fullmatch('[a-f0-9]{64}',source) or labels.get('t67.server_version')!='0.2.3':
        raise ValueError('control image lacks frozen official-server source identity')
    nonce=uuid.uuid4().hex[:12];execution='t67-exec-'+nonce;management='t67-mgmt-'+nonce;control='t67-control-'+nonce
    created=[];primary=None;old=os.environ.get('T67_CONTROL_CONTAINER')
    try:
        with socket.socket() as sock:sock.bind(('127.0.0.1',config['sandbox_port']))
        docker('network','create','--internal','--opt','com.docker.network.bridge.gateway_mode_ipv4=isolated','--label','purpose=t67',execution);created.append(execution)
        docker('network','create','--label','purpose=t67',management);created.append(management)
        subnet=json.loads(docker('network','inspect',management))[0]['IPAM']['Config'][0]['Subnet']
        address=str(ipaddress.ip_network(subnet).network_address+2)
        path=directory/'sandbox.toml';path.write_text(control_config(Path(config['sandbox_template']).read_text(encoding='utf-8'),execution,address,config['sandbox_port']),encoding='utf-8')
        docker(*control_create_args(config,path,management,address,control))
        docker('network','connect',execution,control)
        docker('start',control)
        info=json.loads(docker('inspect',control))[0]
        write(directory/'control-runtime.json',{'schema_version':1,'container_id':info['Id'],'container_name':control,
            'management_ip':address,'execution_ip':info['NetworkSettings']['Networks'][execution]['IPAddress'],
            'port':config['sandbox_port'],'image_id':info['Image'],'source_snapshot_sha256':source,
            'server_version':labels['t67.server_version'],'networks':list(info['NetworkSettings']['Networks']),
            'config_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        with httpx.Client(timeout=2,trust_env=False) as client:
            deadline=time.monotonic()+45
            attempts=[]
            while True:
                try:
                    response=client.get(f"http://127.0.0.1:{config['sandbox_port']}/health")
                    attempts.append({'status_code':response.status_code})
                    if response.status_code==200:break
                except httpx.HTTPError as error:attempts.append({'error':type(error).__name__,'message':str(error)})
                if time.monotonic()>deadline:
                    write(directory/'control-health-failure.json',{'attempts':attempts,'network_settings':info['NetworkSettings']})
                    raise RuntimeError('trusted container control readiness timeout; last='+str(attempts[-1]))
                time.sleep(.5)
            if config.get('business_network'):
                docker('network','connect','--gw-priority','-1',config['business_network'],control)
                if client.get(f"http://127.0.0.1:{config['sandbox_port']}/health").status_code!=200:
                    raise RuntimeError('business network attachment broke management ingress')
                current=json.loads(docker('inspect',control))[0]
                receipt=json.loads((directory/'control-runtime.json').read_text())
                receipt['networks']=list(current['NetworkSettings']['Networks'])
                write(directory/'control-runtime.json',receipt)
        os.environ['T67_CONTROL_CONTAINER']=control
        yield execution
    except BaseException as e:
        primary=e;raise
    finally:
        if old is None:os.environ.pop('T67_CONTROL_CONTAINER',None)
        else:os.environ['T67_CONTROL_CONTAINER']=old
        try:
            log=subprocess.run(['docker','logs',control],capture_output=True,text=True,timeout=10)
            (directory/'server.log').write_text(log.stdout+log.stderr,encoding='utf-8')
        except Exception:pass
        if len(created)==2:cleanup=cleanup_control(control,execution,management)
        else:
            cleanup={'status':'passed','errors':[],'networks_removed':[]}
            for name in reversed(created):
                try:docker('network','rm',name);cleanup['networks_removed'].append(name)
                except Exception as e:cleanup['status']='failed';cleanup['errors'].append(str(e))
        try:write(directory/'control-cleanup.json',cleanup)
        except OSError as error:
            if primary is None:raise
            primary.add_note('cleanup evidence write failed: '+str(error))
        if cleanup['status']!='passed' and primary is None:raise RuntimeError('control cleanup failed: '+str(cleanup['errors']))


def business_http(config,path,params=None,token=''):
    """Real Java HTTP from trusted control NIC; no calculation or fabricated data."""
    control=config.get('active_control_container') or os.environ.get('T67_CONTROL_CONTAINER')
    if not control or not control.startswith('t67-control-'):raise ValueError('active trusted control required')
    if not path.startswith('/') or '..' in path or '?' in path:raise ValueError('invalid business path')
    payload={'url':config['business_url'].rstrip('/')+path,'params':params or {},'token':token}
    code="import json,sys,httpx;v=json.load(sys.stdin);r=httpx.get(v['url'],params=v['params'],headers={'X-Service-Token':v['token']},timeout=20,trust_env=False,follow_redirects=False);print(json.dumps({'status_code':r.status_code,'body':r.json()}))"
    p=subprocess.run(['docker','exec','-i',control,'/usr/local/bin/python','-c',code],input=json.dumps(payload),capture_output=True,text=True,timeout=25)
    if p.returncode:raise RuntimeError('trusted Java HTTP transport failed; no business data returned')
    return json.loads(p.stdout)

