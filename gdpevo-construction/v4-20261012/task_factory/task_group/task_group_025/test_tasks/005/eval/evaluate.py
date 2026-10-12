#!/usr/bin/env python3
"""Private deterministic business projection checks; no live data/model access."""
import json,sys
from pathlib import Path
KEYS=['approved_volume','commercial_coverage','technical_admissibility','capacity_shortfall','launch_gap','financial_exposure']
def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('duplicate JSON object key')
        result[key]=value
    return result
def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'),object_pairs_hook=unique_object,parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite JSON number')))
def compare(actual,expected,path):
    if type(actual) is not type(expected): return [path+': wrong type']
    if isinstance(expected,dict):
        errors=[]
        for k,v in expected.items():
            if k not in actual: errors.append(path+'.'+k+': missing')
            else: errors+=compare(actual[k],v,path+'.'+k)
        return errors
    if isinstance(expected,list):
        if len(actual)!=len(expected):return [path+': wrong membership']
        if not expected:return []
        if isinstance(expected[0],dict):
            names=[k for k in ['supplier_id','fiscal_period','variant'] if k in expected[0]]
            if not names:return [path+': invalid reference table']
            def index(rows):
                d={}
                for row in rows:
                    if not isinstance(row,dict):raise ValueError()
                    key=tuple((type(row[k]).__name__,row[k]) for k in names)
                    if key in d:raise ValueError()
                    d[key]=row
                return d
            try:a,e=index(actual),index(expected)
            except (KeyError,ValueError,TypeError):return [path+': invalid or duplicate identity']
            if a.keys()!=e.keys():return [path+': wrong membership']
            return [error for key in e for error in compare(a[key],e[key],path+'['+str(key)+']')]
        try:
            if len(set(actual))!=len(actual) or any(type(x) is not type(expected[0]) for x in actual):return [path+': invalid set members']
            return [] if sorted(actual)==sorted(expected) else [path+': wrong set']
        except TypeError:return [path+': invalid set']
    if type(expected) is int and path.endswith('_minor'):
        return [] if abs(actual-expected)<=1 else [path+': money outside tolerance']
    return [] if actual==expected else [path+': incorrect business value']
def evaluate(answer,gold):
    scope=isinstance(answer,dict) and type(answer.get('program_id')) is str and answer.get('program_id')==gold['program_id'] and type(answer.get('as_of')) is str and answer.get('as_of')==gold['as_of']
    points=[]
    for i,key in enumerate(KEYS):
        errors=compare(answer.get(key),gold[key],key) if scope else ['incorrect decision context']
        weight=3 if i==5 else 2;passed=not errors
        points.append({'point_id':'N'+str(i+1),'weight':weight,'passed':passed,'assigned_score':weight/13,'earned_score':weight/13 if passed else 0,'details':{'failed_paths':errors[:12]}})
    return {'score':sum(p['weight'] for p in points if p['passed'])/13,'points':points}
def main():
    try:
        if len(sys.argv)!=2 or not Path(sys.argv[1]).is_absolute():raise ValueError('one absolute submission path required')
        answer=read(sys.argv[1]);gold=read(Path(__file__).resolve().parents[1]/'output/answer.json')
        print(json.dumps(evaluate(answer,gold),allow_nan=False));return 0
    except (OSError,ValueError,TypeError) as error:
        print(json.dumps({'error':'parse_or_control_failure','details':str(error)}));return 2
if __name__=='__main__':sys.exit(main())
