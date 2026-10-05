"""Read-only verification for original v3 arms and repaired closed-loop evidence."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
for folder in (ROOT,ROOT/'src',Path('/mnt/c/dev/rsi-eval')):
    sys.path.insert(0,str(folder))
from scripts.planning.gdpevo_calibration import digest,write
from scripts.planning.gdpevo_expansion_judge import grade_task
from scripts.planning.gdpevo_v3_training import actor_messages,summarize
from scripts.planning.gdpevo_v3_recurate import BASE,SESSION,_curator_input
from agent.evolution.curator import validate_curated
from agent.evolution.refinement import select_candidate
ORIGINAL=BASE.parent/'attempt-gdpevo-v3-expanded-20261004'

def read(path): return json.loads(path.read_text())

def check_manifest(directory):
    manifest=read(directory/'manifest.json')
    actual={str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file() and p.name!='manifest.json'}
    if set(manifest)!=actual: raise ValueError('incomplete manifest '+str(directory))
    for rel,sha in manifest.items():
        path=directory/rel
        if not path.resolve().is_relative_to(directory.resolve()) or digest(path)!=sha:
            raise ValueError('evidence hash mismatch '+rel)

def check_row(directory,row,task,rubric,expected_messages=None):
    req=read(directory/'request.json')
    if expected_messages is not None and req['messages']!=expected_messages: raise ValueError('request boundary drift')
    if req['temperature']!=0 or req['stream'] is not False: raise ValueError('settings drift')
    if read(directory/'result.json')!=row: raise ValueError('row/report mismatch')
    calls=[json.loads(line) for line in (directory/'model_calls.jsonl').read_text().splitlines() if line]
    completed=[c for c in calls if c.get('finished_at')]
    if len(completed)!=1 or completed[0].get('http_status')!=200 or row['metrics']['model_calls']!=1:
        raise ValueError('missing real call evidence')
    wire={**req,'thinking':{'type':'disabled'}}
    if hashlib.sha256(json.dumps(wire,ensure_ascii=False).encode()).hexdigest()!=completed[0]['request_sha256']:
        raise ValueError('wire request mismatch')
    raw=read(directory/'response.txt')['choices'][0]['message']['content']
    try:
        decision=json.loads(raw)
        if not isinstance(decision,dict): raise ValueError('non-object')
        grade=grade_task(task,decision,rubric)
    except (ValueError,KeyError,TypeError,IndexError):
        if row['status']!='format_failed' or row['grade']['score']!=0: raise ValueError('format failure lost')
    else:
        if row['status']!='scored' or row['grade']!=grade: raise ValueError('score drift')
    return req['model']

def check_arm(rows,group,tasks,split):
    arm=[r for r in rows if r['group']==group]
    expected={k for k,t in tasks.items() if t['split']==split}
    if len(arm)!=len(expected) or {r['task_id'] for r in arm}!=expected: raise ValueError('incomplete arm '+group)
    return arm

def verify():
    # Original ablation and defective-policy closed-loop runs are retained.
    for directory in (ORIGINAL,BASE,SESSION): check_manifest(directory)
    public=read(SESSION/'frozen/gdpevo-procurement-v3.json'); training=read(SESSION/'frozen/gdpevo-procurement-v3-training.json'); control=read(SESSION/'frozen/gdpevo-procurement-v3-control.json'); tasks={t['task_id']:t for t in public['tasks']}
    original=read(ORIGINAL/'report.json')
    for group in ('fixed-v3','raw-v3','curated-v3'):
        for row in check_arm(original['rows'],group,tasks,'test'):
            check_row(ORIGINAL/group/row['task_id'],row,tasks[row['task_id']],control['rubrics'][row['task_id']])
    if summarize(original['rows'])!=original['groups']: raise ValueError('original summary drift')
    source_records=read(BASE/'training-records.json'); material=_curator_input(training,source_records)
    initial=BASE.parent/'attempt-gdpevo-v3-training-20261004'
    for record in source_records:
        task=record['task']; tid=task['task_id']
        if task!=tasks[tid] or task['split']!='train' or not 1<=len(record['attempts'])<=2:
            raise ValueError('invalid learning task/repair count')
        for index,attempt in enumerate(record['attempts']):
            directory=(initial/'train'/tid) if index==0 else (BASE/'repair'/tid)
            response=read(directory/'response.txt')['choices'][0]['message']['content']
            if json.loads(response)!=json.loads(attempt['model_output']): raise ValueError('learning output drift')
            row=read(directory/'result.json')
            check_row(directory,row,task,control['rubrics'][tid])
            if row['grade']!=attempt['grade']: raise ValueError('learning feedback drift')
        if len(record['attempts'])==2 and record['attempts'][0]['grade']['business_success']:
            raise ValueError('unnecessary success repair')
    if material!=read(SESSION/'curator-records.json'): raise ValueError('Curator learning material drift')
    cur_req=read(SESSION/'curator/request.json')
    if json.loads(cur_req['messages'][1]['content'])!=material: raise ValueError('Curator boundary drift')
    cur_response=read(SESSION/'curator/response.txt')
    skill=validate_curated(cur_response['choices'][0]['message']['content']).model_dump(mode='json')
    if skill!=read(SESSION/'candidate-skill.json'): raise ValueError('skill drift')
    trials=read(SESSION/'validation.json'); fixed=read(SESSION/'fixed-validation.json')
    if fixed!=[r for r in read(BASE/'validation.json') if r['group']=='fixed-train']: raise ValueError('fixed controls drift')
    for row in check_arm(fixed,'fixed-train',tasks,'train'):
        check_row(BASE/'validation/fixed-train'/row['task_id'],row,tasks[row['task_id']],control['rubrics'][row['task_id']],actor_messages(public,training,tasks[row['task_id']],'fixed-v3'))
    for row in check_arm(trials,'candidate-train',tasks,'train'):
        check_row(SESSION/'validation'/row['task_id'],row,tasks[row['task_id']],control['rubrics'][row['task_id']],actor_messages(public,training,tasks[row['task_id']],'curated-v3',skill))
    selection=select_candidate(fixed,trials,policy=read(SESSION/'selection.json').get('policy','per-task-v1'))
    report=read(SESSION/'report.json')
    if selection!=report['selection'] or selection!=read(SESSION/'selection.json'): raise ValueError('selection drift')
    baseline_test=check_arm(report['rows'],'fixed-closed-test',tasks,'test')
    if baseline_test!=[r for r in read(BASE/'report.json')['rows'] if r['group']=='fixed-closed-test']: raise ValueError('test controls drift')
    for row in baseline_test:
        check_row(BASE/'fixed-closed-test'/row['task_id'],row,tasks[row['task_id']],control['rubrics'][row['task_id']],actor_messages(public,training,tasks[row['task_id']],'fixed-v3'))
    for row in check_arm(report['rows'],'candidate-policy-test',tasks,'test'):
        check_row(SESSION/'test'/row['task_id'],row,tasks[row['task_id']],control['rubrics'][row['task_id']],actor_messages(public,training,tasks[row['task_id']],'curated-v3',skill))
    if summarize(report['rows'])!=report['groups']: raise ValueError('summary drift')
    bank=read(SESSION/'bank.json')
    if bank['selection']!=selection or bank['candidate_promoted']!=selection['promote'] or bank['production_assignment_changed'] is not False: raise ValueError('bank drift')
    return {'original_groups':original['groups'],'repaired_groups':report['groups'],'selection':selection,'source':str(SESSION),'no_new_model_calls':True}
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path);args=ap.parse_args();report=verify()
    if args.output: write(args.output,report)
    print(json.dumps(report,ensure_ascii=False))
