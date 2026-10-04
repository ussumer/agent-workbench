"""Bounded real GDPevo run on the accepted v3 expansion taskset."""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys, time
from copy import deepcopy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
EVAL=Path('/mnt/c/dev/rsi-eval')
for p in (ROOT,ROOT/'src',EVAL):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
from scripts.planning.gdpevo_refinement import RealCalls
from scripts.planning.gdpevo_calibration import digest, write, FORMAT
from scripts.planning.gdpevo_expansion import actor_view
from scripts.planning.gdpevo_expansion_judge import grade_task
from agent.evolution.curator import validate_curated
PUBLIC=ROOT/'fixtures/planning/gdpevo-procurement-v3.json'
TRAIN=ROOT/'fixtures/planning/gdpevo-procurement-v3-training.json'
CONTROL=ROOT/'fixtures/planning/private/gdpevo-procurement-v3-control.json'
SESSION=EVAL/'procurement_eval/runs/planning-session-20261004/attempt-gdpevo-v3-training-20261004'

def actor_messages(public, training, task, group, knowledge=None):
    view=actor_view(public,training,task['task_id'],task['split'])
    system=('你是采购决策助手，只读公开资料，不调用工具、不下单。\n'+public['environment']['public_contract']+'\n'+FORMAT+
            '\n本题输出必须严格依据当前任务；所有商务版本、审计关系、承诺账本和审批边界都要显式核对。')
    if group=='raw-v3':
        system+='\n冻结训练经历（只作可审计经验，服从当前任务）：\n'+json.dumps(knowledge,ensure_ascii=False,separators=(',',':'))
    elif group=='curated-v3':
        system+='\n冻结文字技能（只作策略提示，服从当前任务）：\n'+knowledge['body']
    return [{'role':'system','content':system},{'role':'user','content':json.dumps(view,ensure_ascii=False)}]

def call_one(caller, directory, messages, task, rubric, group):
    response,row=caller.call(directory,messages)
    row.update(group=group,task_id=task['task_id'],split=task['split'],grade={'score':0.0,'business_success':False,'points':{}})
    value=None
    if response is not None:
        try:
            value=json.loads(response['choices'][0]['message']['content'])
            if not isinstance(value,dict): raise ValueError('decision must object')
            row['grade']=grade_task(task,value,rubric); row['status']='scored'; write(directory/'submission.json',value)
        except (ValueError,KeyError,TypeError,IndexError,json.JSONDecodeError) as e:
            row['status']='format_failed'; row['errors']=[type(e).__name__]
    write(directory/'result.json',row)
    print(f"{group}/{task['task_id']} {row['status']} {row['grade']['score']:.3f}",flush=True)
    return row,value

def compact_record(task,row,value):
    return {'task_id':task['task_id'],'group_id':task['group_id'],'policy_evidence':task.get('policy_evidence',[]),
            'decision':value or {},'feedback':{'score':row['grade']['score'],'business_success':row['grade']['business_success'],
            'failed_outcomes':[k for k,v in row['grade']['points'].items() if not v]}}

def run():
    from agent.env_utils import load_env
    for k,v in load_env(path=Path('/mnt/c/dev/rush-harness/.env')).items(): os.environ.setdefault(k,v)
    if SESSION.exists(): raise FileExistsError(SESSION)
    public=json.loads(PUBLIC.read_text()); training=json.loads(TRAIN.read_text()); control=json.loads(CONTROL.read_text())
    tasks=public['tasks']; by={t['task_id']:t for t in tasks}; train=[t for t in tasks if t['split']=='train']; test=[t for t in tasks if t['split']=='test']
    SESSION.mkdir(parents=True); (SESSION/'frozen').mkdir()
    for p in (PUBLIC,TRAIN,CONTROL,Path(__file__),ROOT/'scripts/planning/gdpevo_expansion_judge.py',ROOT/'scripts/planning/gdpevo_expansion.py'):
        (SESSION/'frozen'/p.name).write_bytes(p.read_bytes())
    caller=RealCalls(); protocol={'kind':'GDPevo v3 expansion','model_id':caller.model.model_id,'temperature':0,'thinking':'disabled','tools':[],
      'train_count':len(train),'test_count':len(test),'groups':['fixed-v3','raw-v3','curated-v3'],'selection_split':'test_development_only','production_assignment_changed':False,
      'source_hashes':{p.name:digest(p) for p in (SESSION/'frozen').iterdir()},'taskset_hash':digest(PUBLIC),'sealed_test':False}
    write(SESSION/'protocol.json',protocol)
    records=[]
    for task in train:
        row,val=call_one(caller,SESSION/'train'/task['task_id'],actor_messages(public,training,task,'fixed-v3'),task,control['rubrics'][task['task_id']],'fixed-v3')
        records.append(compact_record(task,row,val)); write(SESSION/'training-records.json',records)
    curator_system='''你是GDPevo采购文字技能Curator。只看20个train任务的公开规则、真实决策和公开失败字段；不看test、不接收私有答案。提炼可迁移的检查顺序、条件、例外、停止条件。不要记忆具体ID、价格、数量或答案，不改变权限、工具、审批或代码。只输出JSON恰好三个字段：skill_id="planning_strategy_v3"、description、body；body不超过1600字。'''
    curator_messages=[{'role':'system','content':curator_system},{'role':'user','content':json.dumps(records,ensure_ascii=False)}]
    response,curator_row=caller.call(SESSION/'curator',curator_messages)
    if response is None: raise RuntimeError('curator environment failure preserved')
    raw=response['choices'][0]['message']['content']; skill=validate_curated(raw).model_dump(mode='json'); write(SESSION/'candidate-skill.json',skill)
    rows=[]
    for group in protocol['groups']:
        knowledge=records if group=='raw-v3' else skill
        for task in test:
            row,_=call_one(caller,SESSION/group/task['task_id'],actor_messages(public,training,task,group,knowledge),task,control['rubrics'][task['task_id']],group)
            rows.append(row); write(SESSION/'report.json',{'groups':summarize(rows),'rows':rows,'curator':curator_row,'claims':{'production_actor_run':False,'weight_finetuning':False,'single_repeat':True,'sealed_test':False,'learning_gain_proven':False}})
    write(SESSION/'bank.json',{'scope':'experiment-only','production_assignment_changed':False,'candidate_promoted':False,'skill_sha256':hashlib.sha256(skill['body'].encode()).hexdigest(),'training_sha256':digest(SESSION/'training-records.json'),'skill':skill})
    write(SESSION/'manifest.json',{str(p.relative_to(SESSION)):digest(p) for p in SESSION.rglob('*') if p.is_file() and p.name!='manifest.json'})
    print(json.dumps(summarize(rows),ensure_ascii=False),flush=True)

def summarize(rows):
    out={}
    for group in sorted({r['group'] for r in rows}):
        a=[r for r in rows if r['group']==group]; out[group]={'count':len(a),'business_success':sum(r['grade']['business_success'] for r in a),'mean_score':sum(r['grade']['score'] for r in a)/len(a),'format_or_env_failures':sum(r['status']!='scored' for r in a)}
    return out

def verify():
    m=json.loads((SESSION/'manifest.json').read_text())
    for p,h in m.items():
        if digest(SESSION/p)!=h: raise ValueError('hash mismatch '+p)
    report=json.loads((SESSION/'report.json').read_text()); print(json.dumps(report['groups'],ensure_ascii=False)); return report
if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('command',choices=['run','verify']); args=ap.parse_args()
    (run() if args.command=='run' else verify())
