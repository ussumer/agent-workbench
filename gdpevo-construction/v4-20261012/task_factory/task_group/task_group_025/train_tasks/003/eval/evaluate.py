#!/usr/bin/env python3
"""Private deterministic outcome checks; no subjective narrative scoring."""
import json
import sys
from pathlib import Path

POINTS = [("R1", "settled_principal", 2), ("R2", "refund_claim", 2),
          ("R3", "tooling_disposition", 2), ("R4", "incremental_exposure", 3),
          ("R5", "technical_admissibility", 2), ("R6", "recovery_feasibility", 2),
          ("R7", "change_scope", 1)]
LIST_KEYS = {"categories": "cost_category", "candidates": "supplier_id",
             "alternatives": "supplier_id", "annual": "fiscal_period"}

def compare(actual, expected, path):
    if isinstance(expected, dict):
        if not isinstance(actual, dict): return [path + ": expected object"]
        errors = []
        for key, value in expected.items():
            if key not in actual: errors.append(path + "." + key + ": missing business result")
            else: errors.extend(compare(actual[key], value, path + "." + key))
        return errors
    if isinstance(expected, list):
        if not isinstance(actual, list): return [path + ": expected list"]
        key = LIST_KEYS.get(path.rsplit(".", 1)[-1])
        if len(actual) != len(expected): return [path + ": incorrect entity/period coverage"]
        if key:
            if any(not isinstance(x, dict) or key not in x for x in actual): return [path + ": missing stable row identifier"]
            identifiers = [x[key] for x in actual]
            if len(set(map(str, identifiers))) != len(identifiers): return [path + ": duplicate entity/period"]
            indexed = {str(x[key]): x for x in actual}; errors=[]
            for exp in expected:
                ident=str(exp[key])
                if ident not in indexed: errors.append(path + ": missing entity/period " + ident)
                else: errors.extend(compare(indexed[ident], exp, path + "[" + ident + "]"))
            return errors
        if any(not isinstance(x, str) for x in actual): return [path + ": expected string set"]
        observed = [x.strip() for x in actual]
        if len(set(observed)) != len(observed) or set(observed) != set(expected): return [path + ": incorrect business set"]
        return []
    if isinstance(expected, bool):
        valid = type(actual) is bool and actual == expected
    elif isinstance(expected, int):
        tolerance = 1 if path.endswith("_minor") else 0
        valid = type(actual) is int and abs(actual - expected) <= tolerance
    elif isinstance(expected, str):
        valid = isinstance(actual, str) and actual.strip() == expected
    else:
        valid = actual == expected
    return [] if valid else [path + ": incorrect business value"]

def score(submission):
    gold = json.loads((Path(__file__).resolve().parents[1] / "output/answer.json").read_text(encoding="utf-8"))
    points=[]; total=sum(x[2] for x in POINTS)
    target_ok = submission.get("program_id") == gold["program_id"] and submission.get("as_of") == gold["as_of"]
    for pid,key,weight in POINTS:
        errors = compare(submission.get(key), gold[key], key) if target_ok else ["Incorrect program or decision as-of"]
        assigned=weight/total;passed=not errors
        points.append({"point_id":pid,"weight":weight,"passed":passed,"assigned_score":assigned,
                       "earned_score":assigned if passed else 0,"details":errors or ["Business outcome reconciles with the effective records"]})
    return {"score":sum(p["weight"] for p in points if p["passed"])/total,"points":points}

def main():
    try:
        if len(sys.argv)!=2: raise ValueError("Expected one absolute submission path")
        path=Path(sys.argv[1])
        if not path.is_absolute(): raise ValueError("Submission path must be absolute")
        submission=json.loads(path.read_text(encoding="utf-8-sig"),parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))
        if not isinstance(submission,dict): raise ValueError("Submission must be a JSON object")
        print(json.dumps(score(submission),allow_nan=False))
        return 0
    except (OSError,ValueError,TypeError) as exc:
        print(json.dumps({"error":"parse_or_control_failure","details":str(exc)}))
        return 2
if __name__ == "__main__": sys.exit(main())
