"""Private atomic business evaluator for PRG-107. No source/environment access."""
import json
import sys
from pathlib import Path
POINTS=[('N1','approved_volume',2),('N2','commercial_coverage',2),('N3','technical_admissibility',2),('N4','capacity_shortfall',2),('N5','launch_gap',2),('N6','financial_exposure',3)]
GOLD=Path(__file__).resolve().parents[1]/'output/answer.json'
def compare(got,expected,path=''):
    """Project required business facts; normalize keyed tables and unordered sets."""
    if isinstance(expected,dict):
        if not isinstance(got,dict):return [path+': expected object']
        errors=[]
        for key,value in expected.items():
            errors+=compare(got.get(key),value,path+'/'+key)
        return errors
    if isinstance(expected,list):
        if not isinstance(got,list):return [path+': expected array']
        if len(got)!=len(expected):return [path+': missing or duplicate business rows']
        if expected and isinstance(expected[0],dict):
            keys=[k for k in ('supplier_id','fiscal_period','variant') if k in expected[0]]
            if not keys:return [path+': evaluator table identity not declared']
            def index(rows):
                out={}
                for row in rows:
                    if not isinstance(row,dict):raise ValueError('non-object row')
                    identity=tuple(row.get(k) for k in keys)
                    # Keep boolean and integer identifiers distinct.
                    if any(type(row.get(k)) is not type(expected[0][k]) for k in keys):raise ValueError('wrong identity type')
                    if identity in out:raise ValueError('duplicate identity')
                    out[identity]=row
                return out
            try:gmap=index(got);emap=index(expected)
            except (ValueError,TypeError):return [path+': invalid or duplicate identity']
            if gmap.keys()!=emap.keys():return [path+': wrong business row membership']
            errors=[]
            for identity in emap:errors+=compare(gmap[identity],emap[identity],path+'/'+str(identity))
            return errors
        if any(type(x) is not type(y) for x,y in zip(sorted(got,key=repr),sorted(expected,key=repr))):return [path+': wrong set member type']
        return [] if sorted(got,key=repr)==sorted(expected,key=repr) else [path+': wrong set membership']
    if type(got) is not type(expected):return [path+': wrong scalar type']
    tolerance=1 if path.rsplit('/',1)[-1].endswith('_minor') else 0
    ok=abs(got-expected)<=tolerance if type(expected) is int else got==expected
    return [] if ok else [path+': incorrect business value']
def evaluate(answer):
    expected=json.loads(GOLD.read_text(encoding='utf-8'))
    context=isinstance(answer,dict) and answer.get('program_id')==expected['program_id'] and answer.get('as_of')==expected['as_of']
    points=[];earned=0
    for pid,key,weight in POINTS:
        errors=compare(answer.get(key),expected[key],key) if context else ['incorrect program/date context']
        passed=not errors;assigned=weight/13
        if passed:earned+=weight
        points.append({'point_id':pid,'weight':weight,'passed':passed,'assigned_score':assigned,'earned_score':assigned if passed else 0,'details':errors or ['complete business conclusion matches']})
    return {'score':earned/13,'points':points}
def main():
    try:
        if len(sys.argv)!=2:raise ValueError('Expected one absolute submission path')
        path=Path(sys.argv[1])
        if not path.is_absolute():raise ValueError('Submission path must be absolute')
        answer=json.loads(path.read_text(encoding='utf-8-sig'))
        print(json.dumps(evaluate(answer),allow_nan=False));return 0
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print(json.dumps({'error':'parse_or_control_failure','details':str(exc)}));return 2
if __name__=='__main__':sys.exit(main())
