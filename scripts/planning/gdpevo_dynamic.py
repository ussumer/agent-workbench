"""Materialize a frozen T58 experimental bank and run the existing tool Actor."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT, ROOT / "src", Path("/mnt/c/dev/rsi-eval")):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from scripts.planning.gdpevo_v3_scoped import GROUPS, SESSION as SCOPED
from scripts.planning.live_baseline import load_skill_bank, run


def export_bank(source: Path, output: Path) -> dict:
    manifest = json.loads((source / 'manifest.json').read_text())
    for filename in ('bank.json', 'skills.json', 'protocol.json'):
        if hashlib.sha256((source / filename).read_bytes()).hexdigest() != manifest[filename]:
            raise ValueError('scoped source hash mismatch: ' + filename)
    bank = json.loads((source / 'bank.json').read_text())
    if set(bank['skills']) != set(GROUPS) or bank.get('production_assignment_changed') is not False:
        raise ValueError('four experimental scoped skills required')
    skills = load_skill_bank(source / 'bank.json')
    for group, skill in zip(bank['skills'], skills, strict=True):
        if skill.skill_id != 'planning_' + group + '_strategy':
            raise ValueError('scoped skill ID mismatch')
    payload = {
        'skills': [skill.model_dump(mode='json') for skill in skills],
        'skill_hashes': {skill.skill_id: hashlib.sha256(skill.body.encode()).hexdigest()
                        for skill in skills},
        'production_assignment_changed': False,
        'candidate_promoted': False,
        'source_manifest_sha256': hashlib.sha256((source / 'manifest.json').read_bytes()).hexdigest(),
        'source': str(source),
        'claims': {'learning_gain_proven': False, 'v3_production_adapter': False},
    }
    with output.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    load_skill_bank(output)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('export', 'run'))
    parser.add_argument('--source', type=Path, default=SCOPED)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--total-cny', type=float, default=50.0)
    parser.add_argument('--per-attempt-cny', type=float, default=2.0)
    parser.add_argument('--actor-turn-allowance', type=int, default=16)
    args = parser.parse_args()
    if args.command == 'export':
        export_bank(args.source, args.output)
        return 0
    args.output.mkdir(parents=True, exist_ok=False)
    bank_path = args.output / 'experimental-bank.json'
    export_bank(args.source, bank_path)
    # The production goal is deliberately distinct from synthetic v3 scoring.
    # This pilot proves state-conditioned selection + real computation wiring only.
    return run(args.output / 'actor', total_cny=args.total_cny,
               per_attempt_cny=args.per_attempt_cny, skill_bank_file=bank_path,
               experiment_group='curated-v1', actor_turn_allowance=args.actor_turn_allowance)


if __name__ == '__main__':
    raise SystemExit(main())
