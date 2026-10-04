"""Scripted model ONLY for wiring; real Mongo/checkpoints, Java and MCP. No kernel calls."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
import test_t39 as services
from deepagents.backends import StateBackend
from langchain_core.messages import AIMessage
from langgraph.types import Command
from starlette.testclient import TestClient
from test_t12 import ScriptedChatModel, tool_call
from test_t39 import INTERNAL, OWNER, actual_orders

from agent.artifacts.service import ArtifactService
from agent.artifacts.store import MongoArtifactStore
from agent.async_tasks.service import build_async_task_service
from agent.persistence.repository import ApplicationRepository
from agent.planning.actor import FORBIDDEN_TOOLS, build_planning_actor
from agent.planning.computation import ComputationService
from agent.tools.planning import build_planning_tools
from api_view.api.deps import WebContext
from api_view.run_registry import RunRegistry
from api_view.web_config import MongoResources, PersistenceSettings
from api_view.web_main import create_app

db = services.db
stack = services.stack
scenario = services.scenario

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def resources(db):
    result = MongoResources(PersistenceSettings.from_env().for_test_database(
        db.name.removeprefix("rush_harness_test_"))).start()
    try:
        yield result
    finally:
        result.close()


class InspectingModel(ScriptedChatModel):
    tool_names: list[str] = []

    def bind_tools(self, tools, **kwargs):
        self.tool_names = [t.name if hasattr(t, "name") else t["name"] for t in tools]
        return self


@pytest.fixture
def actor(scenario, resources):
    orders, thread, problem, plan, erp, channel = scenario
    repository = ApplicationRepository(resources.database)
    repository.ensure_thread(owner_user_id=OWNER, thread_id=thread)

    def never_start_kernel(owner):
        raise AssertionError("T38 is paused; this wiring test must not start a kernel")

    kernel = ComputationService(resources.database, never_start_kernel)
    model = InspectingModel(script=[tool_call("planning_submit", {"plan": plan.model_dump(mode="json")})])
    graph = build_planning_actor(orders=orders, kernel=kernel, channel=channel,
                                backend=StateBackend(), checkpointer=resources.checkpointer,
                                store=resources.store, model=model)
    artifacts = ArtifactService(MongoArtifactStore(resources.database))

    def no_course_fallback(owner):
        raise AssertionError("planning conversation must select the independent Actor")

    graphs = {OWNER: graph}
    context = WebContext(
        resources=resources, repository=repository, registry=RunRegistry(),
        approvals=orders.approvals, artifacts=artifacts,
        async_tasks=build_async_task_service(resources.database, artifacts=artifacts),
        settings=resources.settings, graph_provider=no_course_fallback,
        internal_service_token=INTERNAL,
        extra={"planning_graph_provider": lambda owner: graphs[owner],
               "planning_erp_client": erp.client},
    )
    with TestClient(create_app(context=context)) as client:
        client.post("/api/demo/session", json={"user_id": OWNER})
        yield client, context, graphs, model, kernel, scenario


def frames(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[5:].strip()) for line in response.text.splitlines() if line.startswith("data:")]


def start(actor):
    client, _, _, _, _, scenario = actor
    return frames(client.post("/api/chat/stream", json={"thread_id": scenario[1],
        "request_id": uuid.uuid4().hex, "message": "计算候选并逐单提交审批"}))


def pending(actor):
    client, _, _, _, _, scenario = actor
    result = client.get(f"/api/chat/{scenario[1]}/state")
    assert result.status_code == 200, result.text
    interrupts = result.json()["data"]["pending_interrupts"]
    assert len(interrupts) == 1, result.text
    return interrupts[0]


def resume(actor, decision="approve"):
    client, _, _, _, _, scenario = actor
    i = pending(actor)
    return frames(client.post(f"/api/chat/{scenario[1]}/resume", json={
        "request_id": uuid.uuid4().hex, "interrupt_id": i["interrupt_id"],
        "resume": {"decisions": [{"type": decision}]},
    }))


def test_formal_chat_resumes_each_order_once_and_keeps_original_operations(actor):
    _, context, _, _, _, scenario = actor
    orders, thread, _, _, erp, _ = scenario
    before = actual_orders(erp)["total"]
    events = start(actor)
    assert events[-1]["payload"]["status"] == "interrupted", events
    first = pending(actor)
    action = context.approvals.store.find(OWNER, thread, first["interrupt_id"])
    assert action.planning_binding is not None
    assert len(context.approvals.store.list_for_thread(OWNER, thread)) == 2
    assert actual_orders(erp)["total"] == before
    assert resume(actor)[-1]["payload"]["status"] == "interrupted"
    assert actual_orders(erp)["total"] == before + 1
    second = pending(actor)
    assert second["interrupt_id"] != first["interrupt_id"]
    assert resume(actor)[-1]["payload"]["status"] == "completed"
    assert actual_orders(erp)["total"] == before + 2
    row = orders.load(OWNER, thread, thread)
    assert len(row["orders"]) == 2
    assert len({r["operation_id"] for r in row["orders"]}) == 2
    assert len(context.approvals.store.list_for_thread(OWNER, thread)) == 2


def test_rejection_of_first_order_stops_batch_without_writing(actor):
    erp = actor[-1][4]
    before = actual_orders(erp)["total"]
    start(actor)
    assert resume(actor, "reject")[-1]["payload"]["status"] == "completed"
    assert actual_orders(erp)["total"] == before


def test_rejection_of_second_order_preserves_only_first_real_order(actor):
    orders, thread, _, _, erp, _ = actor[-1]
    before = actual_orders(erp)["total"]
    start(actor)
    resume(actor)
    resume(actor, "reject")
    assert actual_orders(erp)["total"] == before + 1
    assert len(orders.load(OWNER, thread, thread)["orders"]) == 1


def test_direct_command_resume_cannot_approve_itself(actor):
    _, _, graphs, _, _, scenario = actor
    _, thread, _, _, erp, _ = scenario
    before = actual_orders(erp)["total"]
    start(actor)
    result = asyncio.run(graphs[OWNER].ainvoke(Command(resume={"decisions": [{"type": "approve"}]}),
        {"configurable": {"owner_user_id": OWNER, "thread_id": thread}}))
    outputs = [m.content for m in result["messages"] if getattr(m, "name", None) == "planning_submit"]
    assert "APPROVAL_PENDING" in outputs[-1]
    assert actual_orders(erp)["total"] == before


def test_rebuilding_graph_between_individual_approvals_restores_checkpoint(actor, resources):
    _, _, graphs, _, kernel, scenario = actor
    orders, thread, _, _, erp, channel = scenario
    before = actual_orders(erp)["total"]
    start(actor)
    resume(actor)
    graphs[OWNER] = build_planning_actor(orders=orders, kernel=kernel, channel=channel,
        backend=StateBackend(), checkpointer=resources.checkpointer, store=resources.store,
        model=InspectingModel(script=[AIMessage(content="两单审批完成")]))
    assert resume(actor)[-1]["payload"]["status"] == "completed"
    assert actual_orders(erp)["total"] == before + 2


def test_actor_excludes_shell_generic_delegation_and_direct_writes(actor):
    start(actor)
    names = set(actor[3].tool_names)
    assert not names & FORBIDDEN_TOOLS
    assert {"planning_read", "planning_check", "planning_submit", "computation_execute", "computation_status"} <= names
    assert not any("judge" in name or "solve" in name for name in names)


def test_tools_schema_keeps_business_identity_and_approval_off_model(scenario):
    orders, _, _, _, _, channel = scenario
    for tool in build_planning_tools(orders, channel):
        properties = tool.tool_call_schema.model_json_schema()["properties"]
        assert not set(properties) & {"owner_user_id", "thread_id", "grant", "operation_id", "approve", "config"}


def test_unpresented_second_approval_cannot_be_decided_early(actor):
    client, context, _, _, _, scenario = actor
    _, thread, _, _, erp, _ = scenario
    before = actual_orders(erp)["total"]
    start(actor)
    current = pending(actor)["interrupt_id"]
    future = next(a for a in context.approvals.store.list_for_thread(OWNER, thread)
                  if a.interrupt_id != current)
    response = client.post(f"/api/chat/{thread}/resume", json={
        "request_id": uuid.uuid4().hex, "interrupt_id": future.interrupt_id,
        "resume": {"decisions": [{"type": "approve"}]},
    })
    assert response.status_code == 400
    assert "STALE_INTERRUPT" in response.text
    assert actual_orders(erp)["total"] == before
    assert context.approvals.store.find(OWNER, thread, future.interrupt_id).status == "pending"


def test_reusing_first_approval_cannot_consume_second_order_interrupt(actor):
    client, _, _, _, _, scenario = actor
    _, thread, _, _, erp, _ = scenario
    start(actor)
    first_id = pending(actor)["interrupt_id"]
    resume(actor)
    before = actual_orders(erp)["total"]
    response = client.post(f"/api/chat/{thread}/resume", json={
        "request_id": uuid.uuid4().hex, "interrupt_id": first_id,
        "resume": {"decisions": [{"type": "approve"}]},
    })
    assert response.status_code == 400
    assert "STALE_INTERRUPT" in response.text
    assert actual_orders(erp)["total"] == before
    assert pending(actor)["interrupt_id"] != first_id


def test_public_goal_rejects_model_supplied_identity(actor):
    client, _, _, _, _, scenario = actor
    response = client.post(f"/api/planning/new-{uuid.uuid4().hex}/goal", json={
        "budget": "2500.00", "demands": [d.model_dump(mode="json") for d in scenario[2].demands],
        "owner_user_id": "demo-b",
    })
    assert response.status_code == 422


def test_read_without_scope_is_refused(scenario):
    orders, _, _, _, _, channel = scenario
    read = build_planning_tools(orders, channel)[0]
    reply = json.loads(read.invoke({}, config={}))
    assert reply["ok"] is False
    assert reply["error"]["code"] == "PLANNING_SCOPE_REQUIRED"


def test_read_one_part_omits_unaffected_materials(scenario):
    orders, thread, _, _, _, channel = scenario
    read = build_planning_tools(orders, channel)[0]
    reply = json.loads(read.invoke({"part_id": "P001"},
        config={"configurable": {"owner_user_id": OWNER, "thread_id": thread}}))
    assert reply["ok"] is True
    assert {d["part_id"] for d in reply["data"]["demands"]} == {"P001"}
    assert all(o["part_id"] == "P001" for o in reply["data"]["offers"])


def test_goal_http_archives_real_erp_source_and_is_owner_scoped(actor):
    client, _, _, _, _, scenario = actor
    _, _, problem, _, _, _ = scenario
    thread = "public-" + uuid.uuid4().hex
    response = client.post(f"/api/planning/{thread}/goal", json={
        "budget": "2500.00", "demands": [d.model_dump(mode="json") for d in problem.demands],
    })
    assert response.status_code == 200, response.text
    assert response.json()["data"]["goal_id"] == thread
    orders = scenario[0]
    row = orders.load(OWNER, thread, thread)
    assert len(row["sources"]) == 3
    assert all(s["body"]["data"]["part"]["part_id"] == s["part_id"] for s in row["sources"])
    read = build_planning_tools(orders, scenario[-1])[1]
    result = json.loads(read.invoke({"part_id": "P001"}, config={"configurable": {
        "owner_user_id": OWNER, "thread_id": thread}}))
    assert result["ok"] and result["data"][0]["body"]["request_id"]
    client.post("/api/demo/session", json={"user_id": "demo-b"})
    assert client.get(f"/api/planning/{thread}/goal").status_code == 404
    assert client.post(f"/api/planning/{thread}/goal", json={
        "budget": "1.00", "demands": [d.model_dump(mode="json") for d in problem.demands],
    }).status_code == 404


def test_public_check_reports_violations_without_private_best_plan(scenario):
    orders, thread, _, plan, _, channel = scenario
    check = build_planning_tools(orders, channel)[2]
    reply = json.loads(check.invoke({"plan": {**plan.model_dump(mode="json"), "revision": 99}},
        config={"configurable": {"owner_user_id": OWNER, "thread_id": thread}}))
    assert reply["ok"] is True
    assert reply["data"]["feasible"] is False
    assert any(v["code"] == "STALE_PLAN" for v in reply["data"]["violations"])
    assert not set(reply["data"]) & {"best", "optimal", "oracle"}
