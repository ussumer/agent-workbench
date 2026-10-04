"""Private bounded reference enumerator, never an Actor tool or production planner.

Synthetic task-set design only. ERP/Mongo/OpenSandbox adapters remain unchanged.
"""
from __future__ import annotations

from itertools import product
from math import prod


def sources(data):
    latest = {}
    for quote in data['quotes']:
        if (quote['status'] == 'draft' or not quote['effective_at'] <= data['as_of'] <= quote['expires_at']):
            continue
        key = (quote['part_id'], quote['supplier_id'])
        previous = latest.get(key)
        if previous and previous['revision'] == quote['revision']:
            raise ValueError('ambiguous formal revision')
        if previous is None or previous['revision'] < quote['revision']:
            latest[key] = quote
    relationships = {(r['part_id'], r['supplier_id']): r for r in data['erp_relationships']}
    result = {}
    for key, q in latest.items():
        r = relationships[key]
        result[':'.join(key)] = {'quote_id': q['quote_id'], 'status': q['status'],
                                'erp_active': r['active'], 'erp_lead_days': r['lead_days']}
    return latest, relationships, result


def enumerate_plans(data, mode='known', limit=100_000):
    latest, relationships, _ = sources(data)
    fixed_cost = sum(c['paid_cents'] for c in data['commitments'])
    fixed = {d['part_id']: sum(c['quantity'] for c in data['commitments'] if c['part_id'] == d['part_id'])
             for d in data['demands']}
    priorities = sorted({d['priority'] for d in data['demands'] if not d['required']})
    choices = []
    for d in data['demands']:
        part = d['part_id']
        remaining = d['quantity'] - fixed[part]
        if remaining < 0 or any(c['lead_days'] > d['max_lead_days'] for c in data['commitments'] if c['part_id'] == part):
            return None, []
        local = [(None, 0, 0)] if remaining == 0 or not d['required'] else []
        if remaining:
            for key, q in latest.items():
                r = relationships[key]
                if (key[0] != part or not r['active'] or r['lead_days'] > d['max_lead_days']
                        or q['status'] == 'revoked' or (q['status'] == 'pending' and mode == 'known')):
                    continue
                packs = (remaining + q['pack_size'] - 1) // q['pack_size']
                units = packs * q['pack_size']
                if units > r['capacity_units'] or (data['site'] == 'pit_stop' and units - remaining > 1):
                    continue
                price = q['pack_price_cents'] if q['status'] == 'signed' else q['price_bounds_cents'][0 if mode == 'lower' else 1]
                local.append((q, packs, packs * price))
        if not local:
            return None, []
        choices.append(local)
    if prod(len(c) for c in choices) > limit:
        raise ValueError('oracle enumeration bound exceeded')
    best, winners = None, []
    for selections in product(*choices):
        fulfilled = {}
        cart_goods = {}
        allocation = []
        for d, (q, packs, cost) in zip(data['demands'], selections, strict=True):
            fulfilled[d['part_id']] = fixed[d['part_id']] + (packs * q['pack_size'] if q else 0)
            if q:
                cart_goods[q['supplier_id']] = cart_goods.get(q['supplier_id'], 0) + cost
                allocation.append({'quote_id': q['quote_id'], 'packs': packs})
        groups = {d['kit_id'] for d in data['demands'] if d['kit_id']}
        if any(len({fulfilled[d['part_id']] >= d['quantity'] for d in data['demands'] if d['kit_id'] == group}) != 1 for group in groups):
            continue
        fees = {}
        for supplier, cost in cart_goods.items():
            tariff = data['carts'].get(supplier, {'freight_cents': 0, 'free_at_cents': 0})
            fees[supplier] = 0 if cost >= tariff['free_at_cents'] else tariff['freight_cents']
        total = fixed_cost + sum(cart_goods.values()) + sum(fees.values())
        if total > data['budget_cents']:
            continue
        coverage = tuple(sum(min(fulfilled[d['part_id']], d['quantity']) for d in data['demands']
                             if not d['required'] and d['priority'] == p) for p in priorities)
        objective = (coverage, -total)
        answer = {'allocation': sorted(allocation, key=lambda x: x['quote_id']),
                  'freight_cents': dict(sorted(fees.items())), 'total_cents': total, 'coverage': list(coverage)}
        if best is None or objective > best:
            best, winners = objective, [answer]
        elif objective == best:
            winners.append(answer)
    return best, winners


def solve_task(task):
    data = task['input']
    _, _, source_map = sources(data)
    known, plans = enumerate_plans(data)
    lower, _ = enumerate_plans(data, 'lower')
    upper, _ = enumerate_plans(data, 'upper')
    disposition = 'needs_information' if lower != known or upper != known else ('infeasible' if known is None else 'execute')
    ledger = sorted([{'order_id':c['order_id'],'part_id':c['part_id'],'quantity':c['quantity'],'paid_cents':c['paid_cents']} for c in data['commitments']],key=lambda c:(c['order_id'],c['part_id']))
    if disposition != 'execute':
        plans = [{'allocation': [], 'freight_cents': {}, 'total_cents':sum(c['paid_cents'] for c in data['commitments']), 'coverage':None}]
    answers = []
    for plan in plans:
        allocation = plan['allocation']
        answer = {'disposition': disposition, 'source_selection': source_map, 'allocation': allocation,
                  'freight_cents':plan['freight_cents'], 'commitment_ledger':ledger,
                  'approval_request':{'revision':data['revision'], 'lines':allocation, 'reuse_prior_approval':False}}
        answers.append(answer)
    return {'disposition':disposition, 'objective':known, 'lower_objective':lower, 'upper_objective':upper,
            'answers':answers, 'plans':plans}


def grade_task(task, submission, rubric):
    """Binary points; select one coherent valid optimum, never mix alternatives."""
    solutions = solve_task(task)['answers']
    scores = []
    for expected in solutions:
        points = {p['point_id']: submission.get(p['field']) == expected[p['field']] for p in rubric}
        score = sum(p['weight'] for p in rubric if points[p['point_id']]) / sum(p['weight'] for p in rubric)
        scores.append({'score':score,'points':points,'business_success':all(points.values())})
    return max(scores, key=lambda item:item['score'])
