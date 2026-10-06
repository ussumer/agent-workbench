"""Run the pre-RSI ordinary chat Agent on the v3 public task set.

This runner is deliberately outside the planning Actor.  It loads an immutable export of the
old source (for example ``8eccd26``), sends only each task's public ``input`` to the ordinary
chat graph, and grades the parsed response with the evaluator-side private rubric.  It never
imports planning modules from the current Harness and never writes the production assignment.

The old stack is started by ``tests.live.stack.running_stack`` from the supplied source export.
The export must be on ``PYTHONPATH`` and must contain ``src`` and ``tests``.  A source archive
does not retain its Git metadata, so the caller must state the revision recorded in the output.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _load_judge() -> Any:
    path = ROOT / "scripts/planning/gdpevo_expansion_judge.py"
    spec = importlib.util.spec_from_file_location("pre_rsi_v3_judge", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load evaluator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tasks(path: Path, split: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for row in payload["tasks"] if row.get("split") == split]
    if not rows:
        raise ValueError(f"no tasks with split={split!r} in {path}")
    return rows


def _prompt(task: dict[str, Any]) -> str:
    # The public request is included as context, but the structured input is authoritative.
    # No rubric, reference answer, control fixture, or training output is sent to the Agent.
    return (
        "你是采购决策助手。根据下面公开任务做出决策。只输出一个合法 JSON 对象，"
        "不要 Markdown、解释或执行下单。必须包含字段：disposition、source_selection、"
        "allocation、freight_cents、commitment_ledger、approval_request、freight_audit、"
        "erp_claim_conflicts。\n公开请求："
        + str(task.get("request", ""))
        + "\n公开输入 JSON："
        + json.dumps(task["input"], ensure_ascii=False, separators=(",", ":"))
    )


def _json_from_message(content: Any) -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("assistant response contains no JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("assistant response JSON is not an object")
    return value


def _last_content(messages: list[Any]) -> Any:
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        # Tool messages and empty assistant chunks are not the final decision.
        if content and getattr(message, "type", None) in {None, "ai", "assistant"}:
            return content
    raise ValueError("ordinary chat graph produced no assistant message")


async def _run_one(graph: Any, task: dict[str, Any], timeout: float) -> tuple[dict[str, Any], list[Any]]:
    from langchain_core.messages import HumanMessage

    state = await asyncio.wait_for(
        graph.ainvoke(
            {"messages": [HumanMessage(content=_prompt(task))]},
            {"configurable": {"thread_id": f"pre-rsi-{task['task_id']}-{uuid.uuid4().hex}"}},
        ),
        timeout=timeout,
    )
    messages = list(state.get("messages") or [])
    return _json_from_message(_last_content(messages)), messages


def _message_usage(messages: list[Any]) -> dict[str, int]:
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for message in messages:
        metadata = getattr(message, "usage_metadata", None) or {}
        for key, target in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens"), ("total_tokens", "total_tokens")):
            value = metadata.get(key)
            if isinstance(value, int):
                usage[target] += value
    return usage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-source", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--tasks", type=Path, default=ROOT / "fixtures/planning/gdpevo-procurement-v3.json")
    parser.add_argument("--control", type=Path, default=ROOT / "fixtures/planning/private/gdpevo-procurement-v3-control.json")
    parser.add_argument("--split", choices=("train", "test"), default="test")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1 or args.limit < 0:
        raise SystemExit("--repeats must be >= 1 and --limit must be >= 0")
    source = args.old_source.resolve()
    if not (source / "src").is_dir() or not (source / "tests").is_dir():
        raise SystemExit("--old-source must contain src/ and tests/")
    sys.path[:0] = [str(source / "src"), str(source / "tests"), str(ROOT)]
    from agent.config import ModelConfig
    from live.stack import running_stack

    judge = _load_judge()
    tasks = _tasks(args.tasks, args.split)
    if args.limit:
        tasks = tasks[: args.limit]
    control = json.loads(args.control.read_text(encoding="utf-8"))["rubrics"]
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "source_revision": args.source_revision,
        "source_path": str(source),
        "task_set": str(args.tasks),
        "split": args.split,
        "repeats": args.repeats,
        "model": ModelConfig.from_env().redacted(),
        "private_judge": str(args.control),
        "private_data_sent_to_actor": False,
        "rows": [],
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    started = time.time()
    with running_stack(model_config=ModelConfig.from_env(), warm_pool_size=1) as stack:
        graph = stack.graphs["demo-a"]
        for repeat in range(1, args.repeats + 1):
            for task in tasks:
                row: dict[str, Any] = {"task_id": task["task_id"], "repeat": repeat, "status": "failed"}
                try:
                    decision, messages = asyncio.run(_run_one(graph, task, args.timeout_seconds))
                    row["submission"] = decision
                    row["usage"] = _message_usage(messages)
                    row["grade"] = judge.grade_task(task, decision, control[task["task_id"]])
                    row["status"] = "scored"
                except Exception as failure:  # preserve failures as denominator evidence
                    row["error"] = {"type": type(failure).__name__, "message": str(failure)}
                raw = json.dumps(row, ensure_ascii=False, sort_keys=True).encode()
                row["row_sha256"] = hashlib.sha256(raw).hexdigest()
                manifest["rows"].append(row)
                (args.output / f"{task['task_id']}-r{repeat}.json").write_text(
                    json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                (args.output / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
                )
    manifest["finished_at"] = time.time()
    manifest["elapsed_seconds"] = manifest["finished_at"] - started
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
