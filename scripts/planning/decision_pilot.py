"""Run and verify one real success/rejection TRACE contrast."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL = Path("/mnt/c/dev/rsi-eval")
for path in (ROOT / "src", EVAL):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.planning.evolution_pilot import (  # noqa: E402
    call_curator,
    public_case,
    run_baseline,
)

from agent.evolution.curator import contrastive_operations, training_record  # noqa: E402


def install_source_mask(stack, part_id: str, attempt: dict) -> None:
    """Mask named fields after a real ERP read; archive both evidence views."""
    import urllib.parse

    import httpx

    class SourceMask(httpx.BaseTransport):
        def __init__(self):
            self.transport = httpx.HTTPTransport()

        def handle_request(self, request):
            response = self.transport.handle_request(request)
            response.read()
            if (
                urllib.parse.unquote(request.url.path).endswith("/parts/" + part_id)
                and response.is_success
            ):
                body = response.json()
                original = json.loads(json.dumps(body))
                for offer in body["data"]["available_suppliers"]:
                    offer["catalog_price"] = None
                    offer["lead_days"] = None
                attempt.setdefault("source_mask_evidence", []).append(
                    {
                        "part_id": part_id,
                        "path": request.url.path,
                        "original_response": original,
                        "deployment_response": body,
                        "masked_fields": ["catalog_price", "lead_days"],
                    }
                )
                headers = dict(response.headers)
                headers.pop("content-length", None)
                replacement = httpx.Response(
                    response.status_code, headers=headers, json=body, request=request
                )
                response.close()
                return replacement
            return response

        def close(self):
            self.transport.close()

    stack.app.state.context.extra["planning_erp_client"] = lambda: stack.erp.client(
        transport=SourceMask(), trust_env=False
    )


def run(output: Path, fixed_result_path: Path | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    if fixed_result_path is None:
        fixed = output / "fixed-business-infeasible"
        code = run_baseline(
            fixed,
            experiment_group="fixed-v1",
            case_id="business-infeasible",
            goal_data=public_case("business-infeasible"),
        )
        fixed_result = json.loads((fixed / "result.json").read_text())
    else:
        fixed = fixed_result_path.parent
        fixed_result = json.loads(fixed_result_path.read_text())
        code = 0 if fixed_result["terminal_status"] == "completed" else 1
    historical_path = EVAL / "procurement_eval/runs/planning-session-20261004/attempt-7/result.json"
    historical = json.loads(historical_path.read_text())
    contrast = contrastive_operations(historical)
    if {c["outcome"] for c in contrast} != {"completed", "failed"}:
        raise ValueError("real successful and failed operations required")
    (output / "contrastive-operations.json").write_text(
        json.dumps(contrast, ensure_ascii=False, indent=2) + "\n"
    )
    records = [training_record(fixed_result), {"operation_pairs": contrast}]
    (output / "training-records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n"
    )
    curated_path, curator_metrics = call_curator(records, output)
    curated = output / "curated-business-infeasible"
    curated_code = run_baseline(
        curated,
        experiment_group="curated-v1",
        skill_file=curated_path,
        case_id="business-infeasible",
        goal_data=public_case("business-infeasible"),
    )
    curated_result = json.loads((curated / "result.json").read_text())
    unseen = {}
    for case in ("deadline-budget-missing", "deadline-budget"):
        destination = output / case
        run_baseline(
            destination,
            experiment_group="curated-v1",
            skill_file=curated_path,
            case_id=case,
            goal_data=public_case(case),
            mask_source_part="P001" if case.endswith("missing") else None,
        )
        result = json.loads((destination / "result.json").read_text())
        unseen[case] = {
            "business_success": result.get("business_success", False),
            "status": result["terminal_status"],
            "model_calls": result["model_calls"],
            "judge": result.get("final_judge"),
            "decision": (result.get("final_goal") or {}).get("decision"),
            "path": str(destination),
            "errors": result["errors"],
        }
    report = {
        "schema_version": 1,
        "kind": "real-trace-contrastive-refinement",
        "train": ["attempt-7-computation-contrast", "fixed-business-infeasible"],
        "contrast_source": str(historical_path),
        "contrast_source_sha256": hashlib.sha256(historical_path.read_bytes()).hexdigest(),
        "fixed_result_path": str(fixed / "result.json"),
        "fixed_result_sha256": hashlib.sha256((fixed / "result.json").read_bytes()).hexdigest(),
        "unseen": unseen,
        "fixed": {
            "exit_code": code,
            "status": fixed_result["terminal_status"],
            "business_success": fixed_result.get("business_success", False),
            "model_calls": fixed_result.get("model_calls", 0),
            "judge": fixed_result.get("final_judge"),
            "decision": (fixed_result.get("final_goal") or {}).get("decision"),
        },
        "curated": {
            "exit_code": curated_code,
            "status": curated_result["terminal_status"],
            "business_success": curated_result.get("business_success", False),
            "model_calls": curated_result.get("model_calls", 0),
            "judge": curated_result.get("final_judge"),
            "decision": (curated_result.get("final_goal") or {}).get("decision"),
            "bank": curated_result.get("bank"),
        },
        "curator": curator_metrics,
        "claims": {
            "paired_success_failure_input": True,
            "real_curator": True,
            "proves_learning_gain": False,
        },
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    manifest = {
        p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in output.rglob("*")
        if p.is_file() and p.name != "evidence-manifest.json"
    }
    (output / "evidence-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return report


def verify(session: Path, output: Path) -> None:
    report = json.loads((session / "report.json").read_text())
    manifest = json.loads((session / "evidence-manifest.json").read_text())
    for name, expected in manifest.items():
        if hashlib.sha256((session / name).read_bytes()).hexdigest() != expected:
            raise ValueError("decision evidence hash mismatch")
    fixed_path = Path(report["fixed_result_path"])
    if hashlib.sha256(fixed_path.read_bytes()).hexdigest() != report["fixed_result_sha256"]:
        raise ValueError("reused fixed evidence changed")
    contrast = json.loads((session / "contrastive-operations.json").read_text())
    historical = Path(report["contrast_source"])
    if hashlib.sha256(historical.read_bytes()).hexdigest() != report["contrast_source_sha256"]:
        raise ValueError("contrast source changed")
    if contrast != contrastive_operations(json.loads(historical.read_text())):
        raise ValueError("contrast differs from original observed operations")
    if {c["outcome"] for c in contrast} != {"completed", "failed"}:
        raise ValueError("missing real operation contrast")
    if not report["curator"]["model_calls"]:
        raise ValueError("Curator model evidence is missing")
    from scripts.planning.judge import grade, solve

    from agent.planning.models import Plan, PlanningProblem

    for group in ("fixed", "curated"):
        item = report[group]
        result_path = (
            fixed_path if group == "fixed" else session / "curated-business-infeasible/result.json"
        )
        result = json.loads(result_path.read_text())
        problem = PlanningProblem.model_validate(result["final_goal"]["problem"])
        if (
            solve(problem).status != "infeasible"
            or result["erp_after"]
            or result["final_goal"].get("proposal") is not None
            or result["final_goal"].get("orders")
            or result["final_goal"].get("decision") != item["decision"]
        ):
            raise ValueError(f"{group} refusal does not match original business evidence")
        if item["status"] != "completed" or not item["business_success"]:
            raise ValueError(f"{group} did not record the expected no-write decision")
        if item["judge"]["environment_status"] != "infeasible":
            raise ValueError(f"{group} independent judge did not classify infeasible")
        if item["decision"]["status"] != "infeasible":
            raise ValueError(f"{group} did not declare infeasible")
    for case, item in report["unseen"].items():
        if item["status"] != "completed" or not item["business_success"] or not item["model_calls"]:
            raise ValueError(f"unseen case {case} failed")
        result = json.loads((Path(item["path"]) / "result.json").read_text())
        problem = PlanningProblem.model_validate(result["final_goal"]["problem"])
        environment = solve(problem).status
        if case.endswith("missing"):
            if (
                environment != "unresolved"
                or item["decision"]["status"] != "needs_information"
                or result["erp_after"]
                or not result.get("source_mask_evidence")
            ):
                raise ValueError("missing evidence must produce clarification and zero writes")
        elif (
            environment != "feasible"
            or not grade(
                problem, Plan.model_validate(result["final_goal"]["proposal"]["plan"])
            ).accepted
        ):
            raise ValueError("resolved control did not produce an accepted candidate")
    output.write_text(
        json.dumps(
            {
                "verified": True,
                "fixed_business_success": report["fixed"]["business_success"],
                "curated_business_success": report["curated"]["business_success"],
                "claims": report["claims"],
                "source": str(session),
                "unseen": report["unseen"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--fixed-result", type=Path)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        report = run(args.output, args.fixed_result)
        print(json.dumps(report, ensure_ascii=False))
    else:
        session = Path(os.environ["DECISION_PILOT_SESSION"])
        verify(session, args.output)
        print(json.dumps({"verified": True, "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
