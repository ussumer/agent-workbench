#!/usr/bin/env python3
"""Private deterministic business-section evaluator. No live calls or source-seed access."""
import json,sys
from pathlib import Path
POINTS=[("R1","settled_principal",2),("R2","refund_claim",2),("R3","tooling_disposition",2),("R4","incremental_exposure",3),("R5","technical_admissibility",2),("R6","recovery_feasibility",2),("R7","change_scope",1)]
class ParseError(ValueError):pass
def unique(pairs):
    out={}
    for k,v in pairs:
        if k in out:raise ParseError("Duplicate JSON object key")
        out[k]=v
    return out
def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),object_pairs_hook=unique,parse_constant=lambda x:(_ for _ in ()).throw(ParseError("Nonfinite JSON number")))
def compare(actual,expected,path=""):
    if isinstance(expected,dict):
        if not isinstance(actual,dict):return path+": expected object"
        for k,v in expected.items():
            if k not in actual:return path+"/"+k+": missing business field"
            failure=compare(actual[k],v,path+"/"+k)
            if failure:return failure
        return None
    if isinstance(expected,list):
        if not isinstance(actual,list) or len(actual)!=len(expected):return path+": incomplete or duplicate row coverage"
        if not expected:return None
        if isinstance(expected[0],dict):
            keys=[k for k in ("supplier_id","category","fiscal_period") if all(k in row for row in expected)]
            if not keys:return path+": control error (unkeyed business table)"
            def index(rows):
                result={}
                for row in rows:
                    if not isinstance(row,dict) or any(k not in row for k in keys):raise ValueError()
                    key=tuple((type(row[k]).__name__,str(row[k]).strip()) for k in keys)
                    if key in result:raise ValueError()
                    result[key]=row
                return result
            try:a=index(actual);e=index(expected)
            except ValueError:return path+": invalid or duplicate business key"
            if set(a)!=set(e):return path+": incorrect business row set"
            for key in sorted(e):
                failure=compare(a[key],e[key],path+"/"+repr(key))
                if failure:return failure
            return None
        if any(type(v) is not type(expected[0]) for v in actual):return path+": wrong member type"
        if sorted(v.strip() if isinstance(v,str) else v for v in actual)!=sorted(expected):return path+": incorrect or duplicate set members"
        return None
    if type(actual) is not type(expected):return path+": wrong scalar type"
    if isinstance(expected,int):
        tolerance=1 if path.rsplit("/",1)[-1].endswith("_minor") else 0
        if abs(actual-expected)>tolerance:return path+": incorrect numeric business result"
    elif isinstance(expected,str):
        if actual.strip()!=expected:return path+": incorrect classification, identifier, unit or date"
    elif actual!=expected:return path+": incorrect business result"
    return None
def main():
    try:
        if len(sys.argv)!=2:raise ParseError("Usage: eval.sh ABSOLUTE_ANSWER_JSON")
        submission=Path(sys.argv[1])
        if not submission.is_absolute():raise ParseError("Submission path must be absolute")
        actual=read(submission)
        if not isinstance(actual,dict):raise ParseError("Submission root must be an object")
        expected=read(Path(__file__).resolve().parents[1]/"output/answer.json")
    except (ValueError,OSError) as e:
        print(json.dumps({"error":"parse_or_control_failure","details":str(e)}));return 2
    context=actual.get("program_id")==expected["program_id"] and actual.get("as_of")==expected["as_of"]
    points=[]
    for point_id,key,weight in POINTS:
        failure=compare(actual.get(key),expected[key],key) if context else "Incorrect program or as_of business context"
        passed=failure is None;assigned=weight/14
        points.append({"point_id":point_id,"weight":weight,"passed":passed,"assigned_score":assigned,"earned_score":assigned if passed else 0,"details":"Complete business goal passed" if passed else failure})
    print(json.dumps({"score":sum(p["weight"] for p in points if p["passed"])/14,"points":points}));return 0
if __name__=="__main__":sys.exit(main())
