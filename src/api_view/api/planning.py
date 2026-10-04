"""Trusted goal creation/revision; quotations and committed orders come from real ERP."""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from agent.approval.models import ApprovalError
from agent.env_utils import load_env
from agent.persistence.repository import OwnershipViolation
from agent.planning.erp import read_erp_problem
from agent.planning.models import Demand, Identifier, Money, Offer, PlanningProblem, PositiveInt
from agent.planning.orders import PlanningOrders
from agent.planning.reconciliation import verify_orders, with_commitments
from agent.planning.revisions import revise_problem
from api_view.api.deps import WebContext, require_owner


class GoalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    budget: Money
    demands: tuple[Demand, ...]


class GoalRevisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: PositiveInt
    budget: Money | None = None
    demands: tuple[Demand, ...] = ()
    refresh_part_ids: tuple[Identifier, ...] = ()


def build_planning_router(context: WebContext) -> APIRouter:
    router = APIRouter(prefix="/planning", tags=["planning"])

    def service() -> PlanningOrders:
        return PlanningOrders(context.resources.database, grant_secret=context.approvals.grant_secret)

    def client_factory(owner: str):
        factory = context.extra.get("planning_erp_client")
        if factory is not None:
            return factory
        env = load_env()
        if not env.get("ERP_SERVICE_TOKEN") or not env.get("ERP_BASE_URL"):
            raise HTTPException(503, "trusted ERP client is not configured")
        def configured_client():
            return httpx.Client(base_url=env["ERP_BASE_URL"], headers={
                "X-Service-Token": env["ERP_SERVICE_TOKEN"], "X-Actor-Id": owner},
                trust_env=False, timeout=httpx.Timeout(15, connect=3))
        return configured_client

    def archive_sources(client, sources: list[dict]) -> None:
        def archive(response):
            response.read()
            if response.is_success and "/parts/" in response.request.url.path:
                sources.append({"part_id": response.request.url.path.rsplit("/", 1)[-1],
                                "path": response.request.url.path, "body": response.json()})
        client.event_hooks.setdefault("response", []).append(archive)

    @router.post("/{thread_id}/goal")
    def create_goal(thread_id: str, body: GoalInput, request: Request) -> dict:
        owner = require_owner(request)
        if not context.extra.get("planning_graph_provider"):
            raise HTTPException(503, "planning Actor is not configured")
        try:
            context.repository.ensure_thread(owner_user_id=owner, thread_id=thread_id,
                                             title="采购规划")
        except OwnershipViolation as failure:
            raise HTTPException(404, "no such planning conversation") from failure
        orders = service()
        if (orders.goals.find_one(orders.scope(owner, thread_id, thread_id)) is None
                and context.resources.database.display_messages.count_documents(
                    {"owner_user_id": owner, "thread_id": thread_id})):
            raise HTTPException(409, "use a fresh conversation for a planning goal")
        sources: list[dict[str, Any]] = []
        try:
            with client_factory(owner)() as client:
                client.headers["X-Actor-Id"] = owner
                archive_sources(client, sources)
                problem = read_erp_problem(client, goal_id=thread_id, budget=body.budget,
                                           demands=body.demands)
            row = orders.create(owner, thread_id, problem)
            orders.goals.update_one({"_id": row["_id"], "sources": {"$exists": False}},
                                    {"$set": {"sources": sources}})
            return {"data": problem.model_dump(mode="json")}
        except ApprovalError as failure:
            raise HTTPException(409, f"{failure.code}: {failure}") from failure

    @router.patch("/{thread_id}/goal")
    def revise_goal(thread_id: str, body: GoalRevisionInput, request: Request) -> dict:
        owner = require_owner(request)
        orders = service()
        try:
            row = orders.load(owner, thread_id, thread_id)
        except ApprovalError as failure:
            raise HTTPException(404, "no such planning goal") from failure
        if context.registry.get(thread_id) is not None or row["execution"] is not None:
            raise HTTPException(409, "GOAL_BUSY: finish or reconcile the active operation first")
        if row["revision"] != body.expected_revision:
            raise HTTPException(409, "REVISION_CONFLICT: reload the current goal")
        previous = PlanningProblem.model_validate(row["problem"])
        parts = {d.part_id for d in previous.demands}
        if (len(set(body.refresh_part_ids)) != len(body.refresh_part_ids)
                or not set(body.refresh_part_ids) <= parts):
            raise HTTPException(422, "refresh only unique existing demand parts")
        fresh_sources: list[dict] = []
        try:
            base = revise_problem(previous, budget=body.budget, demands=body.demands)
            with client_factory(owner)() as client:
                client.headers["X-Actor-Id"] = owner
                frozen = verify_orders(client, row, owner)
                fresh_offers: tuple[Offer, ...] = ()
                archive_sources(client, fresh_sources)
                if body.refresh_part_ids:
                    selected = tuple(d for d in base.problem.demands if d.part_id in body.refresh_part_ids)
                    fresh_offers = read_erp_problem(client, goal_id=thread_id,
                        budget=base.problem.budget, demands=selected).offers
            fresh_ids = {o.offer_id for o in fresh_offers}
            remove_ids = tuple(o.offer_id for o in previous.offers
                               if o.part_id in body.refresh_part_ids and o.offer_id not in fresh_ids)
            revised = revise_problem(previous, budget=body.budget, demands=body.demands,
                                     offers=fresh_offers, remove_offer_ids=remove_ids)
            revision = with_commitments(revised, previous, frozen)
            if context.registry.get(thread_id) is not None:
                raise HTTPException(409, "GOAL_BUSY: a run started during ERP reconciliation")
            sources = [s for s in row.get("sources", []) if s["part_id"] not in body.refresh_part_ids]
            updated = orders.revise_reconciled(owner, thread_id, row, revision,
                                              sources=[*sources, *fresh_sources])
            return {"data": {"problem": updated["problem"],
                "revision_change": updated.get("revision_change"), "orders": updated["orders"]}}
        except ApprovalError as failure:
            raise HTTPException(409, f"{failure.code}: {failure}") from failure
        except httpx.HTTPError as failure:
            raise HTTPException(503, "ERP reconciliation unavailable; goal unchanged") from failure
        except ValueError as failure:
            raise HTTPException(422, str(failure)) from failure

    @router.get("/{thread_id}/goal")
    def goal(thread_id: str, request: Request) -> dict:
        owner = require_owner(request)
        try:
            row = service().load(owner, thread_id, thread_id)
            return {"data": {"problem": row["problem"], "orders": row["orders"],
                             "revision_change": row.get("revision_change"),
                             "execution_state": (row["execution"] or {}).get("phase")}}
        except ApprovalError as failure:
            raise HTTPException(404, str(failure)) from failure
    return router
