"""One bounded Curator repair plus sealed v3 test ablations; reuses paid train calls."""
from __future__ import annotations
import argparse, hashlib, json, os, sys, shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; EVAL=Path('/mnt/c/dev/rsi-eval')
for p in (ROOT,ROOT/'src',EVAL):
 if str(p) not in sys.path: sys.path.insert(0,str(p))
from scripts.planning.gdpevo_refinement import RealCalls
from scripts.planning.gdpevo_calibration import digest,write
from scripts.planning.gdpevo_expansion import actor_view
from scripts.planning.gdpevo_expansion_judge import grade_task
from scripts.planning.gdpevo_v3_training import actor_messages,call_one,summarize
from agent.evolution.curator import validate_curated
PUBLIC=ROOT/'fixtures/planning/gdpevo-procurement-v3.json'; TRAIN=ROOT/'fixtures/planning/gdpevo-procurement-v3-training.json'; CONTROL=ROOT/'fixtures/planning/private/gdpevo-procurement-v3-control.json'
OLD=EVAL/'procurement_eval/runs/planning-session-20261004/attempt-gdpevo-v3-training-20261004'; SESSION=EVAL/'procurement_eval/runs/planning-session-20261004/attempt-gdpevo-v3-expanded-20261004'
def run():
 from agent.env_utils import load_env
 for k,v in load_env(path=Path('/mnt/c/dev/rush-harness/.env')).items(): os.environ.setdefault(k,v)
 if SESSION.exists(): raise FileExistsError(SESSION)
 public=json.loads(PUBLIC.read_text()); training=json.loads(TRAIN.read_text()); control=json.loads(CONTROL.read_text()); old=json.loads((OLD/'training-records.json').read_text())
 tasks=public['tasks']; test=[t for t in tasks if t['split']=='test']; SESSION.mkdir(parents=True); (SESSION/'frozen').mkdir()
 for p in (PUBLIC,TRAIN,CONTROL,Path(__file__),ROOT/'scripts/planning/gdpevo_v3_training.py',ROOT/'scripts/planning/gdpevo_expansion_judge.py'):
  (SESSION/'frozen'/p.name).write_bytes(p.read_bytes())
 shutil.copy2(OLD/'training-records.json',SESSION/'training-records.json')
 caller=RealCalls()
 records=[{'task_id':r['task_id'],'group_id':r['group_id'],'decision':r.get('decision',{}),'feedback':r.get('feedback',{})} for r in old]
 prompt='''你是GDPevo文字技能Curator。材料只有train任务的公开规则、真实输出和公开失败字段。只提炼可迁移的检查顺序、条件、例外、停止条件；不看test，不使用私有答案。不记忆任何实例ID、报价ID、供应商ID、数字、价格、数量、revision或英文字段名；body只能用中文文字和中文标点，禁止阿拉伯数字与拉丁字母。不要改变权限、工具、审批或代码。只输出JSON恰好三个字段：skill_id为planning_strategy_v3，description为字符串，body为不超过一千六百字的字符串。''' 
 response,curator_row=caller.call(SESSION/'curator-repair',[{'role':'system','content':prompt},{'role':'user','content':json.dumps(records,ensure_ascii=False)}])
 if response is None: raise RuntimeError('curator repair environment failure')
 raw=response['choices'][0]['message']['content']; skill=validate_curated(raw).model_dump(mode='json'); write(SESSION/'candidate-skill.json',skill)
 rows=[]
 raw_memory=[{'task_id':r['task_id'],'group_id':r['group_id'],'decision':r.get('decision',{}),'feedback':r.get('feedback',{})} for r in old]
 for group in ('fixed-v3','raw-v3','curated-v3'):
  knowledge=raw_memory if group=='raw-v3' else skill
  for task in test:
   row,_=call_one(caller,SESSION/group/task['task_id'],actor_messages(public,training,task,group,knowledge),task,control['rubrics'][task['task_id']],group)
   rows.append(row); write(SESSION/'report.json',{'groups':summarize(rows),'rows':rows,'curator':curator_row,'claims':{'production_actor_run':False,'weight_finetuning':False,'single_repeat':True,'sealed_test':False,'learning_gain_proven':False,'reused_train_attempts':True}})
 write(SESSION/'bank.json',{'scope':'experiment-only','production_assignment_changed':False,'candidate_promoted':False,'skill_sha256':hashlib.sha256(skill['body'].encode()).hexdigest(),'skill':skill,'prior_failed_curator':str(OLD/'curator/response.txt')})
 write(SESSION/'protocol.json',{'kind':'GDPevo v3 expanded ablation','model_id':caller.model.model_id,'groups':['fixed-v3','raw-v3','curated-v3'],'test_count':len(test),'test_feedback':False,'selection_split':'train_only','reused_learning':str(OLD),'production_assignment_changed':False,'source_hashes':{p.name:digest(p) for p in (SESSION/'frozen').iterdir()}})
 write(SESSION/'manifest.json',{str(p.relative_to(SESSION)):digest(p) for p in SESSION.rglob('*') if p.is_file() and p.name!='manifest.json'})
 print(json.dumps(summarize(rows),ensure_ascii=False))
def verify():
 m=json.loads((SESSION/'manifest.json').read_text())
 for p,h in m.items():
  if digest(SESSION/p)!=h: raise ValueError('hash mismatch '+p)
 print(json.dumps(json.loads((SESSION/'report.json').read_text())['groups'],ensure_ascii=False))
if __name__=='__main__':
 ap=argparse.ArgumentParser(); ap.add_argument('command',choices=['run','verify']); a=ap.parse_args(); run() if a.command=='run' else verify()
