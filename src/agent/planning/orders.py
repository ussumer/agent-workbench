"""Persist and execute a submitted plan, without solving it or approving it."""

from __future__ import annotations

import json
import uuid
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from agent.approval.middleware import MCPWriteChannel
from agent.approval.models import ApprovalError, PendingAction, PendingStatus
from agent.approval.service import ApprovalService, canonical_bytes
from agent.approval.store import MongoPendingActionStore
from agent.persistence.indexes import COLLECTION_PLANNING_GOALS
from mcp_server.tools.registry import ORDER_CREATE

from .checker import order_drafts
from .models import Plan, PlanningProblem
from .reconciliation import committed_lines, revision_metadata
from .revisions import Revision


class PlanningOrders:
    def __init__(self, database: Any, *, grant_secret: str) -> None:
        self.goals = database[COLLECTION_PLANNING_GOALS]
        self.approvals = ApprovalService(
            store=MongoPendingActionStore(database), grant_secret=grant_secret,
            planning_guard=self.validate_action,
        )

    @staticmethod
    def scope(owner: str, thread: str, goal: str) -> dict:
        # These values are supplied by authenticated application code, never tool args.
        if not all(isinstance(v, str) and v.strip() for v in (owner, thread, goal)):
            raise ApprovalError("INVALID_SCOPE", "owner, thread and goal are required")
        return {"owner_user_id": owner, "thread_id": thread, "goal_id": goal}

    def load(self, owner: str, thread: str, goal: str) -> dict:
        row = self.goals.find_one(self.scope(owner, thread, goal))
        if row is None:
            raise ApprovalError("GOAL_NOT_FOUND", "no planning goal in this scope")
        return row

    def create(self, owner: str, thread: str, problem: PlanningProblem) -> dict:
        key = self.scope(owner, thread, problem.goal_id)
        try:
            self.goals.insert_one({
                **key, "revision": problem.revision, "problem": problem.model_dump(mode="json"),
                "proposal": None, "execution": None, "orders": [],
            })
        except DuplicateKeyError:
            old = self.load(owner, thread, problem.goal_id)
            if old["problem"] != problem.model_dump(mode="json"):
                raise ApprovalError("GOAL_EXISTS", "use a revision to change a goal") from None
        return self.load(owner, thread, problem.goal_id)

    def revise(self, owner: str, thread: str, problem: PlanningProblem) -> dict:
        key = self.scope(owner, thread, problem.goal_id)
        row = self.goals.find_one_and_update(
            {**key, "revision": problem.revision - 1, "execution": None},
            {"$set": {"revision": problem.revision,
                      "problem": problem.model_dump(mode="json"), "proposal": None}},
            return_document=ReturnDocument.AFTER,
        )
        if row is None:
            raise ApprovalError("REVISION_CONFLICT", "stale revision or write still in progress")
        return row

    def revise_reconciled(self, owner: str, thread: str, previous: dict,
                          revision: Revision, *, sources: list[dict]) -> dict:
        """CAS includes the order set verified by real ERP reads, never a stale baseline."""
        if revision.problem.revision == previous["revision"]:
            current = self.load(owner, thread, revision.problem.goal_id)
            if current != previous or current["execution"] is not None:
                raise ApprovalError("REVISION_CONFLICT", "goal changed during reconciliation")
            return current
        if revision.problem.revision != previous["revision"] + 1:
            raise ApprovalError("REVISION_CONFLICT", "revision must advance exactly once")
        scope = self.scope(owner, thread, revision.problem.goal_id)
        frozen_ids = {c.order_id for c in revision.problem.commitments}
        if frozen_ids != {o["result"]["data"]["order_id"] for o in previous["orders"]}:
            raise ApprovalError("RECONCILIATION_REQUIRED", "new baseline must include all verified orders")
        metadata = revision_metadata(revision)
        row = self.goals.find_one_and_update(
            {**scope, "_id": previous["_id"], "revision": previous["revision"],
             "proposal": previous["proposal"], "orders": previous["orders"], "execution": None},
            {"$set": {"revision": revision.problem.revision,
                      "problem": revision.problem.model_dump(mode="json"), "proposal": None,
                      "sources": sources, "revision_change": metadata},
             "$push": {"revision_history": {"problem": previous["problem"],
                 "sources": previous.get("sources", []), "revision_change": previous.get("revision_change"),
                 "proposal_id": (previous["proposal"] or {}).get("id")}}},
            return_document=ReturnDocument.AFTER)
        if row is None:
            raise ApprovalError("REVISION_CONFLICT", "goal or order set changed while reading ERP")
        return row

    def prepare(self, owner: str, thread: str, plan: Plan) -> tuple[PendingAction, ...]:
        row = self.load(owner, thread, plan.goal_id)
        problem = PlanningProblem.model_validate(row["problem"])
        if {c.order_id for c in problem.commitments} != {o["result"]["data"]["order_id"] for o in row["orders"]}:
            raise ApprovalError("RECONCILIATION_REQUIRED", "existing orders must be reconciled first")
        drafts = order_drafts(problem, plan)  # rejects stale/foreign/illegal plans
        proposal = row["proposal"]
        if proposal is None or proposal["plan"] != plan.model_dump(mode="json"):
            proposal_id = uuid.uuid4().hex
            proposal = {"id": proposal_id, "plan": plan.model_dump(mode="json"), "drafts": [
                {"interrupt_id": f"planning-{proposal_id}-{i}", "arguments": draft}
                for i, draft in enumerate(drafts)
            ]}
            changed = self.goals.update_one(
                {"_id": row["_id"], "revision": plan.revision, "proposal": row["proposal"],
                 "execution": None, "orders": row["orders"]}, {"$set": {"proposal": proposal}},
            )
            if changed.modified_count != 1:
                raise ApprovalError("PLAN_CONFLICT", "plan changed or a write has started")
        actions = []
        # Repeating after a partial recording failure uses the same interrupt IDs.
        for draft in proposal["drafts"]:
            action = self.approvals.record(
                owner_user_id=owner, thread_id=thread, interrupt_id=draft["interrupt_id"],
                interrupt_value={"action_requests": [{"name": ORDER_CREATE,
                                                      "args": draft["arguments"]}]},
                planning_binding={"goal_id": plan.goal_id, "revision": plan.revision,
                                  "proposal_id": proposal["id"]},
            )
            self.validate_action(action)
            actions.append(action)
        return tuple(actions)

    def validate_action(self, action: PendingAction, *, executing: bool = False) -> None:
        binding = action.planning_binding
        if binding is None:
            return
        row = self.load(action.owner_user_id, action.thread_id, binding["goal_id"])
        proposal = row["proposal"]
        if (row["revision"] != binding["revision"] or proposal is None
                or proposal["id"] != binding["proposal_id"]):
            raise ApprovalError("STALE_PLAN", "the goal or proposed plan changed; approve again")
        draft = next((d for d in proposal["drafts"]
                      if d["interrupt_id"] == action.interrupt_id), None)
        if (draft is None or action.tool_name != ORDER_CREATE
                or not action.matches_bytes(canonical_bytes(ORDER_CREATE, draft["arguments"]))):
            raise ApprovalError("PARAMETERS_CHANGED", "approval does not match the current plan")
        if executing and (row["execution"] is None
                          or row["execution"]["interrupt_id"] != action.interrupt_id):
            raise ApprovalError("EXECUTION_REQUIRED", "planning writes require the execution lease")

    async def execute(
        self, owner: str, thread: str, interrupt_id: str, *, channel: MCPWriteChannel,
    ) -> dict:
        action = self.approvals.store.find(owner, thread, interrupt_id)
        if action is None or action.planning_binding is None:
            raise ApprovalError("ACTION_NOT_FOUND", "no planning approval in this scope")
        self.validate_action(action)
        row = self.load(owner, thread, action.planning_binding["goal_id"])
        for outcome in row["orders"]:
            if outcome["interrupt_id"] == interrupt_id:
                return outcome["result"]
        if action.status not in (PendingStatus.APPROVED, PendingStatus.EXECUTING,
                                 PendingStatus.EXECUTED):
            raise ApprovalError("APPROVAL_REQUIRED", "approve this individual order before writing")
        previous = row["execution"]
        if previous is not None and (previous["phase"] != "uncertain"
                                     or previous["interrupt_id"] != interrupt_id):
            raise ApprovalError("EXECUTION_BUSY", "another write or unreconciled result blocks execution")
        token = uuid.uuid4().hex
        lease = {"token": token, "interrupt_id": interrupt_id, "phase": "inflight"}
        won = self.goals.update_one(
            {"_id": row["_id"], "revision": row["revision"], "proposal.id": row["proposal"]["id"],
             "execution": previous, "orders": row["orders"]}, {"$set": {"execution": lease}},
        )
        if won.modified_count != 1:
            raise ApprovalError("EXECUTION_BUSY", "goal changed or another executor won")
        try:
            authorized = self.approvals.authorize(
                owner_user_id=owner, thread_id=thread, interrupt_id=interrupt_id,
                tool_name=ORDER_CREATE, arguments=action.payload,
            )
        except BaseException:
            self.goals.update_one({"_id": row["_id"], "execution.token": token},
                                  {"$set": {"execution": None}})
            raise
        try:
            result = await channel(name=ORDER_CREATE, arguments=action.payload,
                                   owner_user_id=owner, grant=authorized.grant)
            if isinstance(result, list):
                texts = [b["text"] for b in result
                         if isinstance(b, dict) and isinstance(b.get("text"), str)]
                if len(texts) != 1:
                    raise ApprovalError("UNKNOWN_WRITE_RESULT", "expected one MCP business envelope")
                result = texts[0]
            if isinstance(result, str):
                result = json.loads(result)
            # No success inference from strings or malformed envelopes.
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ApprovalError("UNKNOWN_WRITE_RESULT", "MCP returned no business envelope")
            if result["ok"] and not (isinstance(result.get("data"), dict)
                                     and result["data"].get("order_id")):
                raise ApprovalError("UNKNOWN_WRITE_RESULT", "successful write lacks its order ID")
            if not result["ok"] and (result.get("error") or {}).get("retryable"):
                raise ApprovalError("UNKNOWN_WRITE_RESULT", "upstream failure does not prove no write")
            self.approvals.finish(authorized=authorized, ok=result["ok"],
                                  error=None if result["ok"] else "MCP_WRITE_REJECTED")
            outcome = {"interrupt_id": interrupt_id, "operation_id": authorized.operation_id,
                       "revision": row["revision"], "result": result}
            if result["ok"]:
                outcome["approved_payload"] = action.payload
                outcome["commitments"] = committed_lines(PlanningProblem.model_validate(row["problem"]),
                    Plan.model_validate(row["proposal"]["plan"]), action.payload["supplier_id"],
                    result["data"]["order_id"])
            update: dict = {"$set": {"execution": None}}
            if result["ok"]:
                update["$push"] = {"orders": outcome}
            saved = self.goals.update_one({"_id": row["_id"], "execution.token": token}, update)
            if saved.modified_count != 1:
                raise ApprovalError("RESULT_PERSISTENCE_FAILED", "write outcome was not committed")
            return result
        except BaseException:
            # Timeouts/cancellation do not prove the ERP did not write. No lease expiry.
            self.goals.update_one({"_id": row["_id"], "execution.token": token},
                                  {"$set": {"execution.phase": "uncertain"}})
            raise
