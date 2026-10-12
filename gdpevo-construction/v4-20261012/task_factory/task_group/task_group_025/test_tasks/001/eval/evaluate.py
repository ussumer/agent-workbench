"""Private independent reference arithmetic; no seed/gold/generator access."""
import json,sys
from pathlib import Path
from decimal import Decimal as D,ROUND_HALF_UP,getcontext
getcontext().prec=40
YEARS=[2027,2028,2029,2030]
DEMAND=[29000,34000,47000,47000]
RECOVERED=[29000,34000,37000,0]
# Independently transcribed binding Q084 sources: supplier,pieces,high prices,
# low prices,shared tooling,shared RD,period multiplier,period FX.
SOURCES=[('SUP-001',1,['296','336'],None,'4000000','600000',['1','1.025','1.05','1.075'],['1']*4),
 ('SUP-002',2,['14.1958','26.9930'],None,'265734.27','181818.18',['1','.985','.97','.955'],['7.18','7.24','7.30','7.36']),
 ('SUP-003',1,['44.3590','46.0256'],['40.8103','42.3436'],'256410.26','153846.15',['1','1.01','1.02','1.03'],['7.75','7.80','7.85','7.90'])]
CAPACITY=[[24000,28000,32000,36000],[41000,47000,53000,59000],[23000,30000,37000,44000]]
READY=['2026-11-16','2026-12-07','2027-02-08']
GAPS=[0,0,38]
POINTS=[('N1','approved_volume',2),('N2','commercial_coverage',2),('N3','technical_admissibility',2),('N4','capacity_shortfall',2),('N5','launch_gap',2),('N6','financial_exposure',3)]
def minor(x): return int((x*100).quantize(D('1'),rounding=ROUND_HALF_UP))
def reference():
    out={'approved_volume':{'uom':'vehicle_set','quantity_scope':'full_fiscal_year','annual':[{'fiscal_period':y,'base_sets':int(D(n)*D('.7')),'premium_sets':int(D(n)*D('.3')),'total_sets':n} for y,n in zip(YEARS,DEMAND)],'program_total_sets':157000},'commercial_coverage':[],'technical_admissibility':[],'capacity_shortfall':[],'launch_gap':[],'financial_exposure':{'currency':'CNY','candidates':[]}}
    for j,(sid,pieces,prices,low,tooling,rd,mults,rates) in enumerate(SOURCES):
        out['commercial_coverage'] += [{'supplier_id':sid,'fiscal_period':y,'base_covered':True,'premium_covered':True,'source_resolution':'prior_approved_after_draft' if j==1 else 'approved_amendment'} for y in YEARS]
        out['technical_admissibility'].append({'supplier_id':sid,'qualification_status':'conditional' if j==2 else 'qualified','technical_coverage':['base','premium'],'required_actions':['photometry_validation'] if j==2 else []})
        out['capacity_shortfall'] += [{'supplier_id':sid,'fiscal_period':y,'available_sets':CAPACITY[j][i],'shortfall_sets':max(0,DEMAND[i]-CAPACITY[j][i])} for i,y in enumerate(YEARS)]
        out['launch_gap'].append({'supplier_id':sid,'ready_date':READY[j],'launch_gap_days':GAPS[j],'on_time':GAPS[j]==0})
        annual=[]; upfront={}; npv={}
        for vi,v in enumerate(['base','premium']):
            initial=D(rd)*D(rates[0])/2
            upfront[v]=minor(initial); pv=initial
            for i,y in enumerate(YEARS):
                highsets=RECOVERED[i] if low else DEMAND[i]
                mix=D('.7') if vi==0 else D('.3')
                op=(D(highsets)*D(prices[vi])+D(DEMAND[i]-highsets)*D(low[vi] if low else prices[vi]))*mix*pieces*D(mults[i])*D(rates[i])
                tool=D(RECOVERED[i])*mix*D(tooling)/100000*D(rates[i])
                discounted=(op+tool)/D('1.1')**i; pv+=discounted
                annual.append({'fiscal_period':y,'variant':v,'operating_minor':minor(op),'tooling_minor':minor(tool),'discounted_cash_minor':minor(discounted)})
            npv[v]=minor(pv)
        out['financial_exposure']['candidates'].append({'supplier_id':sid,'annual_variant_cash':annual,'upfront_rd_minor':upfront,'variant_npv_minor':npv})
    return out
def compare(actual,expected,path='',money=False):
    if isinstance(expected,dict):
        if not isinstance(actual,dict): return [path+': expected object']
        errors=[]
        for k,v in expected.items():
            if k not in actual: errors.append(path+'.'+k+': missing')
            else: errors+=compare(actual[k],v,path+'.'+k,money or k.endswith('_minor'))
        return errors
    if isinstance(expected,list):
        if not isinstance(actual,list) or len(actual)!=len(expected): return [path+': wrong table cardinality']
        if expected and isinstance(expected[0],dict):
            keys=[k for k in ['supplier_id','fiscal_period','variant'] if k in expected[0]]
            def index(rows):
                result={}
                for r in rows:
                    if not isinstance(r,dict) or any(k not in r for k in keys): raise ValueError('missing key')
                    if 'fiscal_period' in keys and type(r['fiscal_period']) is not int: raise ValueError('year type')
                    key=tuple(r[k] for k in keys)
                    if key in result: raise ValueError('duplicate row')
                    result[key]=r
                return result
            try: am,em=index(actual),index(expected)
            except (ValueError,TypeError): return [path+': invalid/duplicate row key']
            if set(am)!=set(em): return [path+': wrong business coverage']
            return sum((compare(am[k],em[k],path+str(k),money) for k in em),[])
        return [] if all(type(x) is str for x in actual) and sorted(actual)==sorted(expected) else [path+': wrong set']
    if type(expected) is bool: return [] if type(actual) is bool and actual==expected else [path+': wrong boolean']
    if type(expected) is int:
        if type(actual) is not int: return [path+': expected integer']
        return [] if abs(actual-expected)<=(1 if money else 0) else [path+': numeric mismatch']
    return [] if type(actual) is type(expected) and actual==expected else [path+': wrong classification/value']
def evaluate(answer):
    if not isinstance(answer,dict): raise ValueError('submission must be JSON object')
    expected=reference(); points=[]; earned=0
    for pid,key,w in POINTS:
        errors=[]
        if answer.get('program_id')!='PRG-084' or answer.get('as_of')!='2026-10-12': errors.append('wrong decision context')
        errors+=compare(answer.get(key),expected[key],key)
        passed=not errors
        points.append({'point_id':pid,'weight':w,'passed':passed,'assigned_score':w/13,'earned_score':w/13 if passed else 0,'details':{'mismatches':errors[:20],'mismatch_count':len(errors)}})
        if passed: earned+=w
    return {'score':earned/13,'points':points}
def reject_constant(x): raise ValueError('non-finite JSON number: '+x)
def unique_object(pairs):
    obj={}
    for k,v in pairs:
        if k in obj: raise ValueError('duplicate JSON key: '+k)
        obj[k]=v
    return obj
def main():
    try:
        if len(sys.argv)!=2 or not Path(sys.argv[1]).is_absolute(): raise ValueError('one absolute submission path required')
        a=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'),parse_constant=reject_constant,object_pairs_hook=unique_object)
        print(json.dumps(evaluate(a))); return 0
    except (ValueError,OSError,UnicodeError) as e:
        print(json.dumps({'error':'parse_or_control_failure','details':str(e)})); return 2
if __name__=='__main__': sys.exit(main())
