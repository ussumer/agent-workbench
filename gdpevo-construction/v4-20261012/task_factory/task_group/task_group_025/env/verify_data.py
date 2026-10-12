"""Trusted environment-only source lineage and data-contract verification."""
import json,hashlib,subprocess,zipfile
from pathlib import Path
from collections import Counter
root=Path(__file__).resolve().parent
repo=root.parents[5]
scratch=root.parents[2]/'scratch'/'env-builder'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
seed=json.loads((root/'seed.json').read_text())
rows=seed['records']
counts=Counter(r['domain'] for r in rows)
assert counts['programs']==150 and counts['quotes']==1800
index={(r['domain'],r['record_id']):r['payload'] for r in rows}
assert len(index)==len(rows),'duplicate domain record'
for r in rows:
    obj=r['payload']
    if r['domain'] not in ('programs','suppliers','documents'):
        assert all(k in obj for k in ('currency','uom','effective_period','approval_status'))
        assert ('documents',obj['document_id']) in index
    if r.get('program_id'):assert ('programs',r['program_id']) in index
    if r.get('supplier_id'):assert ('suppliers',r['supplier_id']) in index
for n in ('021','034','047','058','069','083','096','107','118','129','040','110'):
    assert ('programs','PRG-'+n) in index
shortfalls=0
adequate=0
for r in rows:
    if r['domain']=='engineering' and r['payload']['revision']==2:
        obj=r['payload'];p=obj['program_id']
        for cap in obj['annual_capacity']:
            y=cap['fiscal_period']
            vals=[]
            for variant in ('base','premium'):
                candidates=[x['payload'] for x in rows if x['domain']=='forecasts' and x['program_id']==p and x['payload']['fiscal_period']==y and x['payload']['variant']==variant and x['payload']['approval_status']=='approved']
                vals.append(max(candidates,key=lambda x:x['revision'])['quantity'])
            if cap['quantity']<sum(vals):shortfalls+=1
            else:adequate+=1
assert shortfalls>0 and adequate>0
for r in rows:
    if r['domain']=='quotes':
        q=r['payload'];prev=q['replaces_document_id']
        if prev:
            old=index[('quotes',prev)]
            assert q['tooling_charge_id']==old['tooling_charge_id'] and q['rd_charge_id']==old['rd_charge_id']
before=digest(root/'seed.json')
subprocess.run(['python3',str(root/'generate_seed.py')],check=True,capture_output=True)
assert digest(root/'seed.json')==before,'fixed seed is not reproducible'
lineage=[]
for p in sorted((root/'erp').rglob('*')):
    if p.is_file() and 'target' not in p.parts:
        rel=p.relative_to(root/'erp');source=repo/'erp'/rel
        lineage.append({'path':'erp/'+rel.as_posix(),'sha256':digest(p),'original_path':('erp/'+rel.as_posix()) if source.is_file() else None,'original_sha256':digest(source) if source.is_file() else None,'copy_status':'unchanged' if source.is_file() and digest(source)==digest(p) else 'new_or_environment_adapted'})
manifest={'schema_version':1,'seed':seed['seed'],'counts':dict(sorted(counts.items())),'seed_sha256':before,'copied_source_lineage':lineage,'environment_files':[{'path':p.relative_to(root).as_posix(),'sha256':digest(p)} for p in sorted(root.rglob('*')) if p.is_file() and not any(x in p.parts for x in ('target','__pycache__','data')) and p.name!='source-manifest.json'],'business_data_checks':{'status':'passed','capacity_shortfall_supplier_periods':shortfalls,'capacity_adequate_supplier_periods':adequate,'duplicate_ids':False,'charge_identity_across_revisions':True,'fixed_seed_reproducible':True,'target_programs_present':12},'model_calls':0}
(root/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
jar=root/'erp/target/procurement-erp-0.1.0.jar'
libs=[x.rsplit('/',1)[-1] for x in zipfile.ZipFile(jar).namelist() if x.startswith('BOOT-INF/lib/') and x.endswith('.jar')]
build={'maven_version':'3.9.9','wrapper_distribution_sha256':'4ec3f26fb1a692473aea0235c300bd20f0f9fe741947c82c1234cefd76ac3a3c','spring_boot':'3.5.6','java_release':21,'build_java':'Ubuntu OpenJDK21.0.12.1','runtime_java':'Eclipse Temurin21.0.8_9','runtime_image':'eclipse-temurin@sha256:db1689535962d757a5adabf57387584ed543d38c0b9d1fe870123ea362ad73b0','probe_image':'python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea','jar_sha256':digest(jar),'resolved_runtime_jars':sorted(libs)}
(scratch/'build-lock-evidence.json').write_text(json.dumps(build,indent=2)+'\n')
print(json.dumps({'counts':manifest['counts'],'checks':manifest['business_data_checks'],'copied_original_files':sum(x['copy_status']=='unchanged' for x in lineage),'adapted_or_new_java_files':[x['path'] for x in lineage if x['copy_status']!='unchanged']},indent=2))
