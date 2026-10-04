"""TRACE-style text curation from real, completed training evidence."""
from __future__ import annotations

import hashlib
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
            name = call["name"]
            item = {"operation": name}
            if name == "planning_decision":
                args = call.get("args", {})
                decision = args.get("decision", args) if isinstance(args, dict) else {}
                if isinstance(decision, dict) and decision.get("status") in {"needs_information", "infeasible"}:
                    item["decision_status"] = decision["status"]
            actions.append(item)
        elif event["kind"] == "tool_observed":
            payload = event["payload"]
            if payload["name"] in {"computation_execute", "planning_check"}:
                actions.append({"operation": payload["name"]})
    if not actions or not result.get("model_calls"):
        raise ValueError("training requires real Actor actions")
    judge = result.get("final_judge") or result.get("initial_judge", {}).get("grade", {})
    return {"case_id": result.get("case_id", "planning-baseline"),
            "public_goal": {"demand_count": len(result.get("goal", {}).get("problem", {}).get("demands", [])),
                            "offer_count": len(result.get("goal", {}).get("problem", {}).get("offers", [])),
                            "revision": result.get("goal", {}).get("revision", 1)},
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


def contrastive_operations(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep real computation inputs and outcomes, including failed operations.

    Empty selected skills are explicitly labelled cold-start capability mining,
    not fabricated skill use. Long streams are marked excerpts with full hashes.
    """
    if not result.get("model_calls") or not result.get("episode_export"):
        raise ValueError("real exported Episode required")
    calls, records, selected = {}, [], []
    for event in result["episode_export"]["events"]:
        payload = event["payload"]
        if event["kind"] == "turn_grounded":
            selected = payload["selection"]
        elif event["kind"] == "tool_requested":
            call = payload["call"]
            if call["name"] == "computation_execute":
                calls[call["id"]] = (call, list(selected))
        elif event["kind"] == "tool_observed" and payload["name"] == "computation_execute":
            binding = calls.pop(payload["tool_call_id"], None)
            if binding is None:
                raise ValueError("unpaired computation observation")
            call, skills = binding
            observation = json.loads(payload["raw_result"]["content"])
            data = observation.get("data") or {}
            if data.get("status") not in {"completed", "failed"}:
                continue
            streams = {}
            for field in ("stdout", "stderr"):
                stream = data.get(field, "")
                streams[field] = {"excerpt": stream[:2000], "truncated": len(stream) > 2000,
                                  "sha256": hashlib.sha256(stream.encode()).hexdigest()}
            records.append({"episode_id": result["episode_export"]["episode"]["_id"],
                            "event_id": event["event_id"], "selected_skills": skills,
                            "mode": "skill-refinement" if skills else "cold-start-mining",
                            "operation": call["name"], "input": call.get("args", {}),
                            "outcome": data["status"], "observations": streams,
                            "feedback": {"error": observation.get("error"),
                                         "base_version": data.get("base_version"),
                                         "version": data.get("version")}})
    if not records:
        raise ValueError("no completed computation evidence")
    return records
