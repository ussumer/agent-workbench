"""Read-only validation and one-task staging for manually authored v2-shaped fixtures.
No data generator, model runner, ERP adapter or Actor-accessible planner.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.planning.gdpevo_expansion_judge import (
    canonical, independent_solve, reference_solve, reference_answers, grade_task,
)
PUBLIC=ROOT/'fixtures/planning/gdpevo-procurement-v3.json'
TRAINING=ROOT/'fixtures/planning/gdpevo-procurement-v3-training.json'
CONTROL=ROOT/'fixtures/planning/private/gdpevo-procurement-v3-control.json'
ANSWERS=ROOT/'fixtures/planning/private/gdpevo-procurement-v3-answers.json'
PRESERVED=ROOT/'artifacts/tasks/T54/preserved-inputs.json'
OUTCOMES={'procurement','source_selection','freight_audit','commitment_ledger','approval_boundary','erp_claim_conflicts'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def load():
    return tuple(read(p) for p in (PUBLIC,TRAINING,CONTROL,ANSWERS))


def actor_view(public,training,task_id,stage):
    task=next(t for t in public['tasks'] if t['task_id']==task_id)
    if stage!=task['split']: raise ValueError('task split and actor stage differ')
    view={'environment':deepcopy(public['environment']),'task':deepcopy(task)}
    if stage=='train': view['training_materials']=deepcopy(training['tasks'][task_id])
    return view


def mutate(task,change):
    result=deepcopy(task); target=result
    for key in change['path'][:-1]: target=target[key]
    if target[change['path'][-1]]==change['value']: raise ValueError('counterfactual is a no-op')
    target[change['path'][-1]]=deepcopy(change['value'])
    return result


def verify_business(task,manual):
    old=reference_solve(task); independent=independent_solve(task)
    for key in ('disposition','objective','lower_objective','upper_objective','decisions'):
        if canonical(old[key])!=canonical(independent[key]):
            raise ValueError(f'{task["task_id"]}: independent/reference disagreement {key}')
    if canonical(manual) not in [canonical(d) for d in independent['decisions']]:
        raise ValueError(f'{task["task_id"]}: fixed manual expectation differs from enumerated result')
    return independent


def validate(require_all=True):
    public,training,control,gold=load()
    tasks={t['task_id']:t for t in public['tasks']}
    if len(tasks)!=len(public['tasks']): raise ValueError('duplicate task id')
    if require_all and (len(tasks)!=40 or len(public['groups'])!=4): raise ValueError('four new groups, forty main tasks required')
    train={i for i,t in tasks.items() if t['split']=='train'}
    if set(training['tasks'])!=train: raise ValueError('training materials must cover only train tasks')
    if set(control['rubrics'])!=set(tasks) or set(gold['tasks'])!=set(tasks) or set(control['manual_checks'])!=set(tasks):
        raise ValueError('all main tasks require rubric, fixed manual reference and private answers')
    for preserved,digest in read(PRESERVED).items():
        if hashlib.sha256((ROOT/preserved).read_bytes()).hexdigest()!=digest:
            raise ValueError(f'legacy or other agent asset changed: {preserved}')
    report={'schema_version':2,'task_group_id':public['task_group_id'],'groups':{},'model_calls':0,'claims':control['claims'],
            'hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (PUBLIC,TRAINING,CONTROL,ANSWERS)},
            'preserved_assets_sha256':read(PRESERVED)}
    for group in public['groups']:
        gid=group['group_id']; local=[t for t in tasks.values() if t['group_id']==gid]
        tr={t['task_id'] for t in local if t['split']=='train'}; te={t['task_id'] for t in local if t['split']=='test'}
        if (len(tr),len(te))!=(5,5): raise ValueError(f'{gid}: need 5 train/5 test')
        rules={r['rule_id']:r for r in control['groups'][gid]['rules']}
        for r in rules.values():
            if not all(r.get(k) for k in ('condition','policy','fields','stop')): raise ValueError('rule scope incomplete')
        trained={r for i in tr for r in control['matrix'][i]['rule_ids']}
        if trained!=set(rules): raise ValueError(f'{gid}: untrained rule')
        combinations={frozenset(control['matrix'][i]['rule_ids']) for i in tr}
        unseen={frozenset(control['matrix'][i]['rule_ids']) for i in te}-combinations
        if len(unseen)<3: raise ValueError(f'{gid}: less than three unseen combinations')
        matrix={}; main_results={}
        for task in local:
            tid=task['task_id']; applicable=control['matrix'][tid]['rule_ids']
            if not set(applicable)<=set(rules): raise ValueError('matrix references unknown rule')
            if tid in tr:
                materials=training['tasks'][tid]['policy_evidence']
                if {r['rule_id'] for r in materials}!=set(applicable): raise ValueError('train policy subset and matrix differ')
                for material in materials:
                    for field in ('condition','policy','fields','stop'):
                        if material[field]!=rules[material['rule_id']][field]: raise ValueError('train evidence differs from scoped policy')
            points=control['rubrics'][tid]
            if not 6<=len(points)<=10 or {pt['outcome_key'] for pt in points}!=OUTCOMES: raise ValueError('six distinct binary business outcomes required')
            for pt in points:
                if pt['weight'] not in (1,2,3) or pt['binary'] is not True: raise ValueError('invalid binary rubric weight')
                if set(pt['rule_anchors'])!=set(pt['rule_ids']) or not set(pt['rule_ids'])<=set(applicable): raise ValueError('scoring point rule mapping incomplete')
                for rule,anchors in pt['rule_anchors'].items():
                    if not anchors or any(i not in tr or rule not in control['matrix'][i]['rule_ids'] for i in anchors): raise ValueError('point lacks actual local training anchor')
            result=verify_business(task,control['manual_checks'][tid]['expected'])
            if canonical(gold['tasks'][tid])!=canonical(reference_answers(task)): raise ValueError('stored private answer mismatch')
            for answer in gold['tasks'][tid]:
                if not grade_task(task,answer,points)['business_success']: raise ValueError('stored private reference fails grader')
            view=actor_view(public,training,tid,task['split'])
            if tid in te and ('training_materials' in view or any(i in json.dumps(view,ensure_ascii=False) for i in tasks if i!=tid)):
                raise ValueError('test staging leaks other tasks or policies')
            if any(k in json.dumps(view) for k in ('"rubrics"','"gold"','"manual_checks"','"expected"')): raise ValueError('actor control-plane leakage')
            matrix[tid]={'rules':applicable,'scoring_points':[{k:pt[k] for k in ('point_id','outcome_key','weight','rule_anchors')} for pt in points]}
            main_results[tid]={'disposition':result['disposition'],'objective':result['objective'],'winner_count':len(result['decisions']),
                               'states_enumerated':result['states'],'manual_calculation':control['manual_checks'][tid]['calculation']}
        counterfactuals=[]
        for cf in [x for x in control['counterfactuals'] if x['group_id']==gid]:
            if len(cf['changes'])!=1 or cf['condition_group']!=cf['changes'][0]['path'][1]: raise ValueError('counterfactual must change exactly one declared condition group')
            task=tasks[cf['task_id']]; before=independent_solve(task); changed=mutate(task,cf['changes'][0]); after=verify_business(changed,cf['manual_expected'])
            signature=lambda r:(r['disposition'],r['objective'],canonical(r['decisions']))
            if signature(before)==signature(after): raise ValueError(f'{cf["id"]}: no substantive decision/objective/winner change')
            if canonical(gold['counterfactuals'][cf['id']])!=canonical(reference_answers(changed)): raise ValueError('private CF answer mismatch')
            counterfactuals.append({'id':cf['id'],'task_id':cf['task_id'],'condition_group':cf['condition_group'],'change':cf['changes'][0],
                'before':{k:before[k] for k in ('disposition','objective','decisions')},'after':{k:after[k] for k in ('disposition','objective','decisions')},
                'manual_calculation':cf['calculation'],'independent_change_proven':True})
        if len(counterfactuals)<8: raise ValueError('minimum eight CFs per group')
        report['groups'][gid]={'train_tasks':sorted(tr),'test_tasks':sorted(te),'unseen_combinations':len(unseen),'rule_train_test_scoring_map':matrix,
                               'main_validation':main_results,'counterfactuals':counterfactuals}
    report.update(new_main_tasks=len(tasks),new_counterfactuals=len(control['counterfactuals']),combined_main_tasks=len(tasks)+10,
                  combined_counterfactuals=len(control['counterfactuals'])+8,rubric_points=sum(len(p) for p in control['rubrics'].values()),
                  sample_before_expansion='artifacts/tasks/T54/quote-sample/README.md',failed_attempts_preserved=True)
    return report


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('command',choices=['validate','stage']); ap.add_argument('--output',type=Path)
    ap.add_argument('--task'); ap.add_argument('--stage',choices=['train','test']); args=ap.parse_args()
    if args.command=='stage':
        if not args.output or not args.task or not args.stage: ap.error('stage requires --task --stage --output')
        # Only the permitted public and training files are read, never answers/control.
        write(args.output,actor_view(read(PUBLIC),read(TRAINING),args.task,args.stage)); return 0
    report=validate()
    if args.output: write(args.output,report)
    print(json.dumps({k:report[k] for k in ('new_main_tasks','new_counterfactuals','combined_main_tasks','combined_counterfactuals','rubric_points','model_calls','hashes')},ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__': raise SystemExit(main())
