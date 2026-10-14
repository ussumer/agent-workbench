"""Strict provenance for the human-authorized two-skill continuation; old runs stay failed."""
import ast,hashlib,json,os,re,shutil,subprocess,time
from pathlib import Path


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')


def pre_model_signature(text):
    functions={'actor_session','mcp_worker','mcp_client','sandbox_config','register_sourcing_read','register_profile','recorder_class','generate_skill','policy','actor_tools','business_read','model_config'}
    constants={'SYSTEM','SKILL_SYSTEM','SOURCING_READ_DESCRIPTION','EXCLUDED','LOCKED'}
    items={}
    for node in ast.parse(text).body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in functions:
            items[node.name]=ast.dump(node,include_attributes=False)
        elif isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in constants:items[target.id]=ast.dump(node.value,include_attributes=False)
    return hashlib.sha256(json.dumps(items,sort_keys=True).encode()).hexdigest()


def verify_manifest(folder):
    folder=Path(folder).resolve();manifest=read(folder/'manifest.json')
    if not isinstance(manifest,dict) or not manifest:raise ValueError('empty original manifest')
    for relative,expected in manifest.items():
        path=(folder/relative).resolve()
        if Path(relative).is_absolute() or '..' in Path(relative).parts or not path.is_relative_to(folder):raise ValueError('manifest escape')
        if not path.is_file() or digest(path)!=expected:raise ValueError('original evidence hash differs: '+relative)
    return manifest


def validate_old_status(row,session):
    if session==1:
        if row.get('status')!='generated':raise ValueError('first original skill was not generated')
    elif session==2:
        leaves=row.get('error',{}).get('causes',[])
        if row.get('status')!='failed' or len(leaves)!=1 or leaves[0].get('type')!='ValueError' or leaves[0].get('message')!='generated SKILL content/front matter missing':
            raise ValueError('second recovery is limited to proved postprocessing failure')
    else:raise ValueError('only original independent skills one and two may be reused')


def validate_observation(proof,scope,cleanup,stdio):
    if proof.get('kind')!='operator-observed-postprocessing-recovery' or proof.get('process_absent') is not True or proof.get('container_absent') is not True or proof.get('historical_stdio_closed') is not False:
        raise ValueError('operator recovery observation incomplete')
    if stdio.get('closed') is not False or cleanup.get('recycled') is not True or cleanup.get('container_absent') is not True:
        raise ValueError('historical cleanup boundary inconsistent')
    if proof.get('owner')!=scope.get('owner') or proof.get('thread')!=scope.get('thread') or proof.get('sandbox_id')!=cleanup.get('sandbox_id'):
        raise ValueError('operator observation does not match original scope')
    return True


def accounting(current,reused,prior):
    stages=[current,reused,*prior]
    costs=[s.get('cost_estimate_cny') for s in stages]
    return {'new_model_calls':current.get('model_calls',0),'reused_model_calls':reused.get('model_calls',0),
        'effective_calibration_model_calls':current.get('model_calls',0)+reused.get('model_calls',0),
        'all_attempt_model_calls':sum(s.get('model_calls',0) for s in stages),
        'all_attempt_cost_estimate_cny':sum(costs) if all(type(x) in (int,float) for x in costs) else None,
        'usage_complete':all(s.get('usage_complete') is True for s in stages),'actual_cost_cny':None}


def git_source(root,revision):
    filename='scripts/planning/gdpevo_v4_taskset.py'
    if os.name=='nt':args=['git','-C',str(root),'show',revision+':'+filename]
    else:
        pointer=(root/'.git').read_text().strip().removeprefix('gitdir: ')
        if re.match(r'^[A-Za-z]:/',pointer):pointer='/mnt/'+pointer[0].lower()+'/'+pointer[3:]
        args=['git','--git-dir='+pointer,'--work-tree='+str(root),'show',revision+':'+filename]
    return subprocess.check_output(args).decode('utf-8-sig')


def observe_closed(folder,scope):
    cleanup=read(folder/'mcp/cleanup.json');stdio=read(folder/'stdio-cleanup.json')
    sid=cleanup['sandbox_id']
    found=subprocess.check_output(['docker','ps','-aq','--filter','name=sandbox-'+sid],text=True).strip()
    processes=subprocess.check_output(['ps','-eo','pid,args'],text=True)
    active=[line for line in processes.splitlines() if 'mcp-worker' in line and scope['owner'] in line and scope['thread'] in line]
    proof={'kind':'operator-observed-postprocessing-recovery', 'owner':scope['owner'],'thread':scope['thread'],'sandbox_id':sid,
        'process_absent':not active,'container_absent':not found,'historical_stdio_closed':stdio.get('closed'),
        'observed_at_unix':time.time(),'observation_method':'Docker all-container name and ps worker owner/thread matching'}
    validate_observation(proof,scope,cleanup,stdio)
    return proof


def validate_input_and_trace(folder,body,supervision,scope,normalize):
    events=[json.loads(l) for l in (folder/'raw-trace.jsonl').read_text().splitlines() if l]
    model_inputs=[e['value'][0] for e in events if e.get('kind')=='model_input']
    if not model_inputs:raise ValueError('actual historical model trace absent')
    humans=[m for m in model_inputs[0] if m.get('type')=='human']
    if len(humans)!=1 or json.loads(humans[0]['content'])!=supervision:raise ValueError('historical generation used different train supervision')
    if not scope.get('owner') or not scope.get('thread') or scope.get('kind')!='skill':raise ValueError('historical independent scope missing')
    messages=read(folder/'messages.json')
    if not messages or messages[-1].get('type')!='ai' or messages[-1].get('response_metadata',{}).get('finish_reason')!='stop':raise ValueError('historical skill output incomplete or truncated')
    if normalize(messages[-1].get('content',''))!=body:raise ValueError('historical skill body differs from terminal model output')


def validate_recovery_mechanism(folder,scope):
    from scripts.planning import gdpevo_v4_taskset as runtime
    # Verify unchanged MCP/compute/scopes, substituting only the separately verified stdio observation.
    tools=runtime.jsonl(folder/'mcp/tools.jsonl');events=runtime.jsonl(folder/'raw-trace.jsonl')
    from collections import Counter
    observed=[]
    for event in events:
        if event.get('kind')!='tool_start':continue
        value=event['value'];arguments=value.get('inputs')
        if arguments is None:
            try:arguments=json.loads(value.get('input',''))
            except ValueError:arguments=ast.literal_eval(value.get('input',''))
        observed.append((value['name'],json.dumps(runtime.normalized_tool_request(value['name'],arguments),sort_keys=True)))
    expected=[(c['kind'],json.dumps(runtime.normalized_tool_request(c['kind'],c['request']),sort_keys=True)) for c in tools]
    if not expected or Counter(expected)!=Counter(observed):raise ValueError('historical tool requests differ')
    if any(c.get('owner')!=scope['owner'] or c.get('thread')!=scope['thread'] for c in tools):raise ValueError('historical tool scope differs')
    if not any(c['kind']=='sourcing_read' for c in tools):raise ValueError('historical Java read absent')
    executions=read(folder/'mcp/executions.json')
    if not any(e.get('status')=='completed' for e in executions) or any(e.get('owner_user_id')!=scope['owner'] or e.get('thread_id')!=scope['thread'] for e in executions):raise ValueError('historical scoped computation absent')
    inspection=read(folder/'mcp/sandbox-inspection.json');cleanup=read(folder/'mcp/cleanup.json')
    if inspection.get('internal') is not True or not inspection.get('container_id') or inspection.get('mounts') or cleanup.get('recycled') is not True or cleanup.get('container_absent') is not True:raise ValueError('historical isolation or cleanup incomplete')


def verify_source_ledger(source):
    from scripts.planning import gdpevo_v4_taskset as runtime
    run=Path(source)/'run';result=read(run/'result.json');scopes=[]
    for index,row in enumerate(result['skills'],1):
        folder=run/f'skills/session-{index}'
        scopes.append({**row,**read(folder/'scope.json'),'directory':str(folder)})
    return runtime.verify_model_ledger(run,result,scopes)


def bind_reuse(output,source_stage,config,normalize,current_runtime):
    output=Path(output);source=Path(source_stage).resolve();run=source/'run'
    manifest=verify_manifest(run);result=read(run/'result.json');old_config=read(run/'runtime.json')
    if result.get('status')!='failed' or result.get('rows') or len(result.get('skills',[]))!=2:raise ValueError('expected exact stopped two-skill source')
    for key in ('model_config','sandbox_control_image','sandbox_image','business_image','business_seed_sha256'):
        if old_config.get(key)!=config.get(key):raise ValueError('historical runtime identity differs: '+key)
    original=read(source/'protocol.json');protocol=read(output/'protocol.json')
    if original['tasks']!=protocol['tasks'] or original['runtime_versions']!=protocol['runtime_versions']:raise ValueError('historical task/scorer/runtime version differs')
    supervision=read(source/'fewshot-input.json')
    if supervision!=read(output/'fewshot-input.json'):raise ValueError('historical train inputs differ')
    frozen=read(source/'source-hashes.json')
    for name,expected in frozen.items():
        if name.endswith('/gdpevo_v4_taskset.py'):continue
        if not Path(name).is_file() or digest(name)!=expected:raise ValueError('historical source/config/image input changed: '+name)
    root=Path(current_runtime).resolve().parents[2];revision=read(source/'source-revision.json')['git_head']
    historical=git_source(root,revision);signature=pre_model_signature(historical)
    if signature!=pre_model_signature(Path(current_runtime).read_text(encoding='utf-8-sig')):raise ValueError('pre-model generation/Actor code changed')
    observations={}
    for index,row in enumerate(result['skills'],1):
        validate_old_status(row,index);folder=run/f'skills/session-{index}';scope=read(folder/'scope.json');body=normalize((folder/'final-content.txt').read_text())
        validate_input_and_trace(folder,body,supervision,scope,normalize);validate_recovery_mechanism(folder,scope)
        if index==1:
            if normalize((folder/'SKILL.md').read_text())!=body or read(folder/'stdio-cleanup.json').get('closed') is not True:raise ValueError('first generated skill differs or did not close')
        else:observations[str(index)]=observe_closed(folder,scope)
    ledger=verify_source_ledger(source)
    prior=[]
    prior_stage=source.with_name(source.name.replace('run2','run1'))
    if prior_stage.exists():
        verify_manifest(prior_stage/'run');prior_result=read(prior_stage/'run/result.json')
        prior.append({'source_stage':str(prior_stage),**prior_result['usage']})
    binding={'schema_version':1,'kind':'human-authorized exact generated-skill reuse','source_stage':str(source),
        'source_manifest_sha256':digest(run/'manifest.json'),'supervision_sha256':digest(source/'fewshot-input.json'),
        'source_git_head':revision,'original_status':'failed','skill_sessions':[1,2],
        'pre_model_signature_sha256':signature,'reused_usage':result['usage'],'prior_failed_usage':prior,
        'cleanup_observations':observations,'source_ledger_verification':ledger}
    write(output/'reuse-source.json',binding);protocol['reuse']=binding;write(output/'protocol.json',protocol)
    hashes=read(output/'source-hashes.json')
    for relative in manifest:hashes[str((run/relative).resolve())]=manifest[relative]
    for path in (run/'manifest.json',source/'protocol.json',source/'fewshot-input.json',source/'source-revision.json',Path(__file__).resolve()):hashes[str(path)]=digest(path)
    write(output/'source-hashes.json',hashes)
    return binding


def import_skills(output,run_dir,normalize):
    output=Path(output);run_dir=Path(run_dir);binding=read(output/'reuse-source.json');source=Path(binding['source_stage']);manifest=verify_manifest(source/'run')
    if digest(source/'run/manifest.json')!=binding['source_manifest_sha256']:raise ValueError('bound original manifest changed')
    rows=[];old=read(source/'run/result.json')
    for index,row in enumerate(old['skills'],1):
        validate_old_status(row,index);folder=source/f'run/skills/session-{index}';target=run_dir/f'skills/session-{index}'
        shutil.copytree(folder,target)
        body=normalize((folder/'final-content.txt').read_text());(target/'SKILL.md').write_text(body+'\n',encoding='utf-8')
        scope=read(folder/'scope.json')
        provenance={'train_only':True,'supervision_sha256':digest(output/'fewshot-input.json'),'reused_from':str(source),
            'source_session':index,'source_manifest_sha256':binding['source_manifest_sha256'],'raw_sha256':digest(folder/'final-content.txt'),
            'original_status':row['status'],'normalization':'unique complete actual SKILL, no semantic edits'}
        write(target/'provenance.json',provenance)
        if index==2:write(target/'cleanup-observation.json',binding['cleanup_observations']['2'])
        rows.append({**row,**scope,'session':index,'status':'reused_generated' if index==1 else 'reused_recovered',
            'original_status':row['status'],'ledger_source':'reused-run2','reused_from':str(source),'source_session':index,
            'path':str(target/'SKILL.md'),'sha256':digest(target/'SKILL.md')})
    return rows


def verify_reused_skill(output,folder,row,normalize):
    output=Path(output);folder=Path(folder);binding=read(output/'reuse-source.json');source=Path(binding['source_stage']);index=row['source_session'];original=source/f'run/skills/session-{index}'
    verify_manifest(source/'run')
    if digest(source/'run/manifest.json')!=binding['source_manifest_sha256']:raise ValueError('source manifest changed')
    old=read(source/'run/result.json')['skills'][index-1];validate_old_status(old,index);scope=read(original/'scope.json')
    if row.get('ledger_source')!='reused-run2' or row.get('reused_from')!=str(source) or row.get('owner')!=scope['owner'] or row.get('thread')!=scope['thread']:raise ValueError('reused skill scope/source mismatch')
    for key in ('model_calls_before','model_calls_after'):
        if row.get(key)!=old[key]:raise ValueError('reused model ledger scope changed')
    manifest=read(source/'run/manifest.json')
    prefix=f'skills/session-{index}/'
    for relative,expected in manifest.items():
        if not relative.startswith(prefix):continue
        name=relative[len(prefix):]
        if name in {'SKILL.md','provenance.json'}:continue
        if digest(folder/name)!=expected:raise ValueError('derived source evidence changed')
    if normalize((folder/'SKILL.md').read_text())!=normalize((original/'final-content.txt').read_text()) or row.get('sha256')!=digest(folder/'SKILL.md'):raise ValueError('reused skill semantic body changed')
    provenance=read(folder/'provenance.json')
    if provenance.get('train_only') is not True or provenance.get('supervision_sha256')!=digest(output/'fewshot-input.json') or provenance.get('raw_sha256')!=digest(original/'final-content.txt'):raise ValueError('reused train provenance invalid')
    validate_input_and_trace(original,normalize((folder/'SKILL.md').read_text()),read(output/'fewshot-input.json'),scope,normalize)
    validate_recovery_mechanism(original,scope)
    if index==2:
        proof=read(folder/'cleanup-observation.json')
        if proof!=binding['cleanup_observations']['2']:raise ValueError('recovery observation changed')
        validate_observation(proof,scope,read(original/'mcp/cleanup.json'),read(original/'stdio-cleanup.json'))
        return proof
    if read(folder/'stdio-cleanup.json').get('closed') is not True:raise ValueError('first reused session did not close')
    return {'historical_stdio_closed':True,'owner':scope['owner'],'thread':scope['thread'],'kind':'original successful independent generation'}
