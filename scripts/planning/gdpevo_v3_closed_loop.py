"""One bounded full GDPevo loop on v3: repair -> curate -> train select -> held-out test."""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, sys
from copy import deepcopy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; EVAL=Path('/mnt/c/dev/rsi-eval')
for p in (ROOT,ROOT/'src',EVAL):
 if str(p) not in sys.path: sys.path.insert(0,str(p))
from scripts.planning.gdpevo_refinement import RealCalls
from scripts.planning.gdpevo_calibration import digest,write
from scripts.planning.gdpevo_expansion import actor_view
from scripts.planning.gdpevo_expansion_judge import grade_task
from scripts.planning.gdpevo_feedback import diagnose_submission,arithmetic_audit
from scripts.planning.gdpevo_v3_training import actor_messages,call_one,summarize
from agent.evolution.curator import validate_curated
from agent.evolution.refinement import select_candidate
PUBLIC=ROOT/'fixtures/planning/gdpevo-procurement-v3.json'; TRAIN=ROOT/'fixtures/planning/gdpevo-procurement-v3-training.json'; CONTROL=ROOT/'fixtures/planning/private/gdpevo-procurement-v3-control.json'
OLD=EVAL/'procurement_eval/runs/planning-session-20261004/attempt-gdpevo-v3-training-20261004'; SESSION=EVAL/'procurement_eval/runs/planning-session-20261004/attempt-gdpevo-v3-closed-loop-20261004'
FIELDS=('procurement','source_selection','freight_audit','commitment_ledger','approval_boundary','erp_claim_conflicts')
def feedback(task,row,value):
 points=row['grade'].get('points',{}); failed=[f for f in FIELDS if not points.get(task['task_id']+':'+f,False)]
 return {'success':bool(row['grade'].get('business_success')),'failed_outcomes':failed,'diagnostics':diagnose_submission(task,value),'arithmetic_audit':arithmetic_audit(task,value),'scope_note':'只诊断当前候选，不提供全局答案；候选非法不等于整体无解。','feedback_kind':'public-constraint-diagnostic-no-gold'}
def compact(task,row,value): return {'task_id':task['task_id'],'group_id':task['group_id'],'decision':value or {},'grade':row['grade'],'feedback':feedback(task,row,value)}
def build_curator_records(training, records):
    """Join train policy evidence with public attempts, never test answers."""
    result=[]
    for r in records:
        task=r['task']
        evidence=training['tasks'].get(task['task_id'],{}).get('policy_evidence',[])
        if task.get('split')!='train' or not evidence:
            raise ValueError(f"missing train policy evidence: {task.get('task_id')}")
        result.append({'task_id':task['task_id'],'group_id':task['group_id'],
          'policy_evidence':evidence,
          'attempts':[{'decision':a.get('model_output'),'grade':a['grade'],'feedback':a['feedback']} for a in r['attempts']]})
    return result


def run():
 from agent.env_utils import load_env
 for k,v in load_env(path=Path('/mnt/c/dev/rush-harness/.env')).items(): os.environ.setdefault(k,v)
 if SESSION.exists(): raise FileExistsError(SESSION)
 public=json.loads(PUBLIC.read_text()); training=json.loads(TRAIN.read_text()); control=json.loads(CONTROL.read_text()); tasks=public['tasks']; train=[t for t in tasks if t['split']=='train']; test=[t for t in tasks if t['split']=='test']; old=json.loads((OLD/'training-records.json').read_text())
 SESSION.mkdir(parents=True); (SESSION/'frozen').mkdir()
 for p in (PUBLIC,TRAIN,CONTROL,Path(__file__),ROOT/'scripts/planning/gdpevo_feedback.py',ROOT/'scripts/planning/gdpevo_expansion_judge.py'):
  (SESSION/'frozen'/p.name).write_bytes(p.read_bytes())
 caller=RealCalls(); records=[]
 # Reuse original train outputs; repair each failed one once, never retry successes.
 for task,oldrec in zip(train,old,strict=True):
  raw_path=OLD/'train'/task['task_id']/'response.txt'; raw=json.loads(raw_path.read_text())['choices'][0]['message']['content']
  value=json.loads(raw); base={'task_id':task['task_id'],'group':'fixed-v3','split':'train','grade':grade_task(task,value,control['rubrics'][task['task_id']]),'status':'scored'}
  # metrics are only used in candidate selection; original call metrics are preserved from result.
  base.update(json.loads((OLD/'train'/task['task_id']/'result.json').read_text()))
  attempts=[{'attempt':1,'model_output':raw,'grade':base['grade'],'feedback':feedback(task,base,value),'status':'scored'}]
  if not base['grade']['business_success']:
   msgs=actor_messages(public,training,task,'fixed-v3')+[{'role':'assistant','content':raw},{'role':'user','content':'这是公开诊断，不含参考答案。请逐项复核并输出完整最终JSON：'+json.dumps(attempts[-1]['feedback'],ensure_ascii=False)}]
   row,val=call_one(caller,SESSION/'repair'/task['task_id'],msgs,task,control['rubrics'][task['task_id']],'repair-v3')
   if val is not None: attempts.append({'attempt':2,'model_output':json.dumps(val,ensure_ascii=False),'grade':row['grade'],'feedback':feedback(task,row,val),'status':row['status']})
  records.append({'task':task,'attempts':attempts}); write(SESSION/'training-records.json',records)
 # Curator sees only public train task rules and attempts/feedback.
 curator_records=build_curator_records(training,records)
 prompt='''你是GDPevo文字技能Curator。只看train公开规则、真实输出和公开诊断字段，不看test和私有答案。提炼条件、顺序、例外、停止条件；不记忆任何实例ID、报价ID、供应商ID、数字、价格、数量、revision或拉丁字段名。body只能用中文文字和中文标点，禁止阿拉伯数字与拉丁字母。不要改变权限、工具、审批或代码。只输出JSON恰好三个字段：skill_id为planning_strategy_v3，description字符串，body字符串且不超过一千六百字。'''
 response,curator_row=caller.call(SESSION/'curator',[{'role':'system','content':prompt},{'role':'user','content':json.dumps(curator_records,ensure_ascii=False)}])
 if response is None: raise RuntimeError('curator failed')
 skill=validate_curated(response['choices'][0]['message']['content']).model_dump(mode='json'); write(SESSION/'candidate-skill.json',skill)
 # Train-only matched validation and selection.
 trials=[]
 for group,knowledge in (('fixed-train',None),('candidate-train',skill)):
  for task in train:
   row,_=call_one(caller,SESSION/'validation'/group/task['task_id'],actor_messages(public,training,task,'fixed-v3' if group=='fixed-train' else 'curated-v3',knowledge or skill),task,control['rubrics'][task['task_id']],group); row['split']='train'; trials.append(row); write(SESSION/'validation.json',trials)
 selection=select_candidate([r for r in trials if r['group']=='fixed-train'],[r for r in trials if r['group']=='candidate-train']); write(SESSION/'selection.json',selection)
 # Held-out comparison, no feedback/retry; candidate is measured even if rejected.
 rows=[]
 for group,knowledge in (('fixed-closed-test',None),('candidate-closed-test',skill)):
  for task in test:
   row,_=call_one(caller,SESSION/group/task['task_id'],actor_messages(public,training,task,'fixed-v3' if group=='fixed-closed-test' else 'curated-v3',knowledge or skill),task,control['rubrics'][task['task_id']],group); rows.append(row); write(SESSION/'report.json',{'groups':summarize(rows),'rows':rows,'selection':selection,'curator':curator_row,'claims':{'production_actor_run':False,'weight_finetuning':False,'test_feedback':False,'learning_gain_proven':False,'selection_split':'train'}})
 write(SESSION/'bank.json',{'scope':'experiment-only','production_assignment_changed':False,'candidate_promoted':selection['promote'],'selected_arm':'candidate' if selection['promote'] else 'fixed','selection':selection,'skill':skill,'skill_sha256':hashlib.sha256(skill['body'].encode()).hexdigest()})
 write(SESSION/'protocol.json',{'kind':'GDPevo v3 closed loop','model_id':caller.model.model_id,'reused_original_train':str(OLD),'train_repair_max':1,'selection_split':'train','test_feedback':False,'production_assignment_changed':False,'source_hashes':{p.name:digest(p) for p in (SESSION/'frozen').iterdir()}})
 write(SESSION/'manifest.json',{str(p.relative_to(SESSION)):digest(p) for p in SESSION.rglob('*') if p.is_file() and p.name!='manifest.json'})
 print(json.dumps({'selection':selection,'groups':summarize(rows)},ensure_ascii=False))
def verify():
 m=json.loads((SESSION/'manifest.json').read_text())
 for p,h in m.items():
  if digest(SESSION/p)!=h: raise ValueError('hash mismatch '+p)
 print(json.dumps(json.loads((SESSION/'report.json').read_text()),ensure_ascii=False)[:4000])
if __name__=='__main__':
 ap=argparse.ArgumentParser(); ap.add_argument('command',choices=['run','verify']); a=ap.parse_args(); run() if a.command=='run' else verify()
