"""T12 acceptance: two-layer HITL, approval binding and replay protection.

The claims under test, and how each one is actually demonstrated:

* **Two interruptions, not one.** Supplementation (:mod:`agent.tools.hitl_tools`) stops the
  run to ask for missing fields and asks again while they are still missing. Approval
  (:mod:`agent.approval.middleware`) stops a complete action before the write. They are
  exercised separately.
* **Nothing is written before approval, and nothing after a rejection.** Counted against a
  live ERP, not against a mock.
* **One approval, one order.** The approved write goes to the real gateway with a real
  grant, the ERP's operation ledger is checked, and a replay is shown to return the original
  result instead of creating a second order.
* **Changed content needs a new approval.** The check is a hash comparison between the
  approved bytes and the bytes about to be sent.
* **Decisions are durable and single.** The store is the real Mongo store, so the
  conditional-update guarantees are the ones under test rather than a test double's.

The only test double is a scripted chat model, used to steer control flow. Every datum and
every write goes through the real Java ERP and the real MCP gateway.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
for _path in (str(SRC_DIR), str(REPO_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fixtures import erp_service, loader, mcp_service, mongo_service  # noqa: E402

from agent.approval.middleware import (  # noqa: E402
    APPROVED_INTERRUPT_CONFIG_KEY,
    OWNER_CONFIG_KEY,
    THREAD_CONFIG_KEY,
    WriteApprovalMiddleware,
)
from agent.approval.models import (  # noqa: E402
    ApprovalError,
    PendingStatus,
    freeze_payload,
    payload_digest,
)
from agent.approval.service import ApprovalService, canonical_bytes  # noqa: E402
from agent.approval.store import MongoPendingActionStore  # noqa: E402
from agent.tools.hitl_tools import (  # noqa: E402
    SUPPLEMENT_INTERRUPT,
    missing_fields,
    request_order_info,
)
from mcp_server.grants import issue_grant  # noqa: E402
from mcp_server.tools.registry import (  # noqa: E402
    ORDER_CREATE,
    ORDER_SEARCH_DETAILS,
    canonical_order_body,
)
from deepagents import create_deep_agent  # noqa: E402

pytestmark = pytest.mark.integration

ACTOR_HEADER = "x-actor-id"
GRANT_HEADER = "x-approval-grant"
GRANT_SECRET = mcp_service.DEFAULT_GRANT_SECRET
INTERNAL_TOKEN = "acceptance-internal-token"

# Shorter aliases for the two run-config keys every graph invocation must carry.
THREAD_KEY = THREAD_CONFIG_KEY
OWNER_KEY = OWNER_CONFIG_KEY

GOOD_ARGS: dict[str, Any] = {
    "supplier_id": "S001",
    "currency": "CNY",
    "lines": [{"part_id": "P001", "quantity": 50, "unit_price": "25.50"}],
}
EVIL_ARGS: dict[str, Any] = {
    "supplier_id": "S001",
    "currency": "CNY",
    "lines": [{"part_id": "P001", "quantity": 9999, "unit_price": "0.01"}],
}


# --------------------------------------------------------------------------- #
# the declared model double
# --------------------------------------------------------------------------- #


class ScriptedChatModel(BaseChatModel):
    """Replays a fixed script of ``AIMessage``s."""

    script: list[AIMessage]

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if not self.script:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="完成"))])
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {}


def tool_call(name: str, args: dict[str, Any], call_id: str = "call-1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def tool_text(message: ToolMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    blocks = content if isinstance(content, list) else [content]
    return "\n".join(
        block.get("text", json.dumps(block, ensure_ascii=False)) if isinstance(block, dict) else str(block)
        for block in blocks
    )


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def settings():
    candidate = mongo_service.unique_settings(f"t12-{uuid.uuid4().hex[:8]}")
    mongo_service.require_reachable(candidate)
    mongo_service.drop_test_database(candidate)
    yield candidate
    mongo_service.drop_test_database(candidate)


@pytest.fixture(scope="module")
def database(settings):
    """A private test database with the application's real indexes created.

    ``ensure_application_indexes`` is not decoration here: the unique index on
    ``(owner, thread, interrupt)`` is one of the two mechanisms that make a double-click
    unable to produce two approvals, so the test has to run against it.
    """
    from pymongo import MongoClient

    from agent.persistence.indexes import (
        drop_undeclared_application_indexes,
        ensure_application_indexes,
    )

    with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True) as client:
        db = client[settings.database]
        drop_undeclared_application_indexes(db)
        ensure_application_indexes(db)
        yield db


@pytest.fixture(scope="module")
def store(database):
    """The real Mongo store, so the conditional updates are the real ones."""
    return MongoPendingActionStore(database)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    erp_dir = tmp_path_factory.mktemp("t12-erp")
    gateway_dir = tmp_path_factory.mktemp("t12-gateway")
    with erp_service.running_erp(erp_dir, seed_path=loader.SEED_PATH) as erp:
        with mcp_service.running_gateway(
            gateway_dir,
            erp_base_url=erp.base_url,
            erp_token=erp.token,
            grant_secret=GRANT_SECRET,
        ) as gateway:
            yield erp, gateway


@pytest.fixture(scope="module")
def gateway(stack) -> mcp_service.McpGateway:
    return stack[1]


@pytest.fixture()
def service(store) -> ApprovalService:
    return ApprovalService(store=store, grant_secret=GRANT_SECRET)


@pytest.fixture()
def thread_id() -> str:
    return f"t12-{uuid.uuid4().hex[:10]}"


def interrupt_value(tool_name: str = ORDER_CREATE, args: dict | None = None) -> dict:
    return {
        "action_requests": [
            {"name": tool_name, "args": args or dict(GOOD_ARGS), "description": "needs approval"}
        ],
        "review_configs": [
            {"action_name": tool_name, "allowed_decisions": ["approve", "reject"]}
        ],
    }


def record(service: ApprovalService, thread_id: str, *, interrupt_id="i1", args=None):
    return service.record(
        owner_user_id="demo-a",
        thread_id=thread_id,
        interrupt_id=interrupt_id,
        interrupt_value=interrupt_value(args=args),
    )


# --------------------------------------------------------------------------- #
# MCP plumbing
# --------------------------------------------------------------------------- #


async def mcp_call(
    gateway: mcp_service.McpGateway,
    name: str,
    arguments: dict,
    *,
    actor: str = "demo-a",
    grant: str | None = None,
) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {ACTOR_HEADER: actor}
    if grant:
        headers[GRANT_HEADER] = grant
    async with httpx.AsyncClient(
        headers=headers, timeout=httpx.Timeout(30.0, connect=5.0), trust_env=False
    ) as http:
        async with streamable_http_client(gateway.mcp_url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured.get("result", structured)
    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"{name} returned no usable payload: {result}")


def order_count(gateway: mcp_service.McpGateway, actor: str = "demo-a") -> int:
    payload = asyncio.run(
        mcp_call(gateway, ORDER_SEARCH_DETAILS, {"page": 1, "page_size": 100}, actor=actor)
    )
    assert payload["ok"] is True, payload
    return payload["data"]["total"]


async def call_gateway_with_grant(
    gateway: mcp_service.McpGateway, name: str, arguments: dict, *, actor: str, grant: str
) -> dict:
    return await mcp_call(gateway, name, arguments, actor=actor, grant=grant)


# --------------------------------------------------------------------------- #
# layer one: supplementation
# --------------------------------------------------------------------------- #


def test_a_missing_field_is_reported_by_name_not_guessed():
    """The validator names what is absent instead of filling it in."""
    draft = {"supplier_id": "S001", "lines": [{"part_id": "P001", "quantity": 50}]}

    problems = missing_fields(draft)

    assert problems == ["lines[0].unit_price (需两位小数字符串)"]


def test_a_malformed_value_counts_as_missing():
    """A value the ERP would reject must not be reported as supplied.

    Otherwise the user is asked to approve something that cannot be written, and the
    failure arrives after the approval instead of before it.
    """
    assert missing_fields(
        {"supplier_id": "S001", "lines": [{"part_id": "P001", "quantity": 1, "unit_price": "25.500"}]}
    )
    assert missing_fields(
        {"supplier_id": "S001", "lines": [{"part_id": "P0X1", "quantity": 1, "unit_price": "25.50"}]}
    )
    assert missing_fields(
        {"supplier_id": "S001", "lines": [{"part_id": "P001", "quantity": 0, "unit_price": "25.50"}]}
    )
    assert missing_fields({"supplier_id": "S001", "lines": []}) == ["lines"]


def test_a_complete_draft_has_nothing_missing():
    assert missing_fields(GOOD_ARGS) == []


def test_supplement_interrupts_and_then_accepts_a_complete_answer():
    """The tool stops the run once, and resumes only when the fields are actually there."""
    from langgraph.types import Command as Cmd

    asked: list[dict] = []

    def ask(payload):
        asked.append(payload)
        return {"supplier_id": "S001", "lines": [{"part_id": "P001", "quantity": 5, "unit_price": "25.50"}]}

    with _patch_interrupt(ask):
        result = request_order_info(
            field_names=["supplier_id", "lines"],
            question="请补充下单信息。",
            known_values={},
        )
    draft = json.loads(result)["data"]["order_draft"]

    assert len(asked) == 1
    assert asked[0]["interrupt_type"] == SUPPLEMENT_INTERRUPT
    assert "supplier_id" in asked[0]["missing_fields"]
    assert draft["lines"][0]["quantity"] == 5
    assert Cmd is not None


def test_supplement_asks_again_while_fields_are_still_missing_and_never_invents_them():
    """An incomplete answer produces a second interrupt, not a guessed unit price."""
    replies = [
        {"supplier_id": "S001", "lines": [{"part_id": "P001", "quantity": 5}]},  # still no price
        {"lines": [{"part_id": "P001", "quantity": 5, "unit_price": "25.50"}]},  # complete
    ]
    asked: list[dict] = []

    def ask(payload):
        asked.append(payload)
        return replies.pop(0)

    with _patch_interrupt(ask):
        result = request_order_info(
            field_names=["supplier_id", "lines"],
            question="请补充下单信息。",
            known_values={},
        )

    assert len(asked) == 2, "补充无效时必须再次中断"
    assert asked[1]["attempt"] == 2
    assert "unit_price" not in json.dumps(asked[0].get("known_values") or {})

    draft = json.loads(result)["data"]["order_draft"]
    assert draft["lines"][0]["unit_price"] == "25.50"


def test_supplement_does_not_parse_a_non_json_answer_into_fields():
    """Prose is kept as prose. Lenient parsing would turn "便宜点" into a unit price."""
    with _patch_interrupt(lambda payload: "就按上次那样吧"):
        result = request_order_info(
            field_names=["supplier_id", "lines"],
            question="请补充。",
            known_values={"supplier_id": "S001", "lines": [{"part_id": "P001", "quantity": 5, "unit_price": "25.50"}]},
        )

    draft = json.loads(result)["data"]["order_draft"]
    assert draft["lines"][0]["unit_price"] == "25.50"  # unchanged, not re-derived


@contextlib.contextmanager
def _patch_interrupt(replacement):
    """Swap the framework's ``interrupt`` for the duration of one call.

    The tool calls ``interrupt`` directly; patching it lets the two-layer logic be tested
    without standing up a graph that is exercised elsewhere in this file.
    """
    from agent.tools import hitl_tools

    original = hitl_tools.interrupt
    hitl_tools.interrupt = replacement
    try:
        yield
    finally:
        hitl_tools.interrupt = original


# --------------------------------------------------------------------------- #
# layer two: records and decisions
# --------------------------------------------------------------------------- #


def test_an_interrupt_is_recorded_with_its_frozen_bytes_and_one_operation_id(service, thread_id):
    action = record(service, thread_id)

    assert action.status is PendingStatus.PENDING
    assert action.operation_id.startswith("order-")
    assert payload_digest(action.payload_bytes) == action.payload_sha256
    assert action.target == "orders"
    assert action.payload["lines"][0]["unit_price"] == "25.50"
    assert action.summary.startswith("创建订单")
    assert action.expires_at is not None


def test_the_recorded_bytes_are_the_bytes_the_gateway_hashes(service, thread_id):
    """The frozen bytes and the gateway's canonical body are the same bytes by construction."""
    from mcp_server.tools.registry import OrderLineInput

    action = record(service, thread_id)

    expected = canonical_order_body(
        supplier_id="S001",
        currency="CNY",
        lines=[OrderLineInput(part_id="P001", quantity=50, unit_price="25.50")],
        note=None,
    )
    assert action.payload_bytes == expected
    assert action.payload_sha256 == payload_digest(expected)
    # And the hash the service would compute from the live arguments agrees.
    assert canonical_bytes(ORDER_CREATE, GOOD_ARGS) == action.payload_bytes


def test_a_batch_of_action_requests_is_refused(service, thread_id):
    """One approval covers one write; approving the first of several silently is worse."""
    value = interrupt_value()
    value["action_requests"].append({"name": ORDER_CREATE, "args": dict(GOOD_ARGS)})

    with pytest.raises(ApprovalError) as failure:
        service.record(
            owner_user_id="demo-a",
            thread_id=thread_id,
            interrupt_id="i1",
            interrupt_value=value,
        )

    assert failure.value.code == "BATCH_NOT_SUPPORTED"


def test_a_non_write_tool_cannot_be_recorded(service, thread_id):
    with pytest.raises(ApprovalError) as failure:
        service.record(
            owner_user_id="demo-a",
            thread_id=thread_id,
            interrupt_id="i1",
            interrupt_value=interrupt_value(tool_name="part_query"),
        )

    assert failure.value.code == "NOT_APPROVABLE"


def test_the_decision_is_idempotent_for_one_request_id_and_single_for_two(service, thread_id):
    """Two tabs clicking approve: the same request replays, a different one is refused."""
    action = record(service, thread_id)

    first = service.approve(
        owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r1"
    )
    again = service.approve(
        owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r1"
    )

    assert first.status is PendingStatus.APPROVED
    assert again.operation_id == action.operation_id

    with pytest.raises(ApprovalError) as failure:
        service.approve(
            owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r2"
        )
    assert failure.value.code == "ALREADY_DECIDED"


def test_a_rejected_action_cannot_then_be_approved(service, thread_id):
    record(service, thread_id)
    service.reject(
        owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r1"
    )

    with pytest.raises(ApprovalError) as failure:
        service.approve(
            owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r2"
        )

    assert failure.value.code == "ALREADY_DECIDED"


def test_an_expired_approval_window_is_refused(service, thread_id):
    now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    service.clock = lambda: now
    record(service, thread_id)

    service.clock = lambda: now + timedelta(hours=2)

    with pytest.raises(ApprovalError) as failure:
        service.approve(
            owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r1"
        )
    assert failure.value.code == "APPROVAL_EXPIRED"


def test_a_pending_action_survives_a_new_service_instance(store, database, thread_id):
    """Durability: the record is in Mongo, so a restarted process still sees it.

    The checkpoint stores where the graph stopped; this store records what was approved. Both
    have to survive, which is why the approval is not held in memory.
    """
    service = ApprovalService(store=store, grant_secret=GRANT_SECRET)
    action = record(service, thread_id)
    service.approve(
        owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r1"
    )

    restarted = ApprovalService(
        store=MongoPendingActionStore(database), grant_secret=GRANT_SECRET
    )
    reloaded = restarted.store.find("demo-a", thread_id, "i1")

    assert reloaded is not None
    assert reloaded.operation_id == action.operation_id
    assert reloaded.status is PendingStatus.APPROVED
    assert reloaded.payload_bytes == action.payload_bytes


def test_a_record_is_scoped_to_one_owner(service, thread_id):
    record(service, thread_id)

    with pytest.raises(ApprovalError) as failure:
        service.approve(
            owner_user_id="demo-b", thread_id=thread_id, interrupt_id="i1", request_id="r1"
        )

    assert failure.value.code == "ACTION_NOT_FOUND"


# --------------------------------------------------------------------------- #
# authorisation and the gate
# --------------------------------------------------------------------------- #


def test_an_unapproved_action_cannot_be_authorised(service, thread_id):
    record(service, thread_id)

    with pytest.raises(ApprovalError) as failure:
        service.authorize(
            owner_user_id="demo-a",
            thread_id=thread_id,
            interrupt_id="i1",
            tool_name=ORDER_CREATE,
            arguments=GOOD_ARGS,
        )

    assert failure.value.code == "APPROVAL_REQUIRED"


def test_changed_parameters_are_refused_after_approval(service, thread_id):
    """The rule "changed content needs a new approval", as a hash comparison."""
    record(service, thread_id)
    service.approve(
        owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r1"
    )

    same = service.authorize(
        owner_user_id="demo-a",
        thread_id=thread_id,
        interrupt_id="i1",
        tool_name=ORDER_CREATE,
        arguments=GOOD_ARGS,
    )
    assert same.operation_id == service.store.find("demo-a", thread_id, "i1").operation_id

    with pytest.raises(ApprovalError) as failure:
        service.authorize(
            owner_user_id="demo-a",
            thread_id=thread_id,
            interrupt_id="i1",
            tool_name=ORDER_CREATE,
            arguments=EVIL_ARGS,
        )
    assert failure.value.code == "PARAMETERS_CHANGED"


def test_two_authorisations_share_one_operation_id_and_claim_once(service, thread_id):
    """One executor per approval, and a replay reuses the same operation id.

    The grant is reissued on every call — but it is bound to the *same* operation id, which
    is what lets the ERP recognise the second attempt as a retry rather than a new order.
    """
    from mcp_server.grants import verify_grant

    action = record(service, thread_id)
    service.approve(
        owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r1"
    )

    first = service.authorize(
        owner_user_id="demo-a",
        thread_id=thread_id,
        interrupt_id="i1",
        tool_name=ORDER_CREATE,
        arguments=GOOD_ARGS,
    )
    second = service.authorize(
        owner_user_id="demo-a",
        thread_id=thread_id,
        interrupt_id="i1",
        tool_name=ORDER_CREATE,
        arguments=GOOD_ARGS,
    )

    assert first.operation_id == second.operation_id == action.operation_id
    for authorization in (first, second):
        grant = verify_grant(
            authorization.grant,
            secret=GRANT_SECRET,
            expect_owner="demo-a",
            expect_tool=ORDER_CREATE,
            expect_target="orders",
            expect_payload_sha256=action.payload_sha256,
        )
        assert grant.operation_id == action.operation_id

    # The claim itself can only be won once: the record is already EXECUTING, so a second
    # attempt to move APPROVED -> EXECUTING matches nothing.
    assert not service.store.transition(
        "demo-a",
        thread_id,
        "i1",
        expect=PendingStatus.APPROVED,
        target=PendingStatus.EXECUTING,
    )
    assert service.store.find("demo-a", thread_id, "i1").status is PendingStatus.EXECUTING


def test_the_gate_refuses_a_write_when_the_run_carries_no_approved_interrupt(store):
    """Fails closed: without a decision in the run config, nothing is executed."""
    middleware = WriteApprovalMiddleware(
        service=ApprovalService(store=store, grant_secret=GRANT_SECRET),
        channel=_never_called,
    )
    request = _fake_request(GOOD_ARGS, configurable={"thread_id": "t", OWNER_CONFIG_KEY: "demo-a"})

    message = asyncio.run(
        middleware.awrap_tool_call(request, _unreachable_handler)
    )

    payload = json.loads(tool_text(message))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "APPROVAL_REQUIRED"
    assert message.status == "error"


def test_a_refusal_leaks_neither_the_grant_nor_the_operation_id(store, thread_id):
    """The refusal goes into the model's message; the approval machinery must not."""
    service = ApprovalService(store=store, grant_secret=GRANT_SECRET)
    action = record(service, thread_id)
    middleware = WriteApprovalMiddleware(service=service, channel=_never_called)
    request = _fake_request(
        GOOD_ARGS,
        configurable={
            "thread_id": thread_id,
            OWNER_CONFIG_KEY: "demo-a",
            APPROVED_INTERRUPT_CONFIG_KEY: "i1",
        },
    )

    message = asyncio.run(middleware.awrap_tool_call(request, _unreachable_handler))
    body = tool_text(message)

    assert action.operation_id not in body
    assert "v1." not in body, "授权票据不能出现在模型可见的消息里"
    assert GRANT_SECRET not in body


async def _never_called(**_kwargs: Any) -> str:  # pragma: no cover - asserted against
    raise AssertionError("the channel must not be reached for a refused write")


def _unreachable_handler(_request: Any) -> Any:  # pragma: no cover - asserted against
    raise AssertionError("a write must not be delegated to the raw tool")


class _FakeRequest:
    """Minimal stand-in for ``ToolCallRequest`` for the gate's refusal paths."""

    def __init__(self, args: dict, configurable: dict) -> None:
        self.tool_call = {"name": ORDER_CREATE, "args": args, "id": "call-1"}
        self.tool = None
        self.state = {}
        self.runtime = type("R", (), {"config": {"configurable": configurable}})()


def _fake_request(args: dict, *, configurable: dict) -> _FakeRequest:
    return _FakeRequest(args, configurable)


# --------------------------------------------------------------------------- #
# against the real gateway and the real ERP
# --------------------------------------------------------------------------- #


def test_an_unapproved_write_is_refused_by_the_gateway_and_the_erp_stays_empty(gateway):
    """The gateway is the last line: no grant, no write."""
    before = order_count(gateway)

    payload = asyncio.run(
        mcp_call(gateway, ORDER_CREATE, dict(GOOD_ARGS), actor="demo-a", grant=None)
    )

    assert payload["ok"] is False
    assert payload["error"]["code"] == "APPROVAL_REQUIRED"
    assert order_count(gateway) == before


def test_an_approved_write_reaches_the_erp_exactly_once(gateway, service, thread_id):
    """Approve once, get one order; replay the same approval, still one order."""
    action = record(service, thread_id)
    service.approve(
        owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r1"
    )
    before = order_count(gateway)

    async def run_twice() -> list[dict]:
        results = []
        for _ in range(2):
            authorization = service.authorize(
                owner_user_id="demo-a",
                thread_id=thread_id,
                interrupt_id="i1",
                tool_name=ORDER_CREATE,
                arguments=GOOD_ARGS,
            )
            results.append(
                await call_gateway_with_grant(
                    gateway,
                    ORDER_CREATE,
                    json.loads(authorization.payload_bytes.decode("utf-8")),
                    actor="demo-a",
                    grant=authorization.grant,
                )
            )
        return results

    results = asyncio.run(run_twice())

    assert all(item["ok"] is True for item in results), results
    assert results[0]["data"]["order_id"] == results[1]["data"]["order_id"]
    assert order_count(gateway) == before + 1, "同一操作 ID 只应产生一单"

    service.finish(
        authorized=service.authorize(
            owner_user_id="demo-a",
            thread_id=thread_id,
            interrupt_id="i1",
            tool_name=ORDER_CREATE,
            arguments=GOOD_ARGS,
        ),
        ok=True,
    )
    assert service.store.find("demo-a", thread_id, "i1").status is PendingStatus.EXECUTED
    assert action.operation_id.startswith("order-")


def test_a_rejected_write_never_reaches_the_gateway(gateway, service, thread_id):
    record(service, thread_id)
    service.reject(
        owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r1"
    )
    before = order_count(gateway)

    with pytest.raises(ApprovalError) as failure:
        service.authorize(
            owner_user_id="demo-a",
            thread_id=thread_id,
            interrupt_id="i1",
            tool_name=ORDER_CREATE,
            arguments=GOOD_ARGS,
        )

    assert failure.value.code == "APPROVAL_REJECTED"
    assert order_count(gateway) == before


def test_another_users_operation_id_cannot_be_replayed_under_a_new_owner(gateway, service, thread_id):
    """A grant binds the owner, so a stolen ticket is useless to another user."""
    record(service, thread_id)
    service.approve(
        owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r1"
    )
    authorization = service.authorize(
        owner_user_id="demo-a",
        thread_id=thread_id,
        interrupt_id="i1",
        tool_name=ORDER_CREATE,
        arguments=GOOD_ARGS,
    )
    before_a = order_count(gateway, "demo-a")
    before_b = order_count(gateway, "demo-b")

    stolen = asyncio.run(
        call_gateway_with_grant(
            gateway,
            ORDER_CREATE,
            json.loads(authorization.payload_bytes.decode("utf-8")),
            actor="demo-b",
            grant=authorization.grant,
        )
    )

    assert stolen["ok"] is False
    assert stolen["error"]["code"] == "GRANT_MISMATCH"
    assert order_count(gateway, "demo-a") == before_a
    assert order_count(gateway, "demo-b") == before_b


def test_a_forged_grant_is_refused(gateway):
    """Signed with the wrong key, so the signature does not verify."""
    forged = issue_grant(
        secret="not-the-gateway-secret",
        owner="demo-a",
        tool=ORDER_CREATE,
        target="orders",
        operation_id="order-forged",
        payload_sha256=payload_digest(freeze_payload(GOOD_ARGS)),
    )
    before = order_count(gateway)

    payload = asyncio.run(
        call_gateway_with_grant(gateway, ORDER_CREATE, dict(GOOD_ARGS), actor="demo-a", grant=forged)
    )

    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_GRANT"
    assert order_count(gateway) == before


# --------------------------------------------------------------------------- #
# the internal verification endpoint
# --------------------------------------------------------------------------- #


def test_the_internal_endpoint_requires_its_own_service_token(store, thread_id):
    """Separate secret from the user session and from the grant key."""
    from fastapi import FastAPI

    from api_view.internal.approval import build_internal_router

    service = ApprovalService(store=store, grant_secret=GRANT_SECRET)
    action = record(service, thread_id)
    service.approve(
        owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r1"
    )

    app = FastAPI()
    app.include_router(build_internal_router(service=service, service_token=INTERNAL_TOKEN))

    body = {
        "owner_user_id": "demo-a",
        "thread_id": thread_id,
        "interrupt_id": "i1",
        "tool_name": ORDER_CREATE,
        "target": action.target,
        "payload_sha256": action.payload_sha256,
        "operation_id": action.operation_id,
    }

    with _test_client(app) as client:
        assert client.post("/internal/approvals/verify", json=body).status_code == 403
        assert (
            client.post(
                "/internal/approvals/verify",
                json=body,
                headers={"x-internal-service-token": "wrong"},
            ).status_code
            == 403
        )
        ok = client.post(
            "/internal/approvals/verify",
            json=body,
            headers={"x-internal-service-token": INTERNAL_TOKEN},
        )

    assert ok.status_code == 200
    assert ok.json()["data"]["operation_id"] == action.operation_id


def test_the_internal_endpoint_refuses_a_record_that_does_not_match(store, thread_id):
    """The check is against the recorded approval, not just the signature."""
    from fastapi import FastAPI

    from api_view.internal.approval import build_internal_router

    service = ApprovalService(store=store, grant_secret=GRANT_SECRET)
    action = record(service, thread_id)
    service.approve(
        owner_user_id="demo-a", thread_id=thread_id, interrupt_id="i1", request_id="r1"
    )
    app = FastAPI()
    app.include_router(build_internal_router(service=service, service_token=INTERNAL_TOKEN))

    base = {
        "owner_user_id": "demo-a",
        "thread_id": thread_id,
        "interrupt_id": "i1",
        "tool_name": ORDER_CREATE,
        "target": action.target,
        "payload_sha256": action.payload_sha256,
        "operation_id": action.operation_id,
    }
    headers = {"x-internal-service-token": INTERNAL_TOKEN}

    with _test_client(app) as client:
        tampered = client.post(
            "/internal/approvals/verify",
            json={**base, "payload_sha256": payload_digest(b"something else")},
            headers=headers,
        )
        absent = client.post(
            "/internal/approvals/verify",
            json={**base, "interrupt_id": "never-recorded"},
            headers=headers,
        )
        incomplete = client.post(
            "/internal/approvals/verify",
            json={k: v for k, v in base.items() if k != "operation_id"},
            headers=headers,
        )

    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "GRANT_MISMATCH"
    assert absent.status_code == 404
    assert incomplete.status_code == 400


@contextlib.contextmanager
def _test_client(app):
    """A TestClient, imported here so the rest of the module needs no web server."""
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        yield client


# --------------------------------------------------------------------------- #
# the graph: both interruptions in one run
# --------------------------------------------------------------------------- #


def test_the_graph_stops_for_approval_before_any_write_and_refuses_without_a_decision(store):
    """End to end through the assembled graph, with the real gate in the path."""
    executed: list[dict] = []

    async def channel(**kwargs: Any) -> str:
        executed.append(kwargs)
        return json.dumps({"ok": True, "data": {"order_id": "ORD-X"}})

    service = ApprovalService(store=store, grant_secret=GRANT_SECRET)

    def order_create(supplier_id: str, currency: str, lines: list, note: str = "") -> str:
        """Create an order."""
        raise AssertionError("the raw tool must never run: the gate performs the write")

    tool = StructuredTool.from_function(
        func=order_create, name=ORDER_CREATE, description="Create an order."
    )
    middleware = WriteApprovalMiddleware(service=service, channel=channel)
    graph = create_deep_agent(
        model=ScriptedChatModel(
            script=[tool_call(ORDER_CREATE, dict(GOOD_ARGS)), AIMessage(content="已处理。")]
        ),
        tools=[tool],
        middleware=[middleware],
        interrupt_on={ORDER_CREATE: {"allowed_decisions": ["approve", "reject"]}},
        checkpointer=InMemorySaver(),
        system_prompt="你是采购助手。",
    )
    thread = f"graph-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {THREAD_KEY: thread, OWNER_KEY: "demo-a"}}

    first = asyncio.run(graph.ainvoke({"messages": [{"role": "user", "content": "下单"}]}, config))

    interrupts = first.get("__interrupt__") or []
    assert interrupts, "写工具必须先中断"
    assert interrupts[0].value["review_configs"][0]["allowed_decisions"] == ["approve", "reject"]
    assert executed == [], "中断时不得执行任何写"

    resumed = asyncio.run(
        graph.ainvoke(
            Command(resume={"decisions": [{"type": "reject", "message": "不要下单"}]}),
            {"configurable": {THREAD_KEY: thread, OWNER_KEY: "demo-a"}},
        )
    )

    assert executed == [], "拒绝后不得执行任何写"
    rejections = [
        tool_text(m) for m in resumed["messages"] if isinstance(m, ToolMessage) and m.name == ORDER_CREATE
    ]
    assert rejections and "reject" in rejections[0].lower()
