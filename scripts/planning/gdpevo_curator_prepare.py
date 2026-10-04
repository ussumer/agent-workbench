"""Stage complete train-only Curator requests from verified historical evidence.

No model calls, private answers, test outputs, or promotion. Existing paid
attempts remain immutable; the output directory must be new.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT, ROOT / 'src', Path('/mnt/c/dev/rsi-eval')):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from scripts.planning.gdpevo_calibration import digest, write
from scripts.planning.gdpevo_v3_scoped import BASE, GROUPS, _curator_prompt, scoped_curator_input
from scripts.planning.gdpevo_v3_training import SESSION as ORIGINAL


def read_verified(source: Path, relative: str, manifest: dict) -> dict | list:
    path = source / relative
    if digest(path) != manifest.get(relative):
        raise ValueError('historical evidence changed: ' + relative)
    return json.loads(path.read_text(encoding='utf-8'))


def read_paid_request(directory: Path) -> dict:
    """The first run failed before its aggregate manifest; use its wire record."""
    request = json.loads((directory / 'request.json').read_text())
    result = json.loads((directory / 'call-result.json').read_text())
    metrics = result['metrics']
    if result['http_status'] != 200 or metrics['model_calls'] != 1 or len(metrics['calls']) != 1:
        raise ValueError('original request lacks a completed real call: ' + directory.name)
    wire = {**request, 'thinking': {'type': 'disabled'}}
    wire_hash = hashlib.sha256(json.dumps(wire, ensure_ascii=False).encode()).hexdigest()
    if wire_hash != metrics['calls'][0]['request_sha256']:
        raise ValueError('original request differs from paid wire record: ' + directory.name)
    return request


def prepare(output: Path, *, source: Path = BASE, original: Path = ORIGINAL) -> dict:
    manifest = json.loads((source / 'manifest.json').read_text())
    records = read_verified(source, 'training-records.json', manifest)
    training = read_verified(source, 'frozen/gdpevo-procurement-v3-training.json', manifest)
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 2, 'model_calls': 0, 'production_assignment_changed': False,
              'source': str(source), 'source_manifest_sha256': digest(source / 'manifest.json'),
              'original': str(original),
              'original_provenance': 'per-call recorded wire SHA256; failed original run has no aggregate manifest',
              'groups': {}, 'learning_gain_proven': False}
    try:
        for group in GROUPS:
            material = scoped_curator_input(training, records, group)
            context = None
            model = None
            sources = []
            for row in material['train_records']:
                task_id = row['task_id']
                relative = f'train/{task_id}/request.json'
                request = read_paid_request(original / 'train' / task_id)
                response = json.loads((original / 'train' / task_id / 'response.txt').read_text())
                if response['choices'][0]['message']['content'] != row['attempts'][0]['decision']:
                    raise ValueError('original response differs from frozen train record: ' + task_id)
                view = json.loads(request['messages'][1]['content'])
                if view['task'] != row['actor_task']:
                    raise ValueError('original Actor view mismatch: ' + task_id)
                if view['training_materials'] != training['tasks'][task_id]:
                    raise ValueError('original Actor policies mismatch: ' + task_id)
                current = {'system': request['messages'][0]['content'], 'environment': view['environment']}
                if context is not None and (context != current or model != request['model']):
                    raise ValueError('mixed Actor context/model in group: ' + group)
                context, model = current, request['model']
                sources.append({'path': str(original / relative), 'sha256': digest(original / relative),
                                'wire_record_sha256': digest(original / 'train' / task_id / 'call-result.json')})
                if len(row['attempts']) == 2:
                    repair_path = f'repair/{task_id}/request.json'
                    repair = read_verified(source, repair_path, manifest)
                    feedback = json.loads(repair['messages'][-1]['content'].split('：', 1)[1])
                    if (repair['messages'][:2] != request['messages']
                            or repair['messages'][-2]['content'] != row['attempts'][0]['decision']
                            or feedback != row['attempts'][0]['feedback']):
                        raise ValueError('repair feedback/view mismatch: ' + task_id)
                    sources.append({'path': str(source / repair_path), 'sha256': manifest[repair_path]})
                elif len(row['attempts']) != 1:
                    raise ValueError('unexpected historical attempt count: ' + task_id)
            material['actor_visible_common'] = context
            material['visibility'] = {
                'actor': 'common system/environment, actor_task, rule_ids -> policies; repair uses preceding feedback',
                'curator_only': 'grade and post-attempt diagnostic feedback; no gold answers or test outputs',
            }
            payload = {'model': model, 'messages': [
                {'role': 'system', 'content': _curator_prompt(group)},
                {'role': 'user', 'content': json.dumps(material, ensure_ascii=False)},
            ], 'temperature': 0, 'stream': False,
                'response_format': {'type': 'json_object'}, 'max_tokens': 2048}
            size = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
            if size > 131072:
                raise ValueError(f'{group}: full Curator request exceeds byte limit: {size}')
            write(output / f'{group}-request.json', payload)
            report['groups'][group] = {'task_ids': [r['task_id'] for r in material['train_records']],
                                      'attempt_count': sum(len(r['attempts']) for r in material['train_records']),
                                      'request_bytes': size, 'sources': sources}
        write(output / 'report.json', report)
        write(output / 'manifest.json', {path.name: digest(path) for path in output.iterdir() if path.is_file()})
        return report
    except Exception as error:
        write(output / 'failure.json', {'type': type(error).__name__, 'message': str(error), 'model_calls': 0})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output), ensure_ascii=False))
