"""Independent business-result evaluator; no answer-file, seed, env, network or model access."""
import json,sys
from decimal import Decimal as D, ROUND_HALF_UP, getcontext
getcontext().prec=40
YEARS=[2027,2028,2029,2030]
SUPPLIERS=['SUP-013','SUP-014','SUP-015']
QUANTITIES=[29000,36000,43000,41000]
VARIANTS=['base','premium']
WEIGHTS=[2,2,2,2,2,3]
KEYS=['approved_volume','commercial_coverage','technical_admissibility','capacity_shortfall','launch_gap','financial_exposure']
def cents(value):return int((value*100).quantize(D(1),rounding=ROUND_HALF_UP))
def finance_reference():
 # Private independently transcribed commercial facts. No gold or shared generator imports.
 data={
 'SUP-013':{'prices':['28.3916','35.6643'],'piece_factor':1,'multipliers':['1','1.025','1.05','1.075'],'fx':['7.18','7.24','7.30','7.36'],'tooling':'587412.59','rd':'139860.14'},
 'SUP-014':{'prices':['16.0256','21.8590'],'piece_factor':2,'multipliers':['1','0.985','0.97','0.955'],'fx':['7.79','7.84','7.89','7.94'],'tooling':'410256.41','rd':'192307.69'},
 'SUP-015':{'prices':['275','389'],'piece_factor':1,'multipliers':['1','1.01','1.02','1.03'],'fx':['1','1','1','1'],'tooling':'4000000','rd':'1200000'}}
 annual=[];rd=[];npv=[]
 # In this program the 100k tooling cap leaves 35k sets in 2029; the 120k tier leaves
 # 12k first-tier and 29k second-tier sets in 2030. These allocations are audited separately.
 recover=[29000,36000,35000,0];first=[29000,36000,43000,12000];second=[0,0,0,29000]
 for supplier in SUPPLIERS:
  row=data[supplier];variant_npvs=[]
  for i,variant in enumerate(VARIANTS):
   share=[D('0.8'),D('0.2')][i]
   development=D(row['rd'])*D(row['fx'][0])/2
   rd.append({'supplier_id':supplier,'variant':variant,'rd_minor':cents(development)})
   total=development
   for j,year in enumerate(YEARS):
    price=D(row['prices'][i]);rate=D(row['fx'][j]);multiplier=D(row['multipliers'][j])
    if supplier=='SUP-015':piece=(D(first[j])*price+D(second[j])*price*D('0.92'))*share*multiplier
    else:piece=D(QUANTITIES[j])*share*price*row['piece_factor']*multiplier
    parts=piece*rate;tools=D(recover[j])*share*D(row['tooling'])/100000*rate
    cash=parts+tools;discounted=cash/D('1.1')**j;total+=discounted
    annual.append({'supplier_id':supplier,'fiscal_period':year,'variant':variant,'parts_minor':cents(parts),'tooling_minor':cents(tools),'cashflow_minor':cents(cash),'discounted_minor':cents(discounted)})
   variant_npvs.append(total)
  npv.append({'supplier_id':supplier,'base_npv_minor':cents(variant_npvs[0]),'premium_npv_minor':cents(variant_npvs[1]),'total_npv_minor':cents(sum(variant_npvs))})
 return {'currency':'CNY','annual_cashflows':annual,'upfront_rd':rd,'candidate_npvs':npv}
def reference():
 capacities={'SUP-013':[41000,45000,49000,53000],'SUP-014':[45000,51000,57000,63000],'SUP-015':[37000,44000,51000,58000]}
 return {
 'approved_volume':{'uom':'vehicle_set','periods':[{'fiscal_period':y,'base_sets':q*8//10,'premium_sets':q*2//10,'total_sets':q} for y,q in zip(YEARS,QUANTITIES)],'four_year_total_sets':149000},
 'commercial_coverage':[{'supplier_id':s,'fiscal_period':y,'base_covered':True,'premium_covered':True,'commercially_applicable':True} for s in SUPPLIERS for y in YEARS],
 'technical_admissibility':[{'supplier_id':s,'qualification_status':'qualified','base_covered':True,'premium_covered':True,'required_actions':(['photometry_validation'] if s=='SUP-015' else [])} for s in SUPPLIERS],
 'capacity_shortfall':{'uom':'vehicle_set','periods':[{'supplier_id':s,'fiscal_period':y,'available_sets':capacities[s][j],'shortfall_sets':0} for s in SUPPLIERS for j,y in enumerate(YEARS)]},
 'launch_gap':{'launch_date':'2027-01-01','candidates':[{'supplier_id':'SUP-013','ready_date':'2026-11-23','gap_days':0,'on_time':True},{'supplier_id':'SUP-014','ready_date':'2026-12-14','gap_days':0,'on_time':True},{'supplier_id':'SUP-015','ready_date':'2027-02-15','gap_days':45,'on_time':False}]},
 'financial_exposure':finance_reference()}

def compare(actual,expected,path,errors):
 """Compare scored semantic fields only; normalize table order without allowing duplicates."""
 if isinstance(expected,dict):
  if not isinstance(actual,dict):errors.append(path+': expected object');return
  for key,value in expected.items():compare(actual.get(key),value,path+'.'+key,errors)
 elif isinstance(expected,list):
  if not isinstance(actual,list) or len(actual)!=len(expected):errors.append(path+': incomplete or duplicate table');return
  if expected and isinstance(expected[0],dict):
   id_keys=[k for k in ('supplier_id','fiscal_period','variant') if k in expected[0]]
   def index(rows):
    if any(not isinstance(r,dict) for r in rows):raise ValueError('non-object row')
    result={}
    for r in rows:
     key=tuple(r.get(k) for k in id_keys)
     if key in result:raise ValueError('duplicate business key')
     result[key]=r
    return result
   try:
    left=index(actual);right=index(expected)
    if set(left)!=set(right):errors.append(path+': wrong business row coverage');return
    for key,row in right.items():compare(left[key],row,path+str(key),errors)
   except (ValueError,TypeError):errors.append(path+': invalid business row keys')
  elif all(isinstance(x,str) for x in expected):
   if any(not isinstance(x,str) for x in actual) or len(set(actual))!=len(actual) or set(actual)!=set(expected):errors.append(path+': wrong action set')
  elif actual!=expected:errors.append(path+': wrong list')
 elif isinstance(expected,bool):
  if type(actual) is not bool or actual!=expected:errors.append(path+': wrong boolean')
 elif isinstance(expected,int):
  tolerance=1 if path.endswith('_minor') else 0
  if type(actual) is not int or abs(actual-expected)>tolerance:errors.append(path+': wrong integer business result')
 elif type(actual) is not type(expected) or actual!=expected:errors.append(path+': wrong value')
def evaluate(answer):
 expected=reference();points=[]
 context=isinstance(answer,dict) and answer.get('program_id')=='PRG-034' and answer.get('as_of')=='2026-10-12'
 for i,(key,weight) in enumerate(zip(KEYS,WEIGHTS),1):
  errors=[]
  if not context:errors.append('Missing or incorrect program/as-of context')
  else:compare(answer.get(key),expected[key],key,errors)
  passed=not errors;assigned=weight/13
  points.append({'point_id':'N'+str(i),'weight':weight,'passed':passed,'assigned_score':assigned,'earned_score':assigned if passed else 0,'details':errors if errors else ['Complete independent business result matched']})
 return {'score':sum(p['earned_score'] for p in points),'points':points}
def main():
 if len(sys.argv)!=2:print('Usage: evaluate.py <answer-path>',file=sys.stderr);return 2
 try:
  with open(sys.argv[1],encoding='utf-8-sig') as file:answer=json.load(file,parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Non-finite JSON number')))
 except (OSError,ValueError) as exc:print('Submission parse/control failure: '+str(exc),file=sys.stderr);return 2
 if not isinstance(answer,dict):print('Submission must be a JSON object',file=sys.stderr);return 2
 print(json.dumps(evaluate(answer),ensure_ascii=True));return 0
if __name__=='__main__':sys.exit(main())
