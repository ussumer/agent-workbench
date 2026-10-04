"""The whole system, running for real, for the live demo.

Every earlier suite assembled the part it was testing. The demo cannot do that: D01-D08
between them use the ERP, the gateway, the sandbox pool, MongoDB, the skill store, the
approval gate, the artifact routes and the Agent Protocol service, and a demo that stubbed
any of them would be demonstrating the stub.

Two decisions are worth stating because they are not obvious:

**The graphs are built eagerly, before the app starts.** ``graph_provider`` is called from
inside a running event loop (``api_view.api.chat._run_turn``), and assembling a graph needs
``MultiServerMCPClient.get_tools()``, which is a coroutine. Calling ``asyncio.run`` there
would fail with "already running". Building the two demo owners' graphs up front, while no
loop exists, is simpler than a thread hop and makes the per-owner tool headers explicit: the
gateway client is constructed with ``x-actor-id`` fixed to that owner, so no request can
change who a tool call is for.

**The middleware stack is the real one, all eight slots.** ``build_middlewares`` raises when
a slot's context key is missing rather than skipping it, so a stack that assembles is a stack
that has its sandbox health check, its skill sync, its preference injection and its budget.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(REPO_ROOT / "src"), str(REPO_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent.approval.middleware import HttpMCPWriteChannel, WriteApprovalMiddleware  # noqa: E402
from agent.approval.service import ApprovalService  # noqa: E402
from agent.approval.store import MongoPendingActionStore  # noqa: E402
from agent.artifacts.service import ArtifactService  # noqa: E402
from agent.artifacts.store import MongoArtifactStore  # noqa: E402
from agent.async_tasks.service import build_async_task_service  # noqa: E402
from agent.config import ModelConfig, ServiceAddresses  # noqa: E402
from agent.main_agent import build_main_agent, build_virtual_backend  # noqa: E402
from agent.middleware_config import build_middlewares, middleware_inventory  # noqa: E402
from agent.middlewares.tools_summarization import BudgetConfig  # noqa: E402
from agent.middlewares.user_skills_restore import StoreAssignmentReader  # noqa: E402
from agent.persistence.indexes import (  # noqa: E402
    drop_undeclared_application_indexes,
    ensure_application_indexes,
)
from agent.persistence.repository import ApplicationRepository  # noqa: E402
from agent.persistence.scoped_store import UserScopedStore  # noqa: E402
from agent.skills.store import SkillStore  # noqa: E402
from agent.subagents.loader import load_all  # noqa: E402
from agent.tools import with_local_tools  # noqa: E402
from fixtures import (  # noqa: E402
    agent_protocol_service,
    erp_service,
    loader,
    mcp_service,
    mongo_service,
    sandbox_service,
    site_service,
)
from mcp_server.tools.registry import ERP_TOOLS, ERP_WRITE_TOOLS  # noqa: E402

#: The two demo identities, in the order the UI offers them.
DEMO_USERS: tuple[str, ...] = ("demo-a", "demo-b")

#: The token the internal surfaces authenticate with. One value for the whole stack, because
#: the two processes have to agree: the Agent Protocol graph presents it, the app checks it.
INTERNAL_TOKEN = "live-internal-token"

#: Environment the Protocol process inherits from here, and which is therefore ours to set and
#: to put back. ``MAIN_SERVICE_BASE_URL_ENV`` is the address it calls back on; the token is what
#: proves it is allowed to.
MAIN_SERVICE_URL_ENV = "MAIN_SERVICE_BASE_URL"
INTERNAL_TOKEN_ENV = "INTERNAL_SERVICE_TOKEN"
_INHERITED_ENV = (MAIN_SERVICE_URL_ENV, INTERNAL_TOKEN_ENV)

#: Preferences a demo user starts with. Deliberately minimal: D06 is about the *user* setting
#: one, so seeding the same value here would make that case unfalsifiable.
DEFAULT_PREFERENCES: dict[str, str] = {"currency": "CNY", "language": "zh-CN"}


def _owner_from_runtime(runtime: Any) -> str | None:
    """Use the actual SDK runtime/configuration rather than a synthetic runtime shape."""
    from agent.runtime_context import runtime_owner

    return runtime_owner(runtime)


class RecordingGraph:
    """The compiled graph, with one observer attached to every run.

    Why this exists: the demo drives the real routes, because that is the only path where the
    approval is recorded and authorized — but a delegated sub-agent is invoked with
    ``await subagent.ainvoke(...)`` (``deepagents/middleware/subagents.py``), so its own tool
    calls are *never* on the SSE stream. A run driven over HTTP therefore shows a ``task`` call
    and nothing of what the sub-agent did with it, and "the numbers came from the ERP" becomes
    an assertion about the model's prose — the exact thing the reconciliation exists to avoid.

    The app builds its own run config and has no idea this observer exists, so the only place
    to add one is the graph the provider hands over. A callback is an observer: it does not
    change what the agent does, it only makes visible what already happened. The alternative —
    driving the graph in-process with callbacks — was tried and is worse: it skips the resume
    route, so the write gate refuses every approved write for want of an authorization the
    route is responsible for issuing.
    """

    def __init__(self, graph: Any, recorder: Any) -> None:
        self._graph = graph
        self._recorder = recorder

    def astream(self, payload: Any, config: Any = None, **kwargs: Any) -> Any:
        merged = {**(config or {})}
        merged["callbacks"] = [*(merged.get("callbacks") or []), self._recorder]
        return self._graph.astream(payload, merged, **kwargs)

    # Delegated explicitly rather than through ``__getattr__``: the app reaches the graph for
    # state as well as for runs, and a wrapper that answered every attribute would hide a
    # method it does not actually forward.
    def get_state(self, config: Any, **kwargs: Any) -> Any:
        return self._graph.get_state(config, **kwargs)

    def aget_state(self, config: Any, **kwargs: Any) -> Any:
        return self._graph.aget_state(config, **kwargs)

    def update_state(self, config: Any, values: Any, **kwargs: Any) -> Any:
        return self._graph.update_state(config, values, **kwargs)

    def invoke(self, payload: Any, config: Any = None, **kwargs: Any) -> Any:
        merged = {**(config or {})}
        merged["callbacks"] = [*(merged.get("callbacks") or []), self._recorder]
        return self._graph.invoke(payload, merged, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Only for attributes this wrapper did not enumerate, so a new call site fails loudly
        # rather than silently bypassing the recorder.
        raise AttributeError(
            f"RecordingGraph does not forward {name!r}; add it here if the app needs it"
        )


@dataclass
class LiveStack:
    """Everything the demo needs, already running."""

    settings: Any
    database: Any
    resources: Any
    store: Any
    skill_store: SkillStore
    artifacts: ArtifactService
    approvals: ApprovalService
    manager: Any
    factory: Any
    erp: Any
    gateway: Any
    site: Any
    app: Any
    #: Where the app answers. Empty until it is served; the background analyst reaches this
    #: process over this address, so it is a real one rather than an in-process shortcut.
    base_url: str = ""
    #: One breaker registry for the process, keyed by owner inside (see
    #: ``sandbox_breaker.BreakerRegistry``): one user's broken container must not stop another.
    breakers: Any = None
    graphs: dict[str, Any] = field(default_factory=dict)
    planning_graphs: dict[str, Any] = field(default_factory=dict)
    planning_episode_store: Any = None
    model_id: str = ""
    #: The configuration the graphs were built from, kept so the round can check that it is
    #: live *after* the stack came up — which is the first moment the services are all known
    #: to exist and the last moment before a trial would start writing evidence.
    model_config: Any = None
    services: ServiceAddresses = field(default_factory=ServiceAddresses)
    #: Observer attached to every run while it is set. ``None`` outside a trial, so a run the
    #: demo is not measuring is not instrumented.
    recorder: Any = None
    budget_config: BudgetConfig = field(default_factory=BudgetConfig)

    @contextlib.contextmanager
    def recording(self, recorder: Any) -> Iterator[None]:
        """Attach an observer for the duration of one trial."""
        previous = self.recorder
        self.recorder = recorder
        try:
            yield
        finally:
            self.recorder = previous

    @property
    def client(self) -> Any:
        """A client for the app, over the socket it is served on.

        Over HTTP rather than through a ``TestClient``, and that is not incidental: D08's
        background analyst calls this process back on an address, so there has to be one, and a
        runner that reached the app in-process would be exercising a path the callback does not
        take. Cookies are carried by the client's own jar, which is how the session route works
        in a browser too.
        """
        import httpx

        return httpx.Client(
            base_url=self.base_url,
            # Generous: one turn can spend a minute in the model and the sandbox before it
            # answers, and this client waits for the whole stream.
            timeout=httpx.Timeout(300.0, connect=10.0),
            trust_env=False,
        )

    def graph_provider(self, owner: str) -> Any:
        """The compiled agent for one owner, already built.

        Wrapped in :class:`RecordingGraph` while a trial is being measured, so the observer
        rides on the real run instead of on a second, parallel one.
        """
        if owner not in self.graphs:
            raise KeyError(
                f"no graph was assembled for {owner!r}; the live stack builds the graphs for "
                f"{list(self.graphs)} up front (see this module's docstring for why)"
            )
        graph = self.graphs[owner]
        return RecordingGraph(graph, self.recorder) if self.recorder is not None else graph

    def sandbox_for(self, owner: str) -> Any:
        return self.manager.get_or_create(owner)

    def planning_graph_provider(self, owner: str) -> Any:
        if owner not in DEMO_USERS:
            raise KeyError("unknown planning owner")
        if owner not in self.planning_graphs:
            self.planning_graphs[owner] = _assemble_planning(self, owner, self.model_config)
        graph = self.planning_graphs[owner]
        return RecordingGraph(graph, self.recorder) if self.recorder is not None else graph

    def sandbox_url(self) -> str:
        """Where the *container* reaches the fixture site."""
        return sandbox_service.sandbox_url_for_host_config(self.site.base_url)

    def connect_owners(self) -> None:
        """Create the two demo conversations. Called after the app exists."""
        repository = ApplicationRepository(self.database)
        for owner in DEMO_USERS:
            repository.ensure_thread(
                owner_user_id=owner, thread_id=f"{owner}-demo", title="演示会话"
            )


def _run_sync(coro: Any) -> Any:
    """Run a coroutine even when the caller already has a loop running.

    ``_assemble`` needs ``MultiServerMCPClient.get_tools()``, which is async, and the stack
    normally calls it before any loop exists. The trial runner cannot offer that: it wraps the
    whole round in ``asyncio.run`` because the graph is driven with ``await``, so the assembly
    happens *inside* a running loop and a plain ``asyncio.run`` raises.

    A worker thread gives the coroutine a loop of its own without touching the caller's. This
    is the general fix rather than making the runner special-case its setup, because the next
    async caller would hit exactly the same wall.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _gateway_tools(mcp_url: str, owner: str) -> list[Any]:
    """The gateway's tools, bound to one owner.

    The ``x-actor-id`` header is fixed here rather than passed per call, so a tool cannot be
    used as anybody but the owner it was built for — the same property the MCP contract
    requires and the reason a client is built per owner instead of shared.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "erp": {
                "url": mcp_url,
                "transport": "streamable_http",
                "headers": {"x-actor-id": owner},
            }
        }
    )
    return await client.get_tools()


def _local_tools(stack: LiveStack, owner: str, backend: Any) -> list[Any]:
    """The agent-layer tools, for one owner.

    These are not MCP tools — they run in this process — so a stack that only forwarded the
    gateway's list would fail the sub-agent loader, which refuses to grant a declared tool
    that is not in the catalogue. That refusal is the reason this function has to be complete
    rather than best-effort: a missing local tool is a startup error, by design.
    """
    from agent.env_utils import load_env
    from agent.skills.pipeline import SkillPublisher
    from agent.tools import (
        build_assign_skill_tool,
        build_chart_generator_tool,
        build_download_sandbox_file_tool,
        build_request_order_info_tool,
        build_web_search_tool,
    )

    env = load_env()
    tools: list[Any] = [build_request_order_info_tool()]

    chart_url = env.get("MODELSCOPE_MCP_URL") or ""
    chart_token = env.get("MODELSCOPE_API_TOKEN") or ""
    if chart_url and chart_token:
        tools.append(
            build_chart_generator_tool(
                url=chart_url,
                token=chart_token,
                # The chart is drawn remotely and arrives here, so this is the only place it
                # can become a file the user downloads.
                on_asset=stack.artifacts.chart_hook(
                    download_url_template="/api/artifacts/{artifact_id}/content"
                ),
            )
        )

    search_key = env.get("ZHIPU_API_KEY") or ""
    if search_key:
        tools.append(build_web_search_tool(api_key=search_key))

    tools.append(
        build_download_sandbox_file_tool(backend=backend, artifacts=stack.artifacts)
    )
    tools.append(
        build_assign_skill_tool(
            publisher=SkillPublisher(
                backend_provider=lambda: backend,
                store=stack.skill_store,
                allowed_source_hosts=(sandbox_service.SANDBOX_HOST_ALIAS,),
            ),
            artifacts=stack.artifacts,
            # The smoke-repair budget is counted here, across the model's own retries.
            store=stack.skill_store,
        )
    )
    return tools


def _assemble(stack: LiveStack, owner: str, model_config: ModelConfig) -> Any:
    """Build one owner's agent, with the full middleware stack."""
    backend = stack.manager.get_or_create(owner)
    scoped = UserScopedStore(stack.store, owner)
    virtual = build_virtual_backend(
        sandbox_backend=backend, store=scoped, owner_user_id=owner
    )
    tools = _run_sync(_gateway_tools(stack.gateway.mcp_url, owner))
    tools = [*tools, *_local_tools(stack, owner, backend)]

    catalogue = with_local_tools(ERP_TOOLS)
    configs = load_all(available_tools=catalogue, write_tools=ERP_WRITE_TOOLS)

    def workspace_writer(path: str, payload: bytes) -> None:
        backend.upload_files([(path, payload)])

    context = {
        "manager": stack.manager,
        "owner_resolver": _owner_from_runtime,
        "store_provider": lambda user: UserScopedStore(stack.store, user),
        "backend_provider": lambda: backend,
        "skill_reader": StoreAssignmentReader(stack.store, pointers=stack.skill_store),
        "model": model_config.create_chat_model(),
        "backend": virtual,
        "workspace_writer": workspace_writer,
        "breaker_registry": stack.breakers,
        "budget_config": stack.budget_config,
    }
    # ``build_middlewares`` returns the *slots the contract declares*, and two of them are not
    # middleware: ``conversation_summary`` contributes an active ``compact_conversation``
    # *tool*, and ``sandbox_breaker`` contributes a per-user registry the sandbox call sites
    # report to. Both are real parts of the stack and neither belongs in a middleware list —
    # ``create_deep_agent`` reads ``m.name`` on everything it is handed, so passing them would
    # be a startup crash rather than a no-op.
    from langchain.agents.middleware import AgentMiddleware

    middleware = [
        item for item in build_middlewares(context) if isinstance(item, AgentMiddleware)
    ]

    approval = WriteApprovalMiddleware(
        service=stack.approvals,
        channel=HttpMCPWriteChannel(mcp_url=stack.gateway.mcp_url),
        write_tools=ERP_WRITE_TOOLS,
    )

    assembly = build_main_agent(
        model=model_config.create_chat_model(),
        tools=tools,
        write_tools=ERP_WRITE_TOOLS,
        backend=virtual,
        owner_user_id=owner,
        subagents=configs,
        preferences=dict(DEFAULT_PREFERENCES),
        middleware=middleware,
        middleware_inventory=middleware_inventory(context),
        # Inside the sub-agent too: the order agent is where a write actually happens, and a
        # gate that only watched the parent's calls would miss it entirely.
        subagent_middleware=[approval],
        checkpointer=stack.resources.checkpointer,
        store=scoped,
    )
    return assembly.graph


def _assemble_planning(stack: LiveStack, owner: str, model_config: ModelConfig) -> Any:
    from langchain.agents.middleware import AgentMiddleware

    from agent.planning.actor import build_planning_actor
    from agent.planning.computation import ComputationService
    from agent.planning.orders import PlanningOrders

    backend = stack.manager.get_or_create(owner)
    scoped = UserScopedStore(stack.store, owner)
    virtual = build_virtual_backend(sandbox_backend=backend, store=scoped, owner_user_id=owner)
    context = {
        "manager": stack.manager, "owner_resolver": _owner_from_runtime,
        "store_provider": lambda user: UserScopedStore(stack.store, user),
        "backend_provider": lambda: backend,
        "skill_reader": StoreAssignmentReader(stack.store, pointers=stack.skill_store),
        "model": model_config.create_chat_model(), "backend": virtual,
        "workspace_writer": lambda path, payload: backend.upload_files([(path, payload)]),
        "breaker_registry": stack.breakers, "budget_config": stack.budget_config,
    }
    middleware = tuple(item for item in build_middlewares(context)
                       if isinstance(item, AgentMiddleware) and item.name != "TodoListMiddleware")
    return build_planning_actor(
        model_config=model_config, orders=PlanningOrders(stack.database,
                                                       grant_secret=stack.gateway.grant_secret),
        kernel=ComputationService(stack.database, stack.manager.get_or_create),
        channel=HttpMCPWriteChannel(stack.gateway.mcp_url), backend=virtual,
        checkpointer=stack.resources.checkpointer, store=scoped, middleware=middleware,
        episode_store=stack.planning_episode_store,
    )


@contextlib.contextmanager
def running_stack(
    *,
    warm_pool_size: int = 1,
    model_config: ModelConfig | None = None,
    run_dir: Path | None = None,
    database_name: str | None = None,
    preserve_data: bool = False,
    planning_only: bool = False,
) -> Iterator[LiveStack]:
    """Start everything and tear it down, even on failure.

    The fixture site always starts: D05 and D07 need it, and starting it is a fraction of the
    cost of the ERP beside it, so a flag to skip it would save nothing worth the extra state.

    ``run_dir`` and ``database_name`` exist for ``scripts/dev.py``, which is a *session* rather
    than a test: its logs have to outlive the process and its database has to be findable by
    name across restarts. Pass neither (the default, and what every test does) and the two are
    a temp directory and a unique database, exactly as before. ``preserve_data``
    disables deletion both at startup and teardown (including exceptions); it
    requires both stable coordinates and is enabled only by the dev launcher.
    """
    # Retention must be explicit: named test databases still get cleaned by default.
    if preserve_data and (run_dir is None or not database_name):
        raise ValueError("preserve_data requires a stable run_dir and database_name")
    model_config = model_config or ModelConfig.from_env()

    with contextlib.ExitStack() as life:
        if run_dir is None:
            run_dir = Path(life.enter_context(tempfile.TemporaryDirectory(prefix="live-stack-")))
        else:
            run_dir.mkdir(parents=True, exist_ok=True)
        settings = mongo_service.unique_settings(database_name or f"live-{uuid.uuid4().hex[:8]}")
        mongo_service.require_reachable(settings)
        if not preserve_data:
            mongo_service.drop_test_database(settings)

        from pymongo import MongoClient

        from api_view.agent_loader import build_persistence

        mongo = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True)
        database = mongo[settings.database]
        drop_undeclared_application_indexes(database)
        ensure_application_indexes(database)

        resources = build_persistence(settings)

        # D08's background tasks run on the Agent Protocol service, and that service calls
        # *back* into this process (`/internal/analysis/read`). It therefore has to be started
        # after the app is listening and told where — see the ``_serving`` block below. Kept in
        # an ExitStack so the teardown order stays in one place at the end of this function.
        protocol = contextlib.ExitStack()
        try:
            with erp_service.running_erp(run_dir / "erp", seed_path=loader.SEED_PATH) as erp:
                app_port = _free_port()
                with mcp_service.running_gateway(
                    run_dir / "gateway",
                    erp_base_url=erp.base_url,
                    erp_token=erp.token,
                    extra_env={"MCP_APPROVAL_VERIFY_URL":
                               f"http://127.0.0.1:{app_port}/internal/approvals/verify",
                               INTERNAL_TOKEN_ENV: INTERNAL_TOKEN},
                ) as gateway:
                    # The port is not ours to choose. Every procurement skill tells the agent to
                    # fetch quotes from the host at the port inside ``FIXTURES_BASE_URL``
                    # (``supplier-price-urls`` documents it, ``web-scraper`` shows the command),
                    # and the sandbox has no other way to learn the real one. A random port
                    # therefore hands the agent an address nothing answers on — which is what
                    # happened: the site ran on 8088's *default* config while the stack served a
                    # free port, and the case degraded into reports titled 「无报价，无法计算补货
                    # 成本」 that still passed. A busy configured port is a startup error rather
                    # than a silent fallback, because falling back is what broke this.
                    fixture_port = (
                        urlparse(ServiceAddresses.from_env().fixtures_base_url).port or 8088
                    )
                    with site_service.running_site(
                        run_dir / "site", host="0.0.0.0", port=fixture_port
                    ) as site:
                        with sandbox_service.running_pool(
                            settings, warm_pool_size=warm_pool_size
                        ) as (manager, factory):
                            stack = LiveStack(
                                settings=settings,
                                database=database,
                                resources=resources,
                                store=resources.store,
                                skill_store=SkillStore(resources.store, database),
                                artifacts=ArtifactService(
                                    store=MongoArtifactStore(database)
                                ),
                                # The secret the gateway was started with, not one read from
                                # the environment: these two have to agree, and the harness
                                # started the gateway, so the harness knows the value.
                                approvals=ApprovalService(
                                    store=MongoPendingActionStore(database),
                                    grant_secret=mcp_service.DEFAULT_GRANT_SECRET,
                                ),
                                manager=manager,
                                factory=factory,
                                erp=erp,
                                gateway=gateway,
                                site=site,
                                app=None,
                                breakers=manager.breakers,
                                model_id=model_config.model_id,
                                model_config=model_config,
                                services=ServiceAddresses.from_env(),
                            )
                            # Built before the app starts, while no event loop is running.
                            # A planning-only experiment does not need the course main Agent;
                            # skipping it avoids optional report-tool configuration becoming a
                            # false blocker before the independent planning Actor is exercised.
                            if not planning_only:
                                for owner in DEMO_USERS:
                                    stack.graphs[owner] = _assemble(stack, owner, model_config)
                            stack.app = _create_app(stack)
                            # Served on a real socket, because the background analyst reaches
                            # this process over HTTP. A TestClient has no port, so the callback
                            # had nowhere to go and the task ended with an empty last_error —
                            # a failure that looked like the task's fault and was the
                            # harness's.
                            with _serving(stack, port=app_port) as base_url:
                                # The Protocol process reads these from *its* environment, so
                                # they are set before it starts and removed after: a value left
                                # behind would silently redirect the next thing that reads it.
                                previous = {key: os.environ.get(key) for key in _INHERITED_ENV}
                                os.environ[MAIN_SERVICE_URL_ENV] = base_url
                                os.environ[INTERNAL_TOKEN_ENV] = INTERNAL_TOKEN
                                # Not reused even if something already answers: an instance
                                # started with a different MAIN_SERVICE_BASE_URL would point at
                                # a process that is gone, and reusing it would turn that into a
                                # mysterious empty failure again.
                                protocol.enter_context(
                                    agent_protocol_service.running_service(reuse_running=False)
                                )
                                try:
                                    yield stack
                                finally:
                                    for key, value in previous.items():
                                        if value is None:
                                            os.environ.pop(key, None)
                                        else:
                                            os.environ[key] = value
        finally:
            resources.close()
            mongo.close()
            if not preserve_data:
                mongo_service.drop_test_database(settings)
            protocol.close()


def _free_port() -> int:
    """A port the operating system says is free, rather than a fixed one two rounds could share."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextlib.contextmanager
def _serving(stack: LiveStack, *, port: int | None = None) -> Iterator[str]:
    """Serve the app on a real port for the duration of the block, and yield its address.

    A real server rather than a ``TestClient``, because D08's background analyst lives in
    another process and calls back into this one. Without a listening socket that callback has
    nowhere to go, and the task ends with an empty ``last_error`` — which reads like the task's
    fault and is the harness's.
    """
    import threading
    import time

    import uvicorn

    port = _free_port() if port is None else port
    base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(stack.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="live-app", daemon=True)
    thread.start()

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.2)
    if not server.started:
        raise RuntimeError(f"the app did not start listening on {base_url} within 60s")

    stack.base_url = base_url
    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        stack.base_url = ""


def _create_app(stack: LiveStack) -> Any:
    from agent.env_utils import load_env, secret_values
    from agent.evolution.episodes import EpisodeStore
    from agent.planning.orders import PlanningOrders
    from api_view.api.deps import WebContext
    from api_view.run_registry import RunRegistry
    from api_view.web_main import create_app
    stack.approvals.planning_guard = PlanningOrders(
        stack.database, grant_secret=stack.gateway.grant_secret,
    ).validate_action
    stack.planning_episode_store = EpisodeStore(stack.database, secrets=secret_values(load_env()))
    model_identity = {k: v for k, v in stack.model_config.redacted().items() if k != "api_key"}
    context = WebContext(
        resources=stack.resources,
        repository=ApplicationRepository(stack.database),
        registry=RunRegistry(),
        approvals=stack.approvals,
        artifacts=stack.artifacts,
        async_tasks=build_async_task_service(stack.database, artifacts=stack.artifacts),
        settings=stack.settings,
        graph_provider=stack.graph_provider,
        extra={"planning_graph_provider": stack.planning_graph_provider,
               "planning_erp_client": stack.erp.client,
               "planning_episode_store": stack.planning_episode_store,
               "planning_model_identity": {"provenance": "configured-live", "config": model_identity}},
        internal_service_token=INTERNAL_TOKEN,
        sandboxes=stack.manager,
        # The gateway *this stack started*, not the one the environment names. The internal
        # analysis surface is what the background analyst reads through, so pointing it at an
        # address that belongs to some other process left every background read failing with
        # "gateway read failed" — a 503 that arrived in the Protocol service's log and nowhere
        # else, because the task's own error field stayed empty.
        mcp_url=stack.gateway.mcp_url,
        budget_config=stack.budget_config,
    )
    return create_app(context=context)


__all__ = [
    "DEFAULT_PREFERENCES",
    "DEMO_USERS",
    "LiveStack",
    "running_stack",
]
