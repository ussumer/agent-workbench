"""Verify actual dynamic Actor evidence; unavailable/failed runtime is blocked."""
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

from scripts.planning.live_baseline import load_skill_bank

ATTEMPT = Path('/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261005/attempt-dynamic-tool-20261005d')


def verify(attempt: Path) -> dict:
    result_file = attempt / 'actor/result.json'
    if not result_file.is_file():
        return {'status': 'blocked', 'reason': 'real Actor evidence missing', 'real_actor_verified': False}
    result = json.loads(result_file.read_text())
    manifest = json.loads((attempt / 'actor/evidence-manifest.json').read_text())
    for relative, expected in manifest.items():
        path = (attempt / 'actor' / relative).resolve()
        if not path.is_relative_to((attempt / 'actor').resolve()):
            raise ValueError('evidence path escapes attempt')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('evidence hash mismatch: ' + relative)
    if not result.get('episode_export'):
        return {'status': 'blocked', 'reason': 'real Actor did not reach Episode export',
                'errors': result.get('errors', []), 'model_calls': result.get('model_calls', 0),
                'real_actor_verified': False, 'learning_gain_proven': False}
    skills = load_skill_bank(attempt / 'experimental-bank.json')
    expected = {skill.skill_id: hashlib.sha256(skill.body.encode()).hexdigest() for skill in skills}
    export = result['episode_export']
    events = export['events']
    starts = {event['payload']['turn_id']: event['payload'] for event in events if event['kind'] == 'turn_started'}
    grounded = {event['payload']['turn_id']: event['payload'] for event in events if event['kind'] == 'turn_grounded'}
    actions = {event['payload']['turn_id']: event['payload'] for event in events if event['kind'] == 'turn_action'}
    if set(grounded) != set(actions) or not set(grounded) <= set(starts):
        raise ValueError('unpaired grounded Actor action evidence')
    for turn_id, ground in grounded.items():
        start = starts[turn_id]
        if {entry['skill_id'] for entry in start['catalog']} != set(expected):
            raise ValueError('selector did not receive the full bank')
        if len(set(ground['selection'])) != len(ground['selection']):
            raise ValueError('duplicate selected skill')
        if ground['read_bodies'] != [{'skill_id': key, 'body_sha256': expected[key]} for key in ground['selection']]:
            raise ValueError('ordered body hash mismatch')
        visible_parts = ground['visible_system'].get('content', []) if isinstance(ground['visible_system'], dict) else []
        visible = '\n'.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in visible_parts)
        for skill in skills:
            if (skill.body in visible) != (skill.skill_id in ground['selection']):
                raise ValueError('stale or absent skill body in Actor prompt')
    selector_calls = [event for event in events if event['kind'] == 'model_invocation'
                      and event['payload']['role'] == 'skill_selector']
    if len(selector_calls) != len(starts):
        raise ValueError('selector was not invoked on every Actor turn')
    computations = [row for row in result.get('kernel_executions', []) if row.get('status') == 'completed']
    if not computations:
        raise ValueError('no completed OpenSandbox computation')
    if result['bank'].get('production_assignment_changed') is not False:
        raise ValueError('production bank must remain unchanged')
    runtime_completed = (result.get('terminal_status') == 'completed' and len(starts) >= 2
                         and set(starts) == set(grounded))
    protocol_verified = len(grounded) >= 2
    return {'status': 'passed' if runtime_completed else 'blocked',
            'reason': 'complete runtime' if runtime_completed else 'partial runtime; protocol evidence retained',
            'errors': result.get('errors', []), 'terminal_status': result.get('terminal_status'),
            'real_actor_verified': runtime_completed, 'runtime_completed': runtime_completed,
            'grounded_actor_turns': len(grounded),
            'completed_turn_protocol_verified': protocol_verified, 'model_calls': result['model_calls'],
            'turns': len(starts), 'selector_calls': len(selector_calls),
            'completed_computations': len(computations), 'selection_sequences': [x['selection'] for x in grounded.values()],
            'production_assignment_changed': False, 'learning_gain_proven': False,
            'v3_business_adapter_verified': False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--attempt', type=Path, default=ATTEMPT)
    args = parser.parse_args()
    report = verify(args.attempt)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
