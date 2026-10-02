"""Actual SDK graphs discover skills from real sandbox files, including a later publish."""

import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.store.mongodb import MongoDBStore
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acceptance.test_t11 import ScriptedChatModel, _stub_tools, tool_call  # noqa: E402
from agent.main_agent import build_main_agent  # noqa: E402
from agent.middlewares.skill_discovery import (  # noqa: E402
    SkillCatalogRefreshMiddleware,
    configured_sources,
)
from agent.middlewares.skills_sync import SkillsSyncMiddleware  # noqa: E402
from agent.middlewares.user_skills_restore import (  # noqa: E402
    StoreAssignmentReader,
    UserSkillsRestoreMiddleware,
)
from agent.persistence.indexes import ensure_application_indexes  # noqa: E402
from agent.skills.pipeline import SkillPublisher  # noqa: E402
from agent.skills.store import SkillStore  # noqa: E402
from agent.subagents.loader import load_all, to_subagents  # noqa: E402
from agent.tools import with_local_tools  # noqa: E402
from fixtures import mongo_service, sandbox_service  # noqa: E402
from mcp_server.tools.registry import ERP_TOOLS, ERP_WRITE_TOOLS  # noqa: E402


@pytest.fixture(scope="module")
def runtime():
    settings = mongo_service.unique_settings(f"discovery-{uuid.uuid4().hex}")
    mongo_service.require_reachable(settings)
    try:
        with MongoClient(settings.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=5000) as client:
            database = client[settings.database]
            ensure_application_indexes(database)
            store = MongoDBStore(database[settings.store_collection])
            skill_store = SkillStore(store, database)
            with sandbox_service.running_backend() as (backend, _):
                result = backend.execute("mkdir -p /skills/users/main /skills/users/procurement-analyst /skills/users/procurement-order")
                assert result.exit_code == 0
                yield SimpleNamespace(backend=backend, store=store, skill_store=skill_store,
                                      checkpointer=MongoDBSaver(client, db_name=settings.database))
    finally:
        mongo_service.drop_test_database(settings)


def assembly(runtime, model, *, configs=None, subagent_models=None):
    return build_main_agent(
        model=model, tools=_stub_tools() if configs else [], write_tools=ERP_WRITE_TOOLS,
        backend=runtime.backend, owner_user_id="demo-a", subagents=configs or {},
        middleware=[
            SkillsSyncMiddleware(backend_provider=lambda: runtime.backend),
            UserSkillsRestoreMiddleware(backend_provider=lambda: runtime.backend,
                reader=StoreAssignmentReader(runtime.store, pointers=runtime.skill_store)),
        ], subagent_models=subagent_models, checkpointer=runtime.checkpointer,
    )


def invoke(graph, state, *, thread_id=None):
    config = {"configurable": {"thread_id": thread_id or uuid.uuid4().hex,
                               "owner_user_id": "demo-a"}}
    asyncio.run(graph.ainvoke(state, config=config))
    # PrivateStateAttr is hidden from graph outputs but persists in actual checkpoints.
    return graph.get_state(config).values


def prompt(model):
    return "\n".join(str(message.content) for message in model.contexts[-1] if message.type == "system")


@pytest.mark.integration
def test_main_model_receives_skill_management_after_sync(runtime):
    model = ScriptedChatModel(script=[AIMessage(content="ready")])
    graph = assembly(runtime, model).graph
    state = invoke(graph, {"messages": [HumanMessage("检查目录")]})
    assert "skill-management" in {s["name"] for s in state["skills_metadata"]}
    assert "/skills/main/skill-management/SKILL.md" in prompt(model)
    assert "当前没有可用技能" not in prompt(model)


@pytest.mark.integration
def test_delegated_models_receive_only_their_configured_presets(runtime):
    configs = load_all(available_tools=with_local_tools(ERP_TOOLS), write_tools=ERP_WRITE_TOOLS)
    analyst = ScriptedChatModel(script=[AIMessage(content=json.dumps({"summary": "ok", "facts": [], "artifact_ids": [], "warnings": [], "next_action": None}))])
    order = ScriptedChatModel(script=[AIMessage(content="ready")])
    model = ScriptedChatModel(script=[
        tool_call("task", {"description": "检查技能目录", "subagent_type": "procurement-analyst"}, "analyst-discovery"),
        tool_call("task", {"description": "检查技能目录", "subagent_type": "procurement-order"}, "order-discovery"),
        AIMessage(content="done"),
    ])
    invoke(assembly(runtime, model, configs=configs,
        subagent_models={"procurement-analyst": analyst, "procurement-order": order}).graph,
        {"messages": [HumanMessage("检查两个代理的目录")]})
    assert "web-scraper/SKILL.md" in prompt(analyst)
    assert "/skills/procurement/chart_params.md" in prompt(analyst)
    reference = runtime.backend.download_files(["/skills/procurement/chart_params.md"])[0]
    expected = Path(__file__).resolve().parents[2] / "src/skills/procurement/chart_params.md"
    assert not reference.error and reference.content == expected.read_bytes()
    assert "procurement-analysis/SKILL.md" in prompt(order)
    assert "web-scraper/SKILL.md" not in prompt(order)
    assert "skill-management/SKILL.md" not in prompt(order)
    specs = to_subagents(configs.values(), _stub_tools(), backend=runtime.backend)
    assert all(f"/skills/users/{spec['name']}" in spec["skills"] for spec in specs)


@pytest.mark.integration
def test_existing_catalogue_refreshes_after_real_skill_publication(runtime):
    model = ScriptedChatModel(script=[AIMessage(content="first"), AIMessage(content="second")])
    graph = assembly(runtime, model).graph
    thread_id = uuid.uuid4().hex
    first = invoke(graph, {"messages": [HumanMessage("first")]}, thread_id=thread_id)
    slug = "discovery-published"
    assert slug not in {s["name"] for s in first["skills_metadata"]}
    fixture = Path(__file__).resolve().parents[2] / "fixtures/skills/reorder-cost-summary-v1"
    files = {p.relative_to(fixture).as_posix(): p.read_bytes() for p in fixture.rglob("*") if p.is_file()}
    files["SKILL.md"] = files["SKILL.md"].replace(b"reorder-cost-summary", slug.encode())
    source = f"/workspace/scratch/{slug}"
    assert runtime.backend.execute(f"mkdir -p {source}/scripts {source}/examples").exit_code == 0
    responses = runtime.backend.upload_files([(f"{source}/{name}", content) for name, content in files.items()])
    assert all(not response.error for response in responses)
    publisher = SkillPublisher(backend_provider=lambda: runtime.backend, store=runtime.skill_store)
    prepared = publisher.prepare(source_type="generated", source=source, slug=slug)
    published = publisher.complete(prepared, owner_user_id="demo-a", scope="main")
    assert published.status == "assigned"
    from agent.middlewares.user_skills_restore import USER_SKILLS_REVISION_MARKER
    # Require the next API-style invocation (config only, no synthetic Runtime.context)
    # to restore the assigned files from MongoDB before catalogue discovery.
    result = runtime.backend.execute(
        f"rm -rf /skills/users/main/{slug}; rm -f {USER_SKILLS_REVISION_MARKER}"
    )
    assert result.exit_code == 0
    # Reuse the same real checkpoint; the SDK's old metadata is already in graph state.
    second = invoke(graph, {"messages": [HumanMessage("second")]}, thread_id=thread_id)
    assert slug in {s["name"] for s in second["skills_metadata"]}
    assert f"/skills/users/main/{slug}/SKILL.md" in prompt(model)
    assert all(not s["path"].startswith("/skills/users/procurement-") for s in second["skills_metadata"])


def test_unknown_configured_skill_is_rejected_and_reference_is_not_a_skill():
    with pytest.raises(ValueError, match="unknown configured skill"):
        configured_sources(("missing-skill",), "procurement-analyst")
    sources, references = configured_sources(("chart_params",), "procurement-analyst")
    assert sources == ["/skills/users/procurement-analyst"]
    assert references == ["/skills/procurement/chart_params.md"]


@pytest.mark.integration
@pytest.mark.parametrize("scope", ["main", "procurement-analyst", "procurement-order"])
def test_scope_refresh_replaces_stale_catalogue_from_another_scope(runtime, scope):
    path = f"/skills/users/{scope}/scope-visible"
    assert runtime.backend.execute(f"mkdir -p {path}").exit_code == 0
    document = f"---\nname: scope-visible\ndescription: private-catalogue-for-{scope}\n---\n"
    assert not runtime.backend.upload_files([(f"{path}/SKILL.md", document.encode())])[0].error
    sources, _ = configured_sources((), scope)
    middleware = SkillCatalogRefreshMiddleware(backend=runtime.backend, sources=sources,
                                               scope=scope, preset_names=())
    stale = {"skills_metadata": [{"name": "other-scope", "path": "/skills/users/other/secret/SKILL.md"}]}
    update = asyncio.run(middleware.abefore_agent(stale, None, {}))
    names = {skill["name"] for skill in update["skills_metadata"]}
    assert "other-scope" not in names
    skill = next(skill for skill in update["skills_metadata"] if skill["name"] == "scope-visible")
    assert skill["path"] == f"{path}/SKILL.md"
    assert skill["description"] == f"private-catalogue-for-{scope}"
    assert all(item["path"].startswith(f"/skills/users/{scope}/") for item in update["skills_metadata"])
