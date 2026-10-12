#!/usr/bin/env python3
import json,sys
from pathlib import Path
POINTS=[('R1','settled_principal',2),('R2','refund_claim',2),('R3','tooling_disposition',2),('R4','incremental_exposure',3),('R5','technical_admissibility',2),('R6','recovery_feasibility',2),('R7','change_scope',1)]
def unique_object(pairs):
 obj={}
 for k,v in pairs:
  if k in obj:raise ValueError('duplicate JSON object key')
  obj[k]=v
 return obj
def check(got,want,path,key=''):
 # Compare only normalized business fields for this point; extra evidence/narrative is unscored.
 if isinstance(want,dict):
  if not isinstance(got,dict):return [path+': expected object']
  errors=[]
  for k,v in want.items():
   if k not in got:errors.append(path+'.'+k+': missing business field')
   else:errors+=check(got[k],v,path+'.'+k,k)
  return errors
 if isinstance(want,list):
  if not isinstance(got,list):return [path+': expected array']
  if want and isinstance(want[0],dict):
   identity=next((k for k in ['supplier_id','cost_category','fiscal_period'] if k in want[0]),None)
   if not identity:return [path+': unsupported control schema']
   table={}
   for row in got:
    if not isinstance(row,dict) or identity not in row:return [path+': row missing identity']
    id=row[identity]
    if type(id)!=type(want[0][identity]):return [path+': wrong identity type']
    if id in table:return [path+': duplicate business row']
    table[id]=row
   targets={r[identity]:r for r in want}
   if set(table)!=set(targets):return [path+': incorrect business row coverage']
   return [err for id in targets for err in check(table[id],targets[id],path+'['+str(id)+']')]
  if any(type(v)!=str for v in got):return [path+': set elements must be strings']
  if len(got)!=len(set(got)):return [path+': duplicate set item']
  return [] if set(got)==set(want) else [path+': incorrect business set']
 if type(want)==int:
  if type(got)!=int:return [path+': expected integer']
  tolerance=1 if key.endswith('_minor') else 0
  return [] if abs(got-want)<=tolerance else [path+': incorrect monetary result' if tolerance else path+': incorrect exact integer']
 if type(got)!=type(want) or got!=want:return [path+': incorrect business classification or date']
 return []
def main():
 try:
  if len(sys.argv)!=2:raise ValueError('usage: eval.sh ABSOLUTE_ANSWER_PATH')
  p=Path(sys.argv[1])
  if not p.is_absolute():raise ValueError('submission path must be absolute')
  answer=json.loads(p.read_text(encoding='utf-8-sig'),object_pairs_hook=unique_object,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite JSON constant')))
  if not isinstance(answer,dict):raise ValueError('submission root must be an object')
  gold=json.loads((Path(__file__).resolve().parent.parent/'output/answer.json').read_text(encoding='utf-8'))
 except (ValueError,OSError,json.JSONDecodeError) as e:
  print(json.dumps({'error':'parse_or_control_failure','details':str(e)}));return 2
 points=[];score=0
 for id,key,weight in POINTS:
  errors=[]
  if answer.get('program_id')!=gold['program_id'] or answer.get('as_of')!=gold['as_of']:errors.append('wrong program or knowledge cutoff')
  if key not in answer:errors.append(key+': missing business result')
  else:errors+=check(answer[key],gold[key],key)
  passed=not errors;assigned=weight/14;earned=assigned if passed else 0
  points.append({'point_id':id,'weight':weight,'passed':passed,'assigned_score':assigned,'earned_score':earned,'details':errors or ['business result reconciled']});score+=earned
 print(json.dumps({'score':round(score,12),'points':points},ensure_ascii=True));return 0
if __name__=='__main__':sys.exit(main())
