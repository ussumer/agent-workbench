"""PRIVATE deterministic procurement grader; never an Actor tool or production planner.
Two enumeration paths: the old v2 material-choice oracle is a reference;
the cross-check enumerates integer counts per raw offer independently.
"""
from __future__ import annotations
from copy import deepcopy
from itertools import product
from math import prod
from scripts.planning.gdpevo_oracle import solve_task as v2_reference


def source_selection(data):
    # Deliberately independent of v2 sources() and reference_input().
    current={}
    for q in data['quotes']:
        if q['status'] not in ('signed','pending','revoked') or q['effective_at']>data['as_of'] or q['expires_at']<data['as_of']:
            continue
        key=(q['part_id'],q['supplier_id'])
        if key not in current or q['revision']>current[key]['revision']:
            current[key]=q
        elif q['revision']==current[key]['revision']:
            raise ValueError('ambiguous formal revision')
    relationships={(r['part_id'],r['supplier_id']):r for r in data['erp_relationships']}
    view={f'{p}:{s}':{'quote_id':q['quote_id'],'status':q['status'],'erp_active':relationships[p,s]['active'],'erp_lead_days':relationships[p,s]['lead_days']} for (p,s),q in current.items()}
    return current,relationships,view


def _integer_search(data,mode,limit=100_000):
    selected,rels,_=source_selection(data)
    committed={d['part_id']:sum(c['quantity'] for c in data['commitments'] if c['part_id']==d['part_id']) for d in data['demands']}
    paid=sum(c['paid_cents'] for c in data['commitments'])
    demands={d['part_id']:d for d in data['demands']}
    if paid>data['budget_cents'] or any(committed[p]>d['quantity'] for p,d in demands.items()): return None,[],0
    if any(c['lead_days']>demands[c['part_id']]['max_lead_days'] for c in data['commitments']): return None,[],0
    quotes=[]; ranges=[]
    for key,q in selected.items():
        if key[0] not in demands: continue  # Independent catalogue audit, never a purchase line.
        r=rels[key]; d=demands[key[0]]; remaining=d['quantity']-committed[key[0]]
        if (remaining<=0 or not r['active'] or r['lead_days']>d['max_lead_days'] or q['status']=='revoked' or (q['status']=='pending' and mode=='known')): continue
        maximum=(remaining+q['pack_size']-1)//q['pack_size']
        quotes.append(q); ranges.append(range(maximum+1))
    size=prod(len(x) for x in ranges)
    if size>limit: raise ValueError(f'independent integer enumeration bound exceeded: {size}')
    # Resolve kit applicability here, without the reference adapter.
    kits={}
    for p,d in demands.items():
        k=d['kit_id']
        if k is None or ('kit_sites' in d and data['site'] not in d['kit_sites']): continue
        exempt=any(w['part_id']==p and w['kit_id']==k and w['site']==data['site'] and w['status']=='signed' and w['effective_at']<=data['as_of']<=w['expires_at'] for w in data.get('kit_waivers', []))
        if not exempt: kits.setdefault(k,[]).append(p)
    priorities=sorted({d['priority'] for d in demands.values() if not d['required']})
    best=None; winners=[]
    for counts in product(*ranges):
        added={p:0 for p in demands}; suppliers={p:set() for p in demands}; goods={}; allocation=[]; valid=True
        for q,n in zip(quotes,counts):
            if n==0: continue
            p=q['part_id']; r=rels[p,q['supplier_id']]; remaining=demands[p]['quantity']-committed[p]
            units=n*q['pack_size']
            # Public single supplier, minimal integer package policy.
            if n!=(remaining+q['pack_size']-1)//q['pack_size'] or units>r['capacity_units'] or (data['site']=='pit_stop' and units-remaining>1): valid=False; break
            price=q['pack_price_cents'] if q['status']=='signed' else q['price_bounds_cents'][0 if mode=='lower' else 1]
            added[p]+=units; suppliers[p].add(q['supplier_id']); goods[q['supplier_id']]=goods.get(q['supplier_id'],0)+n*price
            allocation.append({'quote_id':q['quote_id'],'packs':n})
        if not valid or any(len(s)>1 for s in suppliers.values()): continue
        fulfilled={p:committed[p]+added[p] for p in demands}
        if any(d['required'] and fulfilled[p]<d['quantity'] for p,d in demands.items()): continue
        if any(len({fulfilled[p]>=demands[p]['quantity'] for p in members})>1 for members in kits.values()): continue
        fees={s:0 if g>=data['carts'][s]['free_at_cents'] else data['carts'][s]['freight_cents'] for s,g in goods.items()}
        total=paid+sum(goods.values())+sum(fees.values())
        if total>data['budget_cents']: continue
        coverage=[sum(min(fulfilled[p],d['quantity']) for p,d in demands.items() if not d['required'] and d['priority']==level) for level in priorities]
        objective=(tuple(coverage),-total)
        if best is None or objective>=best:
            plan={'allocation':sorted(allocation,key=lambda x:x['quote_id']),'total_cents':total,'coverage':coverage}
            if best is None or objective>best: best=objective; winners=[]
            winners.append(plan)
    return best,winners,size


def independent_solve(task,limit=100_000):
    d=task['input']; runs={mode:_integer_search(d,mode,limit) for mode in ('known','lower','upper')}
    known=runs['known'][0]
    disposition='needs_information' if any(runs[m][0]!=known for m in ('lower','upper')) else ('infeasible' if known is None else 'execute')
    decisions=[dict(p,disposition=disposition) for p in runs['known'][1]] if disposition=='execute' else [{'disposition':disposition,'allocation':[],'total_cents':sum(c['paid_cents'] for c in d['commitments']),'coverage':None}]
    return {'disposition':disposition,'objective':known,'lower_objective':runs['lower'][0],'upper_objective':runs['upper'][0],'decisions':decisions,'states':{m:r[2] for m,r in runs.items()}}


def canonical(value):
    if isinstance(value,dict): return {k:canonical(v) for k,v in sorted(value.items())}
    if isinstance(value,list): return sorted([canonical(x) for x in value],key=repr)
    return value


def reference_solve(task):
    old=v2_reference(task)
    decisions=[]
    for a,p in zip(old['answers'],old['plans']):
        decisions.append({'disposition':a['disposition'],'allocation':a['allocation'],'total_cents':p['total_cents'],'coverage':p['coverage']})
    return {'disposition':old['disposition'],'objective':old['objective'],'lower_objective':old['lower_objective'],'upper_objective':old['upper_objective'],'decisions':decisions}



def separate_audits(task):
    data=task['input']; rel={(r['part_id'],r['supplier_id']):r for r in data['erp_relationships']}
    conflicts=[]
    for q in data['quotes']:
        for field,value in q.get('erp_claims',{}).items():
            if value!=rel[q['part_id'],q['supplier_id']][field]:
                conflicts.append({'quote_id':q['quote_id'],'field':field})
    fees={}
    for cart in data['audit_carts']:
        tariff=data['carts'][cart['supplier_id']]
        goods=cart['new_goods_cents']
        fees[cart['cart_id']]=0 if goods==0 or goods>=tariff['free_at_cents'] else tariff['freight_cents']
    return {'erp_claim_conflicts':conflicts,'freight_audit':fees}


def reference_answers(task):
    """The existing v2 outputs, plus two disjoint audit artifacts. No fixed planner for Actor."""
    audits=separate_audits(task)
    return [dict(a,**audits) for a in v2_reference(task)['answers']]


def _typed(value):
    if isinstance(value,dict): return tuple((k,_typed(v)) for k,v in sorted(value.items()))
    if isinstance(value,list): return tuple(sorted((_typed(v) for v in value),key=repr))
    return (type(value).__name__,value)


def grade_task(task,submission,rubric):
    """Six binary outcomes; one point for the complete purchase decision.
    No separate reward for copying allocation into approval lines or computing its freight.
    Invoice audit uses different carts, not the candidate allocation.
    """
    if not isinstance(submission,dict): submission={}
    demanded={d['part_id'] for d in task['input']['demands']}
    audit_keys={f'{r["part_id"]}:{r["supplier_id"]}' for r in task['input']['erp_relationships'] if r['part_id'] not in demanded}
    def projection(value,point):
        if point=='procurement':
            return dict({f:value.get(f) for f in ('disposition','allocation','freight_cents')}, approval_lines=(value.get('approval_request') or {}).get('lines') if isinstance(value.get('approval_request'),dict) else None)
        if point=='source_selection':
            selected=value.get('source_selection',{})
            return {k:selected.get(k) for k in audit_keys} if isinstance(selected,dict) else None
        if point=='approval_boundary':
            a=value.get('approval_request',{})
            return {f:a.get(f) for f in ('revision','reuse_prior_approval')} if isinstance(a,dict) else None
        return value.get(point)
    scores=[]
    for expected in reference_answers(task):
        points={p['point_id']:_typed(projection(submission,p['outcome_key']))==_typed(projection(expected,p['outcome_key'])) for p in rubric}
        scores.append({'score':sum(p['weight'] for p in rubric if points[p['point_id']])/sum(p['weight'] for p in rubric),'points':points,'business_success':all(points.values())})
    return max(scores,key=lambda x:x['score'])
