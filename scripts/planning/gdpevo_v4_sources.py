"""GDPval source discovery for a v4 candidate, not a calibrated training runner.

build needs the separately installed pyarrow 25.0.1 reader. verify is offline and
uses the standard library. Original benchmark rubrics/deliverables never enter
the public source package. v3 examples remain explicitly synthetic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT.parent / 'rsi-eval'
SCENARIO_ID = 'SCN_025_automotive_procurement_v4'
SCENARIO = EVAL / 'data_construction/Stage_1_Scenario_Discovery/scenario' / SCENARIO_ID
OUT = ROOT / 'fixtures/planning/gdpevo-procurement-v4-candidate.json'
ARCHIVE = ROOT / 'artifacts/source-discovery/gdpval/train.parquet'
REVISION = '11e7900cdcac61bc4daf59e65feb238acda98fbf'
PARQUET_SHA = 'f8422fab9b21d90c0ee5f0659842ab666d418cb8940842918f9f4b0df7ae0202'
IDS = [
    '1b1ade2d-f9f6-4a04-baa5-aa15012b53be',
    '93b336f3-61f3-4287-86d2-87445e1e0f90',
    '15ddd28d-8445-4baa-ac7f-f41372e1344e',
    '24d1e93f-9018-45d4-b522-ad89dfd78079',
    '05389f78-589a-473c-a4ae-67c61050bfca',
    'ab81b076-e5d8-473a-9bdb-7ea7c38f6ebc',
]
NAMES = ['Modular sourcing and design-change approvals', 'Battery assembly localisation economics',
         'Supply interruption and transition planning', 'Headlamp supplier NPV comparison',
         'Supplier failure and replacement nomination', 'Stock and critical parts receiving']
MAPPING = {
    'quotes': {'examples': ['E004', 'E005'],
               'preserved_business_basis': 'Compare supplier quotations, financial impact and sourcing alternatives.',
               'unsupported_v3_extensions': ['signed/draft/revoked precedence', 'pending-price intervals', 'exact ERP field-authority protocol']},
    'packages': {'examples': ['E002', 'E003'],
                 'preserved_business_basis': 'Component costs, sourcing volume, supplier capacity and transition lead time.',
                 'unsupported_v3_extensions': ['integer package sizes', 'free-freight thresholds', 'non-greedy optional-demand coverage']},
    'kits': {'examples': ['E001', 'E002', 'E004'],
             'preserved_business_basis': 'Modular cost drivers, child parts, component assembly and product variants.',
             'unsupported_v3_extensions': ['all-or-none kit closure', 'independent-member exceptions', 'lexicographic procurement priorities']},
    'revisions': {'examples': ['E001', 'E005', 'E006'],
                  'preserved_business_basis': 'Post-nomination changes, renewed signoffs, paid-upfront costs and order discrepancies.',
                  'unsupported_v3_extensions': ['immutable commitment ledger schema', 'append-only residual ordering', 'supplier-cart historical freight exclusion']},
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_new(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def build():
    if OUT.exists() or SCENARIO.exists():
        raise FileExistsError('Candidate/scenario already exists; never overwrite source evidence.')
    assert sha(ARCHIVE) == PARQUET_SHA, 'Unexpected source Parquet'
    sys.path.insert(0, str(ROOT / 'artifacts/source-reader'))
    import pyarrow.parquet as pq
    records = {r['task_id']: r for r in pq.read_table(ARCHIVE).to_pylist()}
    created = datetime.now(timezone.utc).isoformat()
    scenario = {'scenario_id': SCENARIO_ID, 'name': 'Automotive sourcing and procurement control',
                'domain': 'ERP / Automotive Procurement',
                'description': 'Original GDPval real-world work benchmark tasks. Business context is supported; v3-specific synthetic rules are not authenticated by these sources.',
                'examples': []}
    source_tasks = []
    for index, task_id in enumerate(IDS, 1):
        row = records[task_id]
        eid = f'E{index:03d}'
        folder = SCENARIO / 'attachments' / eid
        public = {k: row[k] for k in ('task_id', 'sector', 'occupation', 'prompt', 'reference_files')}
        public.update({'dataset': 'openai/gdpval', 'dataset_revision': REVISION,
                       'source_parquet_sha256': PARQUET_SHA, 'retrieved_at': created,
                       'source_url': 'https://huggingface.co/datasets/openai/gdpval/tree/' + REVISION})
        write_new(folder / 'raw-source.json', public)
        attachments = [f'attachments/{eid}/raw-source.json']
        refs = []
        for remote_path in row['reference_files']:
            url = f'https://huggingface.co/datasets/openai/gdpval/resolve/{REVISION}/' + quote(remote_path, safe='/')
            destination = folder / Path(remote_path).name
            with urlopen(url, timeout=40) as response:
                data = response.read()
            if not data:
                raise ValueError('Empty source attachment: ' + remote_path)
            with destination.open('xb') as stream:
                stream.write(data)
            relative = destination.relative_to(SCENARIO).as_posix()
            refs.append({'path': relative, 'source_path': remote_path, 'url': url,
                         'sha256': sha(destination), 'bytes': len(data)})
            attachments.append(relative)
        public['archived_reference_files'] = refs
        # Metadata with file digests is separate from the immutable original row.
        write_new(folder / 'provenance.json', {'raw_source_sha256': sha(folder / 'raw-source.json'),
                                              'reference_files': refs})
        notes = SCENARIO / f'notes/{eid}.md'
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text(f'''# {eid}: {NAMES[index-1]}

Source: GDPval / openai/gdpval, original task `{task_id}`; revision `{REVISION}`.
Occupation: {row['occupation']}. Sector: {row['sector']}.
Author: Codex, source discovery on {created}. The source describes a real-world
work benchmark with fictitious identifying details, not a verified live company incident.

The complete original prompt and original reference filenames are archived in
`attachments/{eid}/raw-source.json`; reference attachments are preserved unchanged.
The original deliverable is an executive document, spreadsheet, or procedure as
specified by that prompt, not a v3 procurement-decision JSON.

This belongs to automotive procurement because it concerns supplier/component
relationships, commercial evidence, sourcing changes, volume/cost planning or
receiving controls. See the original prompt for the complete process, constraints
and deliverable; no extra operational rules have been attributed to it.

Evaluation for a future adapted task must preserve its required financial
calculations, evidence and approval/governance requirements. Task-specific metrics,
gold outputs and an independent evaluator are still Stage 2 work. Source rubric and
deliverable files are not copied into solver-visible inputs. V3-specific package,
kit and ledger rules remain synthetic extensions; see `v3-source-mapping.json`.
''', encoding='utf-8')
        scenario['examples'].append({'example_id': eid, 'name': NAMES[index-1],
                                     'source': f'GDPval task {task_id} @ {REVISION}',
                                     'prompt': row['prompt'], 'notes': f'notes/{eid}.md',
                                     'attachments': attachments})
        source_tasks.append({'task_id': f'gdpval-{task_id}', 'example_id': eid,
                             'origin': 'original_real_world_benchmark_task', 'prompt': row['prompt'],
                             'reference_files': refs, 'split': 'source_pool_unassigned',
                             'evaluation_ready': False, 'original_output_contract_preserved': True})
    # JSON is a YAML 1.2 subset, keeping the Stage 1 filename without adding a runtime dependency.
    write_new(SCENARIO / 'scenario.yaml', scenario)
    write_new(SCENARIO / 'v3-source-mapping.json', MAPPING)
    v3 = read(ROOT / 'fixtures/planning/gdpevo-procurement-v3.json')
    candidate = {'schema_version': 1, 'dataset_id': 'chengchuan-procurement-v4-source-candidate',
                 'status': 'exploratory/intermediate', 'construction_stage': 'stage_1_scenario_discovery',
                 'scenario_id': SCENARIO_ID, 'scenario_workspace': 'rsi-eval/data_construction/Stage_1_Scenario_Discovery/scenario/' + SCENARIO_ID,
                 'created_at': created, 'source_revision': REVISION, 'source_parquet_sha256': PARQUET_SHA,
                 'evaluation_ready': False, 'training_ready': False,
                 'real_production_records_verified': False, 'learning_gain_proven': False,
                 'source_tasks': source_tasks, 'v3_group_mapping': MAPPING,
                 'synthetic_reference': {'origin': 'unchanged_synthetic_v3',
                                         'path': 'fixtures/planning/gdpevo-procurement-v3.json',
                                         'sha256': sha(ROOT / 'fixtures/planning/gdpevo-procurement-v3.json'),
                                         'dataset': v3},
                 'missing_stages': ['Stage 2: source-faithful task synthesis, answers, independent evaluators and tool adapters',
                                    'Stage 2: same-runtime base/fewshot calibration', 'Stage 3: independent quality review'],
                 'next_task': 'T67', 'production_assignment_changed': False}
    write_new(OUT, candidate)
    files = sorted(p for p in SCENARIO.rglob('*') if p.is_file())
    write_new(SCENARIO / 'manifest.json', {'created_at': created, 'source_revision': REVISION,
                                          'files': [{'path': p.relative_to(SCENARIO).as_posix(), 'sha256': sha(p)} for p in files]})
    print(json.dumps({'candidate': str(OUT), 'scenario': str(SCENARIO), 'source_tasks': len(source_tasks),
                      'stage': 'Stage 1 only; Stage 2/3 incomplete'}))


def verify():
    candidate = read(OUT)
    scenario = read(SCENARIO / 'scenario.yaml')
    assert candidate['status'] == 'exploratory/intermediate'
    for flag in ('evaluation_ready', 'training_ready', 'real_production_records_verified',
                 'learning_gain_proven', 'production_assignment_changed'):
        assert candidate[flag] is False, flag
    assert len(candidate['source_tasks']) == len(scenario['examples']) == len(IDS) == 6
    assert candidate['source_revision'] == REVISION
    assert candidate['source_parquet_sha256'] == PARQUET_SHA
    assert scenario['scenario_id'] == SCENARIO_ID
    assert candidate['v3_group_mapping'] == read(SCENARIO / 'v3-source-mapping.json') == MAPPING
    assert set(MAPPING) == {'quotes', 'packages', 'kits', 'revisions'}
    attachments = 0
    for expected_id, example, task in zip(IDS, scenario['examples'], candidate['source_tasks']):
        raw = read(SCENARIO / example['attachments'][0])
        assert raw['task_id'] == expected_id
        assert raw['dataset_revision'] == REVISION
        assert raw['prompt'] == example['prompt'] == task['prompt']
        assert task['split'] == 'source_pool_unassigned' and task['evaluation_ready'] is False
        assert (SCENARIO / example['notes']).is_file()
        assert not set(raw).intersection({'rubric_json', 'rubric_pretty', 'deliverable_files'})
        provenance = read(SCENARIO / 'attachments' / example['example_id'] / 'provenance.json')
        assert provenance['raw_source_sha256'] == sha(SCENARIO / example['attachments'][0])
        assert len(provenance['reference_files']) == len(raw['reference_files'])
        assert provenance['reference_files'] == task['reference_files']
        for ref in provenance['reference_files']:
            assert ref['url'].startswith('https://huggingface.co/datasets/openai/gdpval/resolve/' + REVISION + '/')
            file = SCENARIO / ref['path']
            assert file.resolve().is_relative_to(SCENARIO.resolve())
            assert sha(file) == ref['sha256'] and file.stat().st_size == ref['bytes']
            attachments += 1
    assert attachments == 2
    # Check exact source identity against the immutable upstream file when available.
    if ARCHIVE.exists():
        assert sha(ARCHIVE) == PARQUET_SHA
        sys.path.insert(0, str(ROOT / 'artifacts/source-reader'))
        import pyarrow.parquet as pq
        originals = {r['task_id']: r for r in pq.read_table(ARCHIVE).to_pylist()}
        for example, expected_id in zip(scenario['examples'], IDS):
            raw = read(SCENARIO / example['attachments'][0])
            for field in ('task_id', 'sector', 'occupation', 'prompt', 'reference_files'):
                assert raw[field] == originals[expected_id][field], field
    snapshot = candidate['synthetic_reference']
    assert snapshot['origin'] == 'unchanged_synthetic_v3'
    assert snapshot['sha256'] == sha(ROOT / snapshot['path'])
    assert snapshot['dataset'] == read(ROOT / snapshot['path'])
    assert snapshot['dataset']['environment']['data_mode'] == 'synthetic_offline_design'
    tasks = snapshot['dataset']['tasks']
    assert len(tasks) == 40
    for item in read(SCENARIO / 'manifest.json')['files']:
        path = SCENARIO / item['path']
        assert path.resolve().is_relative_to(SCENARIO.resolve()) and sha(path) == item['sha256']
    report = {'status': 'passed', 'scope': 'Stage 1 provenance, not business calibration',
              'source_tasks': 6, 'original_reference_attachments': attachments,
              'synthetic_v3_reference_tasks': len(tasks), 'model_calls': 0,
              'candidate_sha256': sha(OUT), 'scenario_manifest_sha256': sha(SCENARIO / 'manifest.json'),
              'evaluation_ready': False, 'training_ready': False}
    print(json.dumps(report, ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'verify'])
    args = parser.parse_args()
    build() if args.command == 'build' else verify()
