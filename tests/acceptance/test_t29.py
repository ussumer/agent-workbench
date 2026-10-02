"""Real HTTP runs and delegated MCP/ERP calls must drive real owner-scoped memory."""

import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acceptance.test_t11 import ScriptedChatModel, _stub_tools, tool_call  # noqa: E402
from acceptance.test_t13 import build_client, sse_events  # noqa: E402
from agent.approval.service import ApprovalService  # noqa: E402
from agent.approval.store import MongoPendingActionStore  # noqa: E402
from agent.main_agent import build_main_agent, build_virtual_backend  # noqa: E402
from agent.memory.preferences import (  # noqa: E402
    HISTORY_KEY,
    PREFERENCES_KEY,
    load_preferences,
    save_automatic_history,
)
from agent.middlewares.context_injection import ContextInjectionMiddleware  # noqa: E402
from agent.middlewares.skills_sync import SkillsSyncMiddleware  # noqa: E402
from agent.persistence.scoped_store import UserScopedStore  # noqa: E402
from agent.subagents.loader import load_all  # noqa: E402
from agent.tools import with_local_tools  # noqa: E402
from fixtures import erp_service, loader, mcp_service, mongo_service, sandbox_service  # noqa: E402
from live.stack import _gateway_tools  # noqa: E402
from mcp_server.tools.registry import ERP_TOOLS, ERP_WRITE_TOOLS  # noqa: E402


@pytest.fixture(scope="module")
def services(tmp_path_factory):
    directory = tmp_path_factory.mktemp("memory-runtime")
    with erp_service.running_erp(directory / "erp", seed_path=loader.SEED_PATH) as erp:
        with mcp_service.running_gateway(directory / "gateway", erp_base_url=erp.base_url,
                                         erp_token=erp.token) as gateway:
            settings = mongo_service.unique_settings(f"t29-{uuid.uuid4().hex[:12]}")
            resources = mongo_service.start_resources(settings)
            try:
                with sandbox_service.running_pool(settings, warm_pool_size=0) as (manager, _):
                    yield SimpleNamespace(resources=resources, manager=manager, gateway=gateway)
            finally:
                resources.close()
                mongo_service.drop_test_database(settings)


@pytest.fixture()
def runtime(services):
    resources = services.resources
    configs = load_all(available_tools=with_local_tools(ERP_TOOLS), write_tools=ERP_WRITE_TOOLS)
    models = {}
    analysts = {}
    graphs = {}

    def scoped(owner):
        return UserScopedStore(resources.store, owner)

    for owner in ("demo-a", "demo-b"):
        for key in (PREFERENCES_KEY, HISTORY_KEY, "preferences.md"):
            scoped(owner).delete(("memories", owner), key)
        backend = services.manager.get_or_create(owner)
        virtual = build_virtual_backend(sandbox_backend=backend, store=scoped(owner), owner_user_id=owner)
        tools = asyncio.run(_gateway_tools(services.gateway.mcp_url, owner))
        names = {tool.name for tool in tools}
        tools.extend(tool for tool in _stub_tools() if tool.name not in names)
        models[owner] = ScriptedChatModel(script=[])
        analysts[owner] = ScriptedChatModel(script=[])
        graphs[owner] = build_main_agent(
            model=models[owner], tools=tools, write_tools=ERP_WRITE_TOOLS, backend=virtual,
            owner_user_id=owner, subagents=configs, checkpointer=resources.checkpointer,
            store=scoped(owner), subagent_models={"procurement-analyst": analysts[owner]},
            middleware=[ContextInjectionMiddleware(store_provider=scoped),
                        SkillsSyncMiddleware(backend_provider=lambda backend=backend: backend)],
        ).graph
    approvals = ApprovalService(store=MongoPendingActionStore(resources.database), grant_secret="t29-test-secret")
    client = build_client(resources, approvals, graphs["demo-a"])
    client.app.state.context.graph_provider = graphs.__getitem__
    with client:
        yield SimpleNamespace(client=client, models=models, analysts=analysts, scoped=scoped)


def send(runtime, message, *, owner="demo-a"):
    runtime.client.headers["X-Demo-User"] = owner
    status, events = sse_events(runtime.client, request_id=uuid.uuid4().hex,
                                thread_id=uuid.uuid4().hex, message=message)
    assert status == 200
    done = [event["payload"] for name, event in events if name == "done"]
    assert len(done) == 1
    return done[0], events


def query(runtime, *, failed=False, owner="demo-a"):
    runtime.models[owner].script.extend([
        tool_call("task", {"description": "查询 ERP", "subagent_type": "procurement-analyst"}, uuid.uuid4().hex),
        AIMessage(content="done"),
    ])
    runtime.analysts[owner].script.extend([
        tool_call("part_query" if failed else "supplier_query",
                  {"part_id": "P999"} if failed else {"q": "S001"}, uuid.uuid4().hex),
        AIMessage(content="result checked"),
    ])
    return send(runtime, "查询无效物料" if failed else "查询 S001 供应商", owner=owner)


def model_prompt(model):
    return "\n".join(str(message.content) for message in model.contexts[-1] if message.type == "system")


def test_explicit_preferences_are_saved_and_seen_in_the_next_request(runtime):
    done, _ = send(runtime, "以后都用表格，并用折线图")
    assert done["status"] == "completed"
    preferences = load_preferences(runtime.scoped("demo-a"))
    assert preferences.values["output_format"] == "table"
    assert preferences.values["chart_type"] == "line"
    send(runtime, "你好")
    assert "output_format: table" in model_prompt(runtime.models["demo-a"])
    assert "chart_type: line" in model_prompt(runtime.models["demo-a"])


def test_real_delegated_erp_query_updates_history_and_subagent_preferences(runtime):
    send(runtime, "以后都用表格，并用折线图")
    done, _ = query(runtime)
    assert done["status"] == "completed"
    preferences = load_preferences(runtime.scoped("demo-a"))
    assert preferences.history["recent_supplier_ids"] == ["S001"]
    assert preferences.history["recent_queries"] == ["查询 S001 供应商"]
    assert preferences.values["output_format"] == "table"
    assert "output_format: table" in model_prompt(runtime.analysts["demo-a"])
    assert "chart_type: line" in model_prompt(runtime.analysts["demo-a"])


def test_unsuccessful_erp_result_does_not_create_successful_history(runtime):
    query(runtime, failed=True)
    preferences = load_preferences(runtime.scoped("demo-a"))
    assert preferences.history["recent_supplier_ids"] == []
    assert preferences.history["recent_queries"] == []


def test_chitchat_does_not_update_procurement_history(runtime):
    done, _ = send(runtime, "你好")
    assert done["status"] == "completed"
    assert load_preferences(runtime.scoped("demo-a")).history == {"recent_supplier_ids": [], "recent_queries": []}


def test_other_owner_does_not_receive_preferences_or_history(runtime):
    send(runtime, "以后都用表格")
    query(runtime)
    send(runtime, "你好", owner="demo-b")
    preferences = load_preferences(runtime.scoped("demo-b"))
    assert preferences.values["output_format"] == "markdown"
    assert preferences.history == {"recent_supplier_ids": [], "recent_queries": []}
    assert "output_format: table" not in model_prompt(runtime.models["demo-b"])


def test_stale_automatic_history_write_cannot_overwrite_new_explicit_preference(runtime):
    scoped = runtime.scoped("demo-a")
    stale = load_preferences(scoped)
    send(runtime, "以后都用表格")
    stale.record_query("interleaved successful query")
    save_automatic_history(scoped, stale)
    current = load_preferences(scoped)
    assert current.values["output_format"] == "table"
    assert current.history["recent_queries"] == ["interleaved successful query"]


def test_memory_write_failure_is_visible_after_real_procurement_result(runtime, monkeypatch):
    original = UserScopedStore.put

    def fail_history(self, namespace, key, value, *args, **kwargs):
        if key == HISTORY_KEY:
            raise RuntimeError("injected memory write failure")
        return original(self, namespace, key, value, *args, **kwargs)

    monkeypatch.setattr(UserScopedStore, "put", fail_history)
    done, events = query(runtime)
    assert done["status"] == "failed"
    assert any(event["payload"]["code"] == "MEMORY_PERSISTENCE_FAILED"
               for name, event in events if name == "error")
