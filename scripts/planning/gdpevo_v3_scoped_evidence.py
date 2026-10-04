"""Recompute scoped scores and train selection; verify exact public requests."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
for p in (ROOT,ROOT/'src',Path('/mnt/c/dev/rsi-eval')):sys.path.insert(0,str(p))
from scripts.planning.gdpevo_v3_scoped import BASE,SESSION,GROUPS,scoped_curator_input,_curator_prompt
from scripts.planning.gdpevo_v3_evidence import check_manifest,check_row,check_arm,read
from scripts.planning.gdpevo_v3_training import actor_messages,summarize
from scripts.planning.gdpevo_calibration import digest,write
from agent.evolution.curator import validate_curated
from agent.evolution.refinement import select_candidate
import hashlib

def verify():
    check_manifest(BASE);check_manifest(SESSION)
    public=read(SESSION/'frozen/gdpevo-procurement-v3.json'); training=read(SESSION/'frozen/gdpevo-procurement-v3-training.json'); control=read(SESSION/'frozen/gdpevo-procurement-v3-control.json')
    tasks={t['task_id']:t for t in public['tasks']};records=read(SESSION/'training-records.json');skills=read(SESSION/'skills.json');protocol=read(SESSION/'protocol.json')
    if records!=read(BASE/'training-records.json') or digest(BASE/'manifest.json')!=protocol['source_manifest_sha256']:raise ValueError('origin drift')
    if set(skills)!=set(GROUPS):raise ValueError('incomplete skill bank')
    for group in GROUPS:
        # T58 actually used v1. Keep its missing-view result honest and replayable.
        material=scoped_curator_input(training,records,group,version=1)
        if material!=read(SESSION/f'curator-input-{group}.json'):raise ValueError('learning input drift')
        req=read(SESSION/'curator'/group/'request.json')
        messages=[{'role':'system','content':_curator_prompt(group)},{'role':'user','content':json.dumps(material,ensure_ascii=False)}]
        if req['messages']!=messages or req['model']!=protocol['model_id']:raise ValueError('curator boundary drift')
        response=read(SESSION/'curator'/group/'response.txt')
        value=validate_curated(response['choices'][0]['message']['content']).model_dump(mode='json')
        if value!=skills[group] or value['skill_id']!=f'planning_{group}_strategy':raise ValueError('skill drift')
        row=read(SESSION/'curator'/group/'call-result.json')
        if row['metrics']['model_calls']!=1 or row['http_status']!=200:raise ValueError('no real curator call')
    fixed=read(SESSION/'fixed-validation.json');trials=read(SESSION/'validation.json');report=read(SESSION/'report.json')
    if fixed!=[r for r in read(BASE/'validation.json') if r['group']=='fixed-train']:raise ValueError('fixed train drift')
    for row in check_arm(fixed,'fixed-train',tasks,'train'):
        t=tasks[row['task_id']];check_row(BASE/'validation/fixed-train'/t['task_id'],row,t,control['rubrics'][t['task_id']],actor_messages(public,training,t,'fixed-v3'))
    for row in check_arm(trials,'scoped-train',tasks,'train'):
        t=tasks[row['task_id']];check_row(SESSION/'validation'/t['task_id'],row,t,control['rubrics'][t['task_id']],actor_messages(public,training,t,'curated-v3',skills[t['group_id']]))
    fixed_test=check_arm(report['rows'],'fixed-closed-test',tasks,'test')
    if fixed_test!=[r for r in read(BASE/'report.json')['rows'] if r['group']=='fixed-closed-test']:raise ValueError('fixed test drift')
    for row in fixed_test:
        t=tasks[row['task_id']];check_row(BASE/'fixed-closed-test'/t['task_id'],row,t,control['rubrics'][t['task_id']],actor_messages(public,training,t,'fixed-v3'))
    for row in check_arm(report['rows'],'scoped-test',tasks,'test'):
        t=tasks[row['task_id']];check_row(SESSION/'test'/t['task_id'],row,t,control['rubrics'][t['task_id']],actor_messages(public,training,t,'curated-v3',skills[t['group_id']]))
    selection=select_candidate(fixed,trials)
    if selection!=report['selection'] or selection!=read(SESSION/'selection.json'):raise ValueError('selection drift')
    if summarize(report['rows'])!=report['groups']:raise ValueError('summary drift')
    bank=read(SESSION/'bank.json')
    if bank['skills']!=skills or bank['selection']!=selection or bank['candidate_promoted']!=selection['promote'] or bank['production_assignment_changed'] is not False:raise ValueError('bank drift')
    for group,skill in skills.items():
        if bank['skill_hashes'][group]!=hashlib.sha256(skill['body'].encode()).hexdigest():raise ValueError('body hash drift')
    return {'groups':report['groups'],'selection':selection,'source':str(SESSION),'no_new_model_calls':True}
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path);args=ap.parse_args();report=verify()
    if args.output:write(args.output,report)
    print(json.dumps(report,ensure_ascii=False))
