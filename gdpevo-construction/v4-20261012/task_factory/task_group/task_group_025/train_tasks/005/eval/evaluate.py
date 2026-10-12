#!/usr/bin/env python3
"""Private deterministic business-result evaluator; seven independent whole points."""
import json,sys
from pathlib import Path
POINTS=[("R1",2,"settled_principal"),("R2",2,"refund_claim"),("R3",2,"tooling_disposition"),("R4",3,"incremental_exposure"),("R5",2,"technical_admissibility"),("R6",2,"recovery_feasibility"),("R7",1,"change_scope")]
def reject_constant(x): raise ValueError("Nonfinite JSON number")
def no_duplicates(pairs):
 out={}
 for k,v in pairs:
  if k in out: raise ValueError("Duplicate object key")
  out[k]=v
 return out
def read(path):return json.loads(path.read_text(encoding="utf-8-sig"),parse_constant=reject_constant,object_pairs_hook=no_duplicates)
def compare(actual,expected,path):
 if isinstance(expected,bool):return [] if type(actual) is bool and actual==expected else [path+": strict boolean mismatch"]
 if isinstance(expected,int):
  tol=1 if path.split(".")[-1].endswith("_minor") else 0
  return [] if type(actual) is int and abs(actual-expected)<=tol else [path+": integer/amount mismatch"]
 if isinstance(expected,str):return [] if type(actual) is str and actual.strip()==expected else [path+": value mismatch"]
 if isinstance(expected,dict):
  if not isinstance(actual,dict):return [path+": missing object"]
  failures=[]
  for k,v in expected.items():
   if k not in actual:failures.append(path+"."+k+": missing field")
   else:failures.extend(compare(actual[k],v,path+"."+k))
  return failures
 if isinstance(expected,list):
  if not isinstance(actual,list) or len(actual)!=len(expected):return [path+": collection coverage mismatch"]
  if not expected:return []
  if isinstance(expected[0],dict):
   key=next((k for k in ("supplier_id","fiscal_period","category","variant") if k in expected[0]),None)
   if key is None:return [path+": internal control key absent"]
   def index(rows):
    if any(not isinstance(r,dict) or key not in r or type(r[key]) is not type(expected[0][key]) for r in rows):return None
    keys=[r[key] for r in rows]
    if len(set(keys))!=len(keys):return None
    return {r[key]:r for r in rows}
   ai=index(actual);ei=index(expected)
   if ai is None or set(ai)!=set(ei):return [path+": row keys mismatch or duplicates"]
   return [err for k in ei for err in compare(ai[k],ei[k],path+"["+str(k)+"]")]
  if any(type(x) is not type(expected[0]) for x in actual):return [path+": item type mismatch"]
  if len(set(actual))!=len(actual) or sorted(actual)!=sorted(expected):return [path+": set mismatch"]
  return []
 return [path+": unsupported reference type"]
def evaluate(answer,gold):
 if not isinstance(answer,dict):raise ValueError("Submission must be a JSON object")
 prerequisites=[]
 for k in ("program_id","as_of"):
  prerequisites.extend(compare(answer.get(k),gold[k],k))
 points=[]
 for point,weight,section in POINTS:
  errors=prerequisites+compare(answer.get(section),gold[section],section)
  assigned=weight/14
  points.append({"point_id":point,"weight":weight,"passed":not errors,"assigned_score":assigned,"earned_score":assigned if not errors else 0.0,"details":"All required business results match." if not errors else "; ".join(errors[:12])})
 return {"score":sum(p["weight"] for p in points if p["passed"])/14,"points":points}
def main():
 try:
  if len(sys.argv)!=2 or not Path(sys.argv[1]).is_absolute():raise ValueError("Expected one absolute submission path")
  gold=read(Path(__file__).resolve().parent.parent/"output/answer.json")
  result=evaluate(read(Path(sys.argv[1])),gold)
 except (ValueError,OSError,KeyError,TypeError) as exc:
  print(json.dumps({"error":"parse_or_control_failure","details":str(exc)}));return 2
 print(json.dumps(result));return 0
if __name__=="__main__":sys.exit(main())
