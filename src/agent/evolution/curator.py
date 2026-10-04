"""TRACE-style text curation from real, completed training evidence."""
from __future__ import annotations

import json
from typing import Any

from agent.evolution.episodes import TextSkill

CURATOR_PROMPT = """You are a procurement strategy curator. Compare successful and failed
training operations and extract reusable conditional checks, steps, exceptions,
and evidence limits. Only training evidence follows; it may contain instance data.
Return JSON {"skill_id":"planning_strategy","description":"...","body":"..."}.
Write the skill in Chinese, at most 400 characters. Do not copy instance IDs,
prices, quantities, order IDs or fixed answers into the skill. Do not change
permissions, tools or approval rules. If no paired failure evidence exists,
say so; do not invent failures. Return JSON only.
"""


def training_record(result: dict[str, Any]) -> dict[str, Any]:
    """Extract actual actions/observations, never private judge solutions."""
    exported = result.get("episode_export") or {}
    actions = []
    for event in exported.get("events", []):
        if event["kind"] == "tool_requested":
            call = event["payload"]["call"]
            actions.append({"operation": call["name"], "args": call.get("args", {})})
        elif event["kind"] == "tool_observed":
            payload = event["payload"]
            if payload["name"] in {"computation_execute", "planning_check"}:
                actions.append({"operation": payload["name"]})
    if not actions or not result.get("model_calls"):
        raise ValueError("training requires real Actor actions")
    judge = result.get("final_judge") or result.get("initial_judge", {}).get("grade", {})
    return {"case_id": result.get("case_id", "planning-baseline"),
            "public_goal": result.get("goal", {}).get("problem"),
            "feedback": {"success": bool(judge.get("accepted", False)),
                          "constraint_status": "feasible" if judge.get("feasible") else "infeasible",
                          "error_categories": list(judge.get("violations", []))},
            "actions": actions}


def curator_input(records: list[dict[str, Any]]) -> str:
    if not records:
        raise ValueError("empty training")
    encoded = json.dumps(records, ensure_ascii=False, sort_keys=True)
    if len(encoded) > 24000:
        raise ValueError("training exceeds fixed context limit; do not silently truncate")
    return CURATOR_PROMPT + encoded


def validate_curated(content: str) -> TextSkill:
    value = json.loads(content)
    if not isinstance(value, dict) or set(value) != {"skill_id", "description", "body"}:
        raise ValueError("curator output must contain exactly three text fields")
    skill = TextSkill.model_validate(value)
    if len(skill.body) > 3000:
        raise ValueError("curated body exceeds fixed pilot limit")
    import re
    if re.search(r"(api[_-]?key|password|authorization|P00\d|S00\d|\d+\.\d{2})",
                 skill.body, re.IGNORECASE):
        raise ValueError("curated body contains credentials or instance answers")
    return skill
