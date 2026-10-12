"""Private deterministic whole-point business evaluator; no network or model calls."""
import json
import sys
from pathlib import Path
POINTS = [('N1', 'approved_volume', 2), ('N2', 'commercial_coverage', 2),
          ('N3', 'technical_admissibility', 2), ('N4', 'capacity_shortfall', 2),
          ('N5', 'launch_gap', 2), ('N6', 'financial_exposure', 3)]
TOTAL_WEIGHT = sum(weight for _, _, weight in POINTS)
GOLD_PATH = Path(__file__).resolve().parent.parent / 'output' / 'answer.json'

def differences(actual, expected, path, money_tolerance=0):
    errors = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [path + ': object required']
        for key, value in expected.items():
            if key not in actual:
                errors.append(path + '.' + key + ': missing result')
            else:
                errors.extend(differences(actual[key], value, path + '.' + key, money_tolerance))
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return [path + ': row count or set membership mismatch']
        if expected and isinstance(expected[0], dict):
            key = 'supplier_id' if 'supplier_id' in expected[0] else 'fiscal_period'
            if any(not isinstance(row, dict) or key not in row for row in actual):
                return [path + ': row identity missing']
            # Reject duplicate identifiers and wrong identifier types, including bool years.
            if any(type(row[key]) is not type(expected[0][key]) for row in actual):
                return [path + ': row identity type mismatch']
            try:
                amap = {row[key]: row for row in actual}
            except TypeError:
                return [path + ': invalid row identity']
            if len(amap) != len(actual) or set(amap) != {row[key] for row in expected}:
                return [path + ': row identity set mismatch']
            for row in expected:
                errors.extend(differences(amap[row[key]], row, path + '[' + str(row[key]) + ']', money_tolerance))
        else:
            if any(not isinstance(x, str) for x in actual) or len(set(actual)) != len(actual) or set(actual) != set(expected):
                errors.append(path + ': normalized set mismatch')
    elif type(expected) is int:
        if type(actual) is not int:
            errors.append(path + ': integer required')
        elif abs(actual - expected) > (money_tolerance if any(part.endswith('_minor') for part in path.split('.')) else 0):
            errors.append(path + ': numeric business result mismatch')
    elif type(actual) is not type(expected) or actual != expected:
        errors.append(path + ': business classification mismatch')
    return errors

def evaluate(submission, gold=None):
    gold = gold if gold is not None else json.loads(GOLD_PATH.read_text(encoding='utf-8'))
    points = []
    correct_scope = isinstance(submission, dict) and submission.get('program_id') == gold['program_id'] and submission.get('as_of') == gold['as_of']
    for point_id, key, weight in POINTS:
        errors = ['submission: incorrect program or as-of scope'] if not correct_scope else differences(submission.get(key), gold[key], key, 5 if point_id == 'N6' else 0)
        assigned = weight / TOTAL_WEIGHT
        points.append({'point_id': point_id, 'weight': weight, 'passed': not errors,
                       'assigned_score': assigned, 'earned_score': assigned if not errors else 0.0,
                       'details': {'result': 'passed' if not errors else 'failed', 'mismatches': errors}})
    return {'score': sum(p['earned_score'] for p in points), 'points': points}

def main():
    if len(sys.argv) != 2:
        print(json.dumps({'error': 'usage: evaluate.py <submission.json>'}))
        return 2
    try:
        submission = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        if not isinstance(submission, dict):
            raise ValueError('submission root must be a JSON object')
        result = evaluate(submission)
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({'error': 'parse/control failure', 'details': str(exc)}))
        return 2
    print(json.dumps(result, separators=(',', ':')))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
