#!/usr/bin/env python3
"""Deterministic independent whole-point business checks; no live calls."""
from pathlib import Path
import json
import sys
POINTS=[('N1','approved_volume',2),('N2','commercial_coverage',2),('N3','technical_admissibility',2),('N4','capacity_shortfall',2),('N5','launch_gap',2),('N6','financial_exposure',3)]
TOTAL=sum(p[2] for p in POINTS)

def unique_object(pairs):
 result={}
 for key,value in pairs:
  if key in result: raise ValueError('duplicate JSON object key')
  result[key]=value
 return result

def load(path):
 return json.loads(path.read_text(encoding='utf-8-sig'),object_pairs_hook=unique_object,parse_constant=lambda v: (_ for _ in ()).throw(ValueError('nonfinite JSON number')))

def identity(row):
 if isinstance(row,dict):
  return tuple((k,row.get(k)) for k in ('supplier_id','fiscal_period','variant') if k in row)
 return row

def compare(expected,actual,path):
 """Project required business fields; extra explanations/source references ignored."""
 if type(expected) is dict:
  if type(actual) is not dict: return [path+': expected object']
  errors=[]
  for k,v in expected.items():
   if k not in actual: errors.append(path+'.'+k+': missing business field')
   else: errors.extend(compare(v,actual[k],path+'.'+k))
  return errors
 if type(expected) is list:
  if type(actual) is not list or len(actual)!=len(expected): return [path+': incomplete or extra business rows']
  try:
   ekeys=[identity(r) for r in expected]; akeys=[identity(r) for r in actual]
   if len(set(akeys))!=len(akeys): return [path+': duplicate business key or set member']
   if set(ekeys)!=set(akeys): return [path+': wrong business key set']
   lookup=dict(zip(akeys,actual)); errors=[]
   for key,row in zip(ekeys,expected): errors.extend(compare(row,lookup[key],path+'['+str(key)+']'))
   return errors
  except (TypeError,ValueError): return [path+': invalid business row keys']
 if type(actual) is not type(expected): return [path+': wrong scalar type']
 if type(expected) is int and path.rsplit('.',1)[-1].endswith('_minor'):
  return [] if abs(expected-actual)<=1 else [path+': monetary value outside one-fen tolerance']
 return [] if actual==expected else [path+': incorrect business value']

def evaluate(answer,gold):
 points=[]
 context_ok=type(answer) is dict and answer.get('program_id')==gold['program_id'] and answer.get('as_of')==gold['as_of']
 for pid,key,w in POINTS:
  if not context_ok: errors=['submission: wrong program or decision date']
  elif key not in answer: errors=[key+': missing business section']
  else: errors=compare(gold[key],answer[key],key)
  passed=not errors
  points.append({'point_id':pid,'weight':w,'passed':passed,'assigned_score':w/TOTAL,'earned_score':w/TOTAL if passed else 0,'details':{'errors':errors}})
 return {'score':sum(p['weight'] for p in points if p['passed'])/TOTAL,'points':points}

def main():
 if len(sys.argv)!=2 or not Path(sys.argv[1]).is_absolute():
  print(json.dumps({'error':'Provide one absolute submission path.'}));return 2
 try:
  gold=load(Path(__file__).resolve().parents[1]/'output/answer.json')
  answer=load(Path(sys.argv[1]))
 except (ValueError,OSError) as exc:
  print(json.dumps({'error':'parse/control failure','type':type(exc).__name__}));return 2
 print(json.dumps(evaluate(answer,gold),separators=(',',':')));return 0
if __name__=='__main__': sys.exit(main())
