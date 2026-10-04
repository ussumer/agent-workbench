"""Real one-shot content calibration; no production tool execution or training."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL = Path("/mnt/c/dev/rsi-eval")
for folder in (ROOT, ROOT / "src", EVAL):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from scripts.planning.gdpevo_oracle import grade_task  # noqa: E402
from scripts.planning.gdpevo_taskset import CONTROL_PATH, PUBLIC_PATH, load_taskset  # noqa: E402

SESSION = (
    EVAL / "procurement_eval/runs/planning-session-20261004/attempt-gdpevo-calibration-20261004"
)
LEDGER = SESSION.parent / "budget-ledger.json"
FIELDS = (
    "disposition",
    "source_selection",
    "allocation",
    "freight_cents",
    "commitment_ledger",
    "approval_request",
)
FORMAT = """仅输出JSON对象，可加 explanation 自由解释。六个必填字段：
1. disposition: execute / needs_information / infeasible。
2. source_selection: 以 part_id:supplier_id 为key的对象，记录每个供应关系采用的商务文件，值严格为 {quote_id:字符串,status:原始状态字符串,erp_active:布尔,erp_lead_days:整数}；包括拒绝采购的关系，供审计。
3. allocation: 本轮追加采购数组，行严格为 {quote_id:字符串,packs:正整数}；不能执行时为空。
4. freight_cents: 所有本轮新购供应商的运费分整数map（免邮也写0）；无本轮采购写{}。
5. commitment_ledger: 历史已成交行数组，行严格为 {order_id:字符串,part_id:字符串,quantity:整数,paid_cents:整数}。
6. approval_request: {revision:当前版本整数,lines:本轮追加allocation同结构,reuse_prior_approval:布尔}。这是请求审批的描述，不是下单。
数组顺序不影响评分。不要输出测试答案/评分猜测；自主比较方案。"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def messages_for(public, task, group):
    if task["split"] != "test" or group not in {"base", "policy-visible"}:
        raise ValueError("invalid calibration group or test task")
    system = (
        "你是采购决策助手。本次只分析公开业务资料，不调用工具、不下单。\n"
        + public["environment"]["public_contract"]
        + "\n"
        + FORMAT
    )
    if group == "policy-visible":
        policies = list(
            dict.fromkeys(
                m["text"]
                for t in public["tasks"]
                if t["split"] == "train"
                for m in t["training_materials"]
            )
        )
        system += "\n本诊断对照提供全部训练政策文字（没有参考答案）：\n" + "\n".join(policies)
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                {k: deepcopy(task[k]) for k in ("task_id", "request", "input")}, ensure_ascii=False
            ),
        },
    ]


def normalized(value):
    if not isinstance(value, dict) or not all(k in value for k in FIELDS):
        raise ValueError("missing output fields")
    result = deepcopy({k: value[k] for k in FIELDS})
    if result["disposition"] not in {"execute", "needs_information", "infeasible"}:
        raise ValueError("invalid disposition")
    if not isinstance(result["source_selection"], dict) or not isinstance(
        result["freight_cents"], dict
    ):
        raise ValueError("invalid map field")
    for line in result["source_selection"].values():
        if not isinstance(line, dict) or set(line) != {
            "quote_id",
            "status",
            "erp_active",
            "erp_lead_days",
        }:
            raise ValueError("invalid source_selection shape")
        if type(line["erp_active"]) is not bool or type(line["erp_lead_days"]) is not int:
            raise ValueError("invalid ERP field type")
    if any(type(n) is not int or n < 0 for n in result["freight_cents"].values()):
        raise ValueError("invalid freight cents")
    for field in ("allocation", "commitment_ledger"):
        if not isinstance(result[field], list):
            raise ValueError("invalid line collection")
    approval = result["approval_request"]
    if not isinstance(approval, dict) or set(approval) != {
        "revision",
        "lines",
        "reuse_prior_approval",
    }:
        raise ValueError("invalid approval shape")
    if type(approval["revision"]) is not int or type(approval["reuse_prior_approval"]) is not bool:
        raise ValueError("invalid approval types")
    for lines in (result["allocation"], approval["lines"]):
        if not isinstance(lines, list) or any(
            not isinstance(x, dict)
            or set(x) != {"quote_id", "packs"}
            or not isinstance(x["quote_id"], str)
            or type(x["packs"]) is not int
            or x["packs"] < 1
            for x in lines
        ):
            raise ValueError("invalid allocation shape")
        lines.sort(key=lambda x: x["quote_id"])
    for x in result["commitment_ledger"]:
        if not isinstance(x, dict) or set(x) != {"order_id", "part_id", "quantity", "paid_cents"}:
            raise ValueError("invalid commitment shape")
        if any(type(x[k]) is not int or x[k] < 0 for k in ("quantity", "paid_cents")):
            raise ValueError("invalid committed number")
    result["commitment_ledger"].sort(key=lambda x: (x["order_id"], x["part_id"]))
    return result


def score_response(task, rubric, response):
    raw = response["choices"][0]["message"]["content"]
    # No fixing malformed JSON with another paid model call.
    value = normalized(json.loads(raw))
    return grade_task(task, value, rubric), value


def summarize(rows):
    groups = {}
    for group in sorted({r["group"] for r in rows}):
        selected = [r for r in rows if r["group"] == group]
        groups[group] = {
            "attempts": len(selected),
            "all_correct": sum(r["grade"]["business_success"] for r in selected),
            "mean_score": sum(r["grade"]["score"] for r in selected) / len(selected),
            "model_calls": sum(r["metrics"]["model_calls"] for r in selected),
            "reserved_upper_cny": sum(r["metrics"]["reserved_upper_cny"] for r in selected),
        }
    return groups


def run(session=SESSION):
    import httpx
    from procurement_eval.budget_proxy import gateway
    from scripts.planning.live_baseline import reserve_attempt, settle_attempt

    from agent.config import ModelConfig
    from agent.env_utils import load_env, redact, secret_values

    public, control = load_taskset()
    env = load_env()
    config = json.loads((EVAL / "config.t46.json").read_text())
    model = ModelConfig.from_env({**env, "MODEL_ID": config["model_id"], "MODEL_TEMPERATURE": "0"})
    session.mkdir(parents=True, exist_ok=False)
    frozen = session / "frozen"
    frozen.mkdir()
    for path in (
        PUBLIC_PATH,
        CONTROL_PATH,
        ROOT / "scripts/planning/gdpevo_oracle.py",
        Path(__file__).resolve(),
    ):
        (frozen / path.name).write_bytes(path.read_bytes())
    hashes = {p.name: digest(p) for p in frozen.iterdir()}
    write(
        session / "protocol.json",
        {
            "kind": "real-model-one-shot-synthetic-content-calibration",
            "model_id": model.model_id,
            "temperature": 0,
            "max_output_tokens": 4096,
            "thinking": "disabled",
            "max_calls_per_task": 1,
            "tools": [],
            "retries": 0,
            "groups": ["base", "policy-visible"],
            "total_limit_cny": 50,
            "per_task_limit_cny": 2,
            "fixture_hashes": hashes,
            "policy_visible_is_training": False,
            "production_actor_run": False,
        },
    )
    rows = []
    network_failures = 0
    for group in ("base", "policy-visible"):
        if group == "policy-visible" and all(r["grade"]["business_success"] for r in rows):
            break
        for task in (t for t in public["tasks"] if t["split"] == "test"):
            directory = session / group / task["task_id"]
            directory.mkdir(parents=True)
            reservation = reserve_attempt(LEDGER, 2.0, 50.0)
            messages = messages_for(public, task, group)
            payload = {
                "model": model.model_id,
                "messages": messages,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "stream": False,
                "max_tokens": 4096,
            }
            write(directory / "request.json", payload)
            metrics = {"model_calls": 0, "reserved_upper_cny": 0.0}
            budget = None
            row = {
                "group": group,
                "task_id": task["task_id"],
                "status": "started",
                "started_at": time.time(),
                "reservation_id": reservation["reservation_id"],
                "grade": {"score": 0.0, "business_success": False, "points": {}},
                "errors": [],
            }
            spec = {
                "total_cny": 50.0,
                "per_attempt_cny": 2.0,
                "max_model_calls": 1,
                "max_output_tokens": 4096,
                "max_request_bytes": 131072,
                "timeout_seconds": 120,
                "input_cny_per_million": 9.0,
                "output_cny_per_million": 27.0,
                "pricing_kind": "conservative estimate, not provider bill",
                "pricing_source": "existing session evaluation policy",
            }
            print(f"Starting {group}/{task['task_id']}", flush=True)
            try:
                with (directory / "model_calls.jsonl").open("x") as log:
                    with gateway(model, spec, log, {"thinking": {"type": "disabled"}}) as (
                        url,
                        budget,
                    ):
                        with httpx.Client(timeout=125, trust_env=False) as client:
                            res = client.post(url + "/chat/completions", json=payload)
                        (directory / "response.txt").write_text(
                            redact(res.text, secret_values(env)), encoding="utf-8"
                        )
                        row["http_status"] = res.status_code
                        res.raise_for_status()
                        response = res.json()
                        row["observed_model"] = response.get("model")
                        row["finish_reason"] = response["choices"][0].get("finish_reason")
                        row["grade"], submission = score_response(
                            task, control["rubrics"][task["task_id"]], response
                        )
                        write(directory / "submission.json", submission)
                        row["status"] = "scored"
                        network_failures = 0
            except (httpx.HTTPError, OSError) as exc:
                row["status"] = "environment_failed"
                row["errors"] = [type(exc).__name__]
                network_failures += 1
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                row["status"] = "format_failed"
                row["errors"] = [redact(str(exc), secret_values(env))]
                network_failures = 0
            finally:
                if budget is not None:
                    metrics = budget.summary()
                settle_attempt(LEDGER, reservation["reservation_id"], metrics, 50.0)
                row["metrics"] = metrics
                row["finished_at"] = time.time()
                write(directory / "result.json", row)
                rows.append(row)
                write(
                    session / "report.json",
                    {
                        "protocol": "T53-one-shot-calibration",
                        "rows": rows,
                        "groups": summarize(rows),
                        "claims": {
                            "learning_gain_proven": False,
                            "production_actor_run": False,
                            "single_repeat": True,
                        },
                    },
                )
            print(
                f"{group}/{task['task_id']}: {row['status']} score={row['grade']['score']:.3f} all_correct={row['grade']['business_success']}",
                flush=True,
            )
            if network_failures >= 2:
                print("Stopped after two environment failures.", flush=True)
                return 2
    write(
        session / "manifest.json",
        {
            str(p.relative_to(session)): digest(p)
            for p in session.rglob("*")
            if p.is_file() and p.name != "manifest.json"
        },
    )
    print(json.dumps(summarize(rows), ensure_ascii=False), flush=True)
    return 0


def verify(session=SESSION):
    manifest = json.loads((session / "manifest.json").read_text())
    for path, sha in manifest.items():
        if digest(session / path) != sha:
            raise ValueError("evidence hash mismatch: " + path)
    public = json.loads((session / "frozen" / PUBLIC_PATH.name).read_text())
    control = json.loads((session / "frozen" / CONTROL_PATH.name).read_text())
    report = json.loads((session / "report.json").read_text())
    tasks = {t["task_id"]: t for t in public["tasks"]}
    for row in report["rows"]:
        directory = session / row["group"] / row["task_id"]
        request = json.loads((directory / "request.json").read_text())
        if request["messages"] != messages_for(public, tasks[row["task_id"]], row["group"]):
            raise ValueError("staged message boundary mismatch")
        if row["metrics"]["model_calls"] != 1:
            raise ValueError("expected one real call per task")
        if row["status"] == "scored":
            response = json.loads((directory / "response.txt").read_text())
            grade, _ = score_response(
                tasks[row["task_id"]], control["rubrics"][row["task_id"]], response
            )
            if grade != row["grade"]:
                raise ValueError("independent scoring mismatch")
    expected_ids = {t["task_id"] for t in public["tasks"] if t["split"] == "test"}
    for group in report["groups"]:
        if {r["task_id"] for r in report["rows"] if r["group"] == group} != expected_ids:
            raise ValueError("incomplete group")
    if "policy-visible" not in report["groups"] and report["groups"]["base"]["all_correct"] != 5:
        raise ValueError("non-saturated base needs policy-visible diagnostic")
    if summarize(report["rows"]) != report["groups"]:
        raise ValueError("summary mismatch")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "verify"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        return run()
    report = verify()
    if args.output:
        write(args.output, report)
    print(json.dumps(report["groups"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
