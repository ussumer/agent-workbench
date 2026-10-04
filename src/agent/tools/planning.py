"""Public planning tools. Solving and clarification choices remain with the Actor."""

from __future__ import annotations

import json
from dataclasses import asdict

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.types import interrupt
from pydantic import ValidationError

from agent.approval.middleware import MCPWriteChannel
from agent.approval.models import ApprovalError, PendingStatus
from agent.artifacts.service import resolve_scope
from agent.planning.checker import check_plan
from agent.planning.models import Plan, PlanningProblem
from agent.planning.orders import PlanningOrders
from agent.planning.decisions import PlanningDecision, record_decision


def build_planning_tools(orders: PlanningOrders, channel: MCPWriteChannel) -> list[StructuredTool]:
    def scope(config: RunnableConfig) -> tuple[str, str]:
        result = resolve_scope(config)
        if result is None:
            raise ApprovalError("PLANNING_SCOPE_REQUIRED", "trusted owner/thread required")
        return result

    def public_problem(config: RunnableConfig) -> tuple[tuple[str, str], PlanningProblem]:
        owner, thread = scope(config)
        row = orders.load(owner, thread, thread)
        return (owner, thread), PlanningProblem.model_validate(row["problem"])

    def error(failure: Exception) -> str:
        return json.dumps({"ok": False, "data": None, "error": {
            "code": getattr(failure, "code", "INVALID_PLAN"), "message": str(failure),
        }}, ensure_ascii=False)

    def reply(value: object) -> str:
        return json.dumps({"ok": True, "data": value, "error": None}, ensure_ascii=False)

    def planning_read(config: RunnableConfig, part_id: str | None = None) -> str:
        """Read this thread's public constraints and evidence. Optional part_id reads only
        the affected part. Required quantities/deadlines come before optional coverage and cost.
        """
        try:
            (owner, thread), problem = public_problem(config)
            body = problem.model_dump(mode="json")
            row = orders.load(owner, thread, thread)
            body["revision_change"] = row.get("revision_change")
            body["decision"] = row.get("decision")
            body["plan_semantics"] = "additional quantities; commitments are already ordered and count toward the total budget"
            body["orders"] = [o["result"]["data"] for o in row["orders"]]
            body["execution_state"] = (row["execution"] or {}).get("phase")
            if part_id is not None:
                if not any(d.part_id == part_id for d in problem.demands):
                    raise ApprovalError("UNKNOWN_PART", "part is not in this goal")
                body["demands"] = [d for d in body["demands"] if d["part_id"] == part_id]
                body["offers"] = [o for o in body["offers"] if o["part_id"] == part_id]
                body["commitments"] = [c for c in body["commitments"] if c["part_id"] == part_id]
                body["orders"] = [{**o, "lines": [line for line in o.get("lines", []) if line["part_id"] == part_id]}
                                  for o in body["orders"] if any(line["part_id"] == part_id for line in o.get("lines", []))]
            return reply(body)
        except (ApprovalError, ValidationError) as failure:
            return error(failure)

    def planning_source(part_id: str, config: RunnableConfig) -> str:
        """Read the archived real ERP response for one part. Missing/conflicting evidence
        is not proof of infeasibility; examine sources rather than inventing a value.
        """
        try:
            owner, thread = scope(config)
            row = orders.load(owner, thread, thread)
            sources = [s for s in row.get("sources", []) if s["part_id"] == part_id]
            if not sources:
                raise ApprovalError("SOURCE_NOT_ARCHIVED", "no original source archived for this part")
            return reply(sources)
        except ApprovalError as failure:
            return error(failure)

    def planning_check(plan: Plan, config: RunnableConfig) -> str:
        """Check additional quantities plus frozen commitments against cumulative constraints.
        Does not solve or rank
        optimal plans. Create and compare your own candidates in the persistent kernel.
        """
        try:
            _, problem = public_problem(config)
            return reply(asdict(check_plan(problem, plan)))
        except (ApprovalError, ValidationError) as failure:
            return error(failure)

    async def planning_submit(plan: Plan, config: RunnableConfig) -> str:
        """Submit a feasible candidate, then wait for each order's separate human decision.
        Submitting is not approval. Stale plans and changed parameters need new approval.
        """
        try:
            (owner, thread), problem = public_problem(config)
            checked = check_plan(problem, plan)
            if not checked.feasible:
                return error(ApprovalError("INVALID_PLAN", str(asdict(checked))))
            row = orders.load(owner, thread, thread)
            proposal = row["proposal"]
            if proposal is not None and proposal["plan"] == plan.model_dump(mode="json"):
                # A resumed tool must reuse the batch after its first order has committed.
                actions = tuple(orders.approvals.store.find(owner, thread, d["interrupt_id"])
                                for d in proposal["drafts"])
                if any(a is None for a in actions):
                    if row["orders"]:
                        raise ApprovalError("APPROVAL_STATE_MISSING", "cannot reconstruct a partially written batch")
                    actions = orders.prepare(owner, thread, plan)
            else:
                actions = orders.prepare(owner, thread, plan)
            results: list[dict] = []
            for action in actions:
                assert action is not None
                orders.validate_action(action)
                # Always consume the same interrupt sequence on replay, even for completed
                # earlier orders. Skipping one would apply its cached answer to the next.
                interrupt({
                    "action_requests": [{"name": action.tool_name, "args": action.payload,
                                         "description": action.summary}],
                    "review_configs": [{"action_name": action.tool_name,
                                        "allowed_decisions": ["approve", "reject"]}],
                    "planning_action_id": action.interrupt_id,
                })
                current = orders.approvals.store.find(owner, thread, action.interrupt_id)
                if current is None or current.status not in (
                    PendingStatus.APPROVED, PendingStatus.EXECUTING, PendingStatus.EXECUTED,
                ):
                    return json.dumps({"ok": False, "data": {"orders": results}, "error": {
                        "code": "APPROVAL_REQUIRED" if current is None else f"APPROVAL_{current.status.upper()}",
                        "message": "this individual order has no server-recorded approval",
                    }}, ensure_ascii=False)
                result = await orders.execute(owner, thread, action.interrupt_id, channel=channel)
                if not result["ok"]:
                    return json.dumps({"ok": False, "data": {"orders": results},
                                       "error": result["error"]}, ensure_ascii=False)
                # Grants and operation IDs stay on the control plane.
                results.append(result["data"])
            return reply({"orders": results, "check": asdict(checked)})
        except (ApprovalError, ValidationError, ValueError) as failure:
            return error(failure)

    def planning_decision(decision: PlanningDecision, config: RunnableConfig) -> str:
        """Record a reasoned request for missing information or business infeasibility.
        Cite current part IDs and source_refs. This records your claim, does not prove
        it, and never writes an order. Complete resolved data must not be called missing.
        """
        try:
            owner, thread = scope(config)
            return reply(record_decision(orders, owner, thread, decision))
        except (ApprovalError, ValidationError) as failure:
            return error(failure)

    return [StructuredTool.from_function(f) for f in
            (planning_read, planning_source, planning_check)] + [
                StructuredTool.from_function(coroutine=planning_submit),
                StructuredTool.from_function(planning_decision)]
