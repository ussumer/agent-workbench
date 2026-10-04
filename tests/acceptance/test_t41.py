"""TRACE wiring with declared scripted models; real Mongo, graph, Java and MCP.

Fixture skill text is synthetic test data, never installed as learned Demo knowledge.
"""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import test_t40 as wiring
from deepagents.backends import StateBackend
from langchain_core.messages import AIMessage
from test_t12 import ScriptedChatModel, tool_call
from test_t39 import OWNER, actual_orders

from agent.evolution.episodes import EpisodeStore, EvidenceError, TextSkill
from agent.evolution.orchestration import PlanningTraceMiddleware
from agent.middlewares.tools_summarization import BudgetConfig
from agent.planning.actor import build_planning_actor

db = wiring.db
stack = wiring.stack
scenario = wiring.scenario
resources = wiring.resources
actor = wiring.actor
pytestmark = pytest.mark.integration
IDENTITY = {"provenance": "scripted-component", "model_type": "ScriptedChatModel"}


class ObservedModel(wiring.InspectingModel):
    seen: list[list] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append([m.model_dump(mode="json") for m in messages])
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture
def traced(actor):
    _, context, graphs, _, kernel, scenario = actor
    orders, _, _, plan, _, channel = scenario
    store = EpisodeStore(context.resources.database, secrets={"test-private-secret"})
    store.set_current(OWNER, None)
    model = ObservedModel(script=[tool_call("planning_submit", {"plan": plan.model_dump(mode="json")})])
    selector = ScriptedChatModel(script=[])
    graphs[OWNER] = build_planning_actor(orders=orders, kernel=kernel, channel=channel,
        backend=StateBackend(), checkpointer=context.resources.checkpointer, store=context.resources.store,
        model=model, selector_model=selector, episode_store=store)
    context.extra.update(planning_episode_store=store, planning_model_identity=IDENTITY)
    yield actor, store, model, selector
    store.set_current(OWNER, None)


def exported(traced):
    actor, store, _, _ = traced
    thread = actor[-1][1]
    row = store.episodes.find_one({"owner_user_id": OWNER, "thread_id": thread})
    assert row is not None
    return store.export(OWNER, row["_id"])


def skills(store, *, owner=OWNER):
    bank = store.freeze(owner, [TextSkill(skill_id="a", description="test condition A", body="BODY_A"),
                                TextSkill(skill_id="b", description="test condition B", body="BODY_B")],
                        provenance="synthetic-component-fixture")
    store.set_current(owner, bank)
    return bank


def test_formal_three_runs_two_actual_orders_one_unscored_episode(traced):
    actor, _, _, _ = traced
    before = actual_orders(actor[-1][4])["total"]
    assert wiring.start(actor)[-1]["payload"]["status"] == "interrupted"
    assert wiring.resume(actor)[-1]["payload"]["status"] == "interrupted"
    assert wiring.resume(actor)[-1]["payload"]["status"] == "completed"
    assert actual_orders(actor[-1][4])["total"] == before + 2
    result = exported(traced)
    row = result["episode"]
    assert len(row["run_ids"]) == 3
    assert row["model"] == IDENTITY and row["status"] == "open" and row["feedback"] is None
    settlements = [e["payload"] for e in result["events"] if e["kind"] == "run_settled"]
    assert [e["api_status"] for e in settlements] == ["interrupted", "interrupted", "completed"]
    assert all(e["procurement_success"] is None for e in settlements)
    assert len(settlements[-1]["business_state"]["orders"]) == 2
    assert len(settlements[-1]["approvals"]) == 2
    assert all(a["status"] == "executed" for a in settlements[-1]["approvals"])
    starts = [e["payload"] for e in result["events"] if e["kind"] == "run_started"]
    assert starts[0]["interrupt_id"] is None
    assert starts[1]["interrupt_id"] != starts[2]["interrupt_id"]


def test_bank_promotion_and_withdrawal_do_not_change_waiting_goal(traced):
    actor, store, _, selector = traced
    first = skills(store)
    selector.script = [AIMessage(content='["a"]'), AIMessage(content='["a"]')]
    wiring.start(actor)
    second = store.freeze(OWNER, [TextSkill(skill_id="c", description="next version", body="BODY_C")],
                          provenance="synthetic-component-fixture")
    store.set_current(OWNER, second)
    wiring.resume(actor)
    store.set_current(OWNER, None)
    wiring.resume(actor)
    result = exported(traced)
    assert result["episode"]["bank_id"] == first
    assert all(e["payload"]["bank_id"] == first for e in result["events"] if e["kind"] == "turn_started")
    new = store.bind(OWNER, "new-" + uuid.uuid4().hex, "other", model=IDENTITY)
    assert new["bank_id"] != first and store.bank(OWNER, new["bank_id"])["skills"] == []


def test_rebuild_service_and_graph_resumes_same_bank_and_episode(traced):
    actor, store, model, selector = traced
    wiring.start(actor)
    first = exported(traced)["episode"]
    _, context, graphs, _, kernel, scenario = actor
    replacement = EpisodeStore(context.resources.database)
    context.extra["planning_episode_store"] = replacement
    graphs[OWNER] = build_planning_actor(orders=scenario[0], kernel=kernel, channel=scenario[-1],
        backend=StateBackend(), checkpointer=context.resources.checkpointer, store=context.resources.store,
        model=model, selector_model=selector, episode_store=replacement)
    wiring.resume(actor)
    wiring.resume(actor)
    result = store.export(OWNER, first["_id"])
    assert result["episode"]["bank_id"] == first["bank_id"]
    assert len(result["episode"]["run_ids"]) == 3


def test_every_actor_model_turn_reselects_order_and_drops_old_bodies(traced):
    actor, store, model, selector = traced
    skills(store)
    model.script = [tool_call("planning_read", {}, "r1"),
                    tool_call("planning_check", {"plan": actor[-1][3].model_dump(mode="json")}, "r2"),
                    AIMessage(content="component completed")]
    selector.script = [AIMessage(content='["b","a"]'), AIMessage(content='["a"]'), AIMessage(content="[]")]
    assert wiring.start(actor)[-1]["payload"]["status"] == "completed"
    prompts = [str(turn[0]["content"]) for turn in model.seen]
    assert len(prompts) == 3
    assert prompts[0].index("BODY_B") < prompts[0].index("BODY_A")
    assert "BODY_B" not in prompts[1] and "BODY_A" in prompts[1]
    assert "BODY_A" not in prompts[2] and "BODY_B" not in prompts[2]
    events = exported(traced)["events"]
    grounded = [e["payload"] for e in events if e["kind"] == "turn_grounded"]
    assert [g["selection"] for g in grounded] == [["b", "a"], ["a"], []]
    assert all(len(g["read_bodies"]) == len(g["selection"]) and g["adoption"] == "unassessed" for g in grounded)
    assert len({g["turn_id"] for g in grounded}) == 3
    assert all(len(e["payload"]["catalog"]) == 2 for e in events if e["kind"] == "turn_started")
    selector_calls = [e for e in events if e["kind"] == "model_invocation" and e["payload"]["role"] == "skill_selector"]
    assert len(selector_calls) == 3
    usage = [e["payload"]["budget"] for e in events if e["kind"] == "run_settled"][0]
    assert usage["counts"]["model"] == 6


@pytest.mark.parametrize("selection", ['["unknown"]', '["a","a"]', '{"id":"a"}', 'oops'])
def test_invalid_selection_fails_before_actor_or_write(traced, selection):
    actor, store, model, selector = traced
    skills(store)
    selector.script = [AIMessage(content=selection)]
    before = actual_orders(actor[-1][4])["total"]
    assert wiring.start(actor)[-1]["payload"]["status"] == "failed"
    assert model.seen == [] and actual_orders(actor[-1][4])["total"] == before
    assert not [e for e in exported(traced)["events"] if e["kind"] == "turn_action"]


def test_selector_consumes_shared_budget_before_actor(traced):
    actor, store, model, selector = traced
    skills(store)
    actor[1].budget_config = BudgetConfig(model_calls_per_run=1)
    selector.script = [AIMessage(content='["a"]')]
    assert wiring.start(actor)[-1]["payload"]["status"] == "failed"
    assert model.seen == []
    settled = [e["payload"] for e in exported(traced)["events"] if e["kind"] == "run_settled"][-1]
    assert settled["budget"]["counts"]["model"] == 1
    assert settled["error_code"] == "MODEL_RUN_BUDGET_EXCEEDED"
    assert settled["procurement_success"] is None


def test_corrupt_bank_blocks_resume_and_no_order_write(traced):
    actor, store, _, selector = traced
    bank = skills(store)
    selector.script = [AIMessage(content='["a"]')]
    wiring.start(actor)
    before = actual_orders(actor[-1][4])["total"]
    store.banks.update_one({"_id": bank}, {"$set": {"skills.0.body": "corrupt"}})
    assert wiring.resume(actor)[-1]["payload"]["status"] == "failed"
    assert actual_orders(actor[-1][4])["total"] == before
    with pytest.raises(EvidenceError):
        exported(traced)


def test_mongo_cross_owner_scope_and_thread_binding_rejected(db):
    store = EpisodeStore(db)
    bank = skills(store)
    with pytest.raises(EvidenceError):
        store.bank("demo-b", bank)
    with pytest.raises(EvidenceError):
        store.freeze(OWNER, [TextSkill(skill_id="x", description="wrong", body="wrong", scope="course")], provenance="test")
    thread = uuid.uuid4().hex
    episode = store.begin_run(OWNER, thread, thread, "r", model=IDENTITY, interrupt_id=None, public={})
    with pytest.raises(EvidenceError):
        store.export("demo-b", episode)
    middleware = PlanningTraceMiddleware(store, ScriptedChatModel(script=[]))
    with pytest.raises(EvidenceError):
        middleware.binding({"configurable": {"owner_user_id": OWNER, "thread_id": "other",
            "application_run_id": "r", "planning_episode_id": episode}})


def test_concurrent_binding_is_one_episode_and_fixed_model(db):
    store = EpisodeStore(db)
    skills(store)
    thread = uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: store.bind(OWNER, thread, thread, model=IDENTITY), range(8)))
    assert len({r["_id"] for r in rows}) == 1
    other = store.bind(OWNER, uuid.uuid4().hex, thread, model=IDENTITY)
    assert other["_id"] != rows[0]["_id"]
    with pytest.raises(EvidenceError):
        store.bind(OWNER, thread, thread, model={"provenance": "configured-live"})


def test_large_raw_payload_chunk_roundtrip_hash_and_secret_redaction(db):
    store = EpisodeStore(db, secrets={"private-value"})
    thread = uuid.uuid4().hex
    episode = store.begin_run(OWNER, thread, thread, "r", model=IDENTITY, interrupt_id=None, public={})
    text = "真实原文" * 150000 + "private-value"
    event_id = store.append(OWNER, episode, "r", "tool_observed", {"raw": text})
    result = EpisodeStore(db).export(OWNER, episode)
    event = result["events"][-1]
    assert event["chunks"] > 1 and event["redacted"] is True
    assert event["payload"]["raw"].startswith("真实原文" * 150000)
    assert "private-value" not in json.dumps(event)
    store.events.update_one({"_id": event_id + ":0"}, {"$set": {"content": "corrupted"}})
    with pytest.raises(EvidenceError):
        store.export(OWNER, episode)


def test_incomplete_event_reservation_refuses_export(db):
    store = EpisodeStore(db)
    thread = uuid.uuid4().hex
    episode = store.begin_run(OWNER, thread, thread, "r", model=IDENTITY, interrupt_id=None, public={})
    store.episodes.update_one({"_id": episode}, {"$inc": {"event_seq": 1}})
    with pytest.raises(EvidenceError, match="commits"):
        store.export(OWNER, episode)


def test_evidence_failure_before_actor_is_visible_without_write(traced, monkeypatch):
    actor, store, model, _ = traced
    append = store.append

    def failing(owner, episode, run, kind, payload):
        if kind == "turn_grounded":
            raise EvidenceError("test storage unavailable")
        return append(owner, episode, run, kind, payload)

    monkeypatch.setattr(store, "append", failing)
    before = actual_orders(actor[-1][4])["total"]
    events = wiring.start(actor)
    assert events[-1]["payload"]["status"] == "failed"
    assert model.seen == [] and actual_orders(actor[-1][4])["total"] == before
    assert any(e["payload"].get("code") == "EPISODE_EVIDENCE_FAILED" for e in events)


def test_rejection_and_environment_failure_remain_unscored(traced):
    actor, _, _, _ = traced
    wiring.start(actor)
    wiring.resume(actor, "reject")
    result = exported(traced)
    assert result["episode"]["feedback"] is None
    last = [e["payload"] for e in result["events"] if e["kind"] == "run_settled"][-1]
    assert last["api_status"] == "completed" and last["procurement_success"] is None
    assert last["business_state"]["orders"] == []


def test_sdk_offload_keeps_full_raw_tool_observation(traced):
    actor, _, model, _ = traced
    orders, thread = actor[-1][:2]
    raw = "original-test-observation-" * 12000 + "END_OF_RAW_OBSERVATION"
    orders.goals.update_one({"owner_user_id": OWNER, "thread_id": thread}, {"$set": {
        "sources": [{"part_id": "P001", "payload": raw, "provenance": "synthetic-component-fixture"}]}})
    model.script = [tool_call("planning_source", {"part_id": "P001"}, "large-source"),
                    AIMessage(content="component source checked")]
    assert wiring.start(actor)[-1]["payload"]["status"] == "completed"
    events = exported(traced)["events"]
    observation = next(e["payload"]["raw_result"] for e in events if e["kind"] == "tool_observed")
    assert json.loads(observation["content"])["data"][0]["payload"] == raw
    visible = [m["content"] for m in model.seen[-1] if m["type"] == "tool"]
    assert len(visible) == 1
    assert len(str(visible[0])) < len(raw)
    assert "/large_tool_results/" in str(visible[0])


def test_tool_observation_failure_stops_next_model_even_if_toolnode_catches_it(traced, monkeypatch):
    actor, store, model, _ = traced
    model.script = [tool_call("planning_read", {}), AIMessage(content="must never execute")]
    append = store.append

    def fail_observation(owner, episode, run, kind, payload):
        if kind == "tool_observed":
            raise EvidenceError("test failed after tool returned")
        return append(owner, episode, run, kind, payload)

    monkeypatch.setattr(store, "append", fail_observation)
    assert wiring.start(actor)[-1]["payload"]["status"] == "failed"
    assert len(model.seen) == 1 and len(model.script) == 1


def test_json_escaped_secrets_and_model_credentials_are_not_persisted(db):
    secret = 'private"credential\nsecond-line'
    store = EpisodeStore(db, secrets={secret})
    thread = uuid.uuid4().hex
    episode = store.begin_run(OWNER, thread, thread, "r", model=IDENTITY, interrupt_id=None, public={})
    store.append(OWNER, episode, "r", "tool_observed", {"content": json.dumps({"value": secret})})
    payload = store.export(OWNER, episode)["events"][-1]["payload"]
    assert secret not in json.loads(payload["content"])["value"]
    with pytest.raises(EvidenceError):
        store.freeze(OWNER, [TextSkill(skill_id="bad", description="test", body=secret)], provenance="test")
    with pytest.raises(EvidenceError):
        store.bind(OWNER, uuid.uuid4().hex, "bad-model", model={**IDENTITY, "api_key": "not-recorded"})
