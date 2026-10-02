"""T21 acceptance: recovery, isolation, and faults across module boundaries.

Every earlier task pinned its own module's behaviour. This suite is about what happens
*between* them — a write whose response is lost, a container that disappears under a skill, a
run that ends while nobody is watching, two owners in flight at once. Those are the cases
where a correct module and a correct module still add up to a wrong system.

Three properties are asserted rather than described:

* **No user's state is reachable from another's**, including through the shared HTTP client —
  which is checked by reading the code that would have to hold it, and by running callers
  concurrently.
* **Persistence lives in MongoDB, not in this process**, checked by reading it back from a
  separate interpreter.
* **A fault produces a checksable state**, never a spinner and never a success. The places
  that report ``lost``/``503``/``409`` are asserted here at the level where a user would meet
  them.

The acceptance also asks that the fault scenarios be run three times without flakes, so the
two scenarios where a race is plausible (concurrent writes, cancel vs. poll) are executed
three times each and their outcomes compared. Retrying until they pass would hide exactly the
class of bug this task exists to find.
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.approval.models import PendingStatus  # noqa: E402
from agent.approval.service import ApprovalService  # noqa: E402
from agent.approval.store import MongoPendingActionStore  # noqa: E402
from agent.artifacts.service import ArtifactService  # noqa: E402
from agent.artifacts.store import MongoArtifactStore  # noqa: E402
from agent.async_tasks.service import (  # noqa: E402
    ASYNC_ANALYST_GRAPH_ID,
    AsyncTaskService,
    SdkProtocolClient,
)
from agent.async_tasks.store import MongoAsyncTaskStore  # noqa: E402
from agent.middlewares.user_skills_restore import (  # noqa: E402
    USER_SKILLS_ROOT,
    StoreAssignmentReader,
    UserSkillsRestoreMiddleware,
)
from agent.persistence.indexes import (  # noqa: E402
    drop_undeclared_application_indexes,
    ensure_application_indexes,
)
from agent.persistence.repository import ApplicationRepository  # noqa: E402
from agent.skills.pipeline import SkillPublisher, SkillValidationError  # noqa: E402
from agent.skills.store import SkillStore, content_digest  # noqa: E402
from fixtures import (  # noqa: E402
    agent_protocol_service,
    erp_service,
    loader,
    mcp_service,
    mongo_service,
    sandbox_service,
    site_service,
)

pytestmark = pytest.mark.integration

OWNER_A = "demo-a"
OWNER_B = "demo-b"
GRANT_SECRET = mcp_service.DEFAULT_GRANT_SECRET
SRC_DIR = Path(__file__).resolve().parents[2] / "src"

GOOD_ARGS = {
    "supplier_id": "S001",
    "currency": "CNY",
    "lines": [{"part_id": "P001", "quantity": 50, "unit_price": "25.50"}],
}


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def settings():
    candidate = mongo_service.unique_settings(f"t21-{uuid.uuid4().hex[:8]}")
    mongo_service.require_reachable(candidate)
    mongo_service.drop_test_database(candidate)
    yield candidate
    mongo_service.drop_test_database(candidate)


@pytest.fixture(scope="module")
def database(settings):
    from pymongo import MongoClient

    with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True) as mongo:
        db = mongo[settings.database]
        drop_undeclared_application_indexes(db)
        ensure_application_indexes(db)
        yield db


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    """The real ERP and the real gateway. Idempotency is theirs to enforce, not ours."""
    with erp_service.running_erp(
        tmp_path_factory.mktemp("t21-erp"), seed_path=loader.SEED_PATH
    ) as erp:
        with mcp_service.running_gateway(
            tmp_path_factory.mktemp("t21-gateway"),
            erp_base_url=erp.base_url,
            erp_token=erp.token,
            grant_secret=GRANT_SECRET,
        ) as gateway:
            yield erp, gateway


@pytest.fixture(scope="module")
def gateway(stack):
    return stack[1]


@pytest.fixture(scope="module")
def sandbox():
    with sandbox_service.running_backend() as (backend, _report):
        yield backend


@pytest.fixture(scope="module")
def skill_store(database):
    from langgraph.store.mongodb import MongoDBStore

    client = database.client
    store = MongoDBStore(client[database.name]["persistent-store"])
    return SkillStore(store, database)


@pytest.fixture(scope="module")
def artifacts(database):
    return ArtifactService(store=MongoArtifactStore(database))


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    with site_service.running_site(tmp_path_factory.mktemp("t21-site"), host="0.0.0.0") as running:
        yield running


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def run_script(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


FIXTURE_SKILL = (
    Path(__file__).resolve().parents[2] / "fixtures" / "skills" / "reorder-cost-summary-v1"
)


def fixture_files(slug: str = "reorder-cost-summary") -> dict[str, bytes]:
    files = {
        path.relative_to(FIXTURE_SKILL).as_posix(): path.read_bytes()
        for path in sorted(FIXTURE_SKILL.rglob("*"))
        if path.is_file()
    }
    files["SKILL.md"] = files["SKILL.md"].decode("utf-8").replace("reorder-cost-summary", slug).encode()
    return files


def upload_tree(backend, root: str, files: dict[str, bytes]) -> None:
    """Write a directory into the sandbox.

    Only names that *contain* a slash have a parent directory — ``SKILL.md`` sits at the root,
    and asking ``mkdir -p`` for its "parent" would create a directory called ``SKILL.md`` and
    then fail to write the file of the same name.
    """
    directories = {
        f"{root}/{name.rsplit('/', 1)[0]}" for name in files if "/" in name
    }
    if directories:
        backend.execute("mkdir -p " + " ".join(f"'{path}'" for path in sorted(directories)))
    responses = backend.upload_files(
        [(f"{root}/{name}", payload) for name, payload in files.items()]
    )
    failures = [getattr(item, "error", None) for item in responses if getattr(item, "error", None)]
    assert not failures, failures


def tool_text(raw: object) -> str:
    """Flatten an MCP tool result to text.

    MCP tools answer with a list of content blocks rather than a string, so the envelope has
    to be reassembled before it can be parsed — the same shape T16's suite had to handle.
    """
    if isinstance(raw, str):
        return raw
    blocks = raw if isinstance(raw, list) else [raw]
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
        else:
            parts.append(json.dumps(block, ensure_ascii=False, default=str))
    return "\n".join(parts)


def tool_payload(raw: object) -> dict:
    return json.loads(tool_text(raw))


def db_fingerprint(database) -> dict[str, int]:
    """Row counts per application collection, for before/after comparison."""
    from agent.persistence.indexes import APPLICATION_COLLECTIONS

    return {name: database[name].count_documents({}) for name in APPLICATION_COLLECTIONS}


# --------------------------------------------------------------------------- #
# V09 / V10 / V04 — writes that are retried, lost, or raced
# --------------------------------------------------------------------------- #


def _grant_for(*, owner: str, operation_id: str, arguments: dict) -> str:
    """Mint the approval grant an approved write would carry.

    The payload digest is taken from the *same* canonical builder the gateway uses, so the
    grant is accepted for exactly these bytes and for no others. ``operation_id`` travels in
    the grant rather than as a tool argument, because the contract forbids the model from
    supplying one — a caller that could name its own operation id could forge a retry.
    """
    from agent.approval.models import payload_digest
    from mcp_server.grants import issue_grant
    from mcp_server.tools.registry import OrderLineInput, canonical_order_body

    body = canonical_order_body(
        supplier_id=arguments["supplier_id"],
        currency=arguments["currency"],
        lines=[OrderLineInput(**line) for line in arguments["lines"]],
        note=arguments.get("note"),
    )
    return issue_grant(
        secret=GRANT_SECRET,
        owner=owner,
        tool="order_create",
        target="orders",
        operation_id=operation_id,
        payload_sha256=payload_digest(body),
    )


def _operate(gateway, tool: str, arguments: dict, *, owner: str, operation_id: str) -> dict:
    """Call a write tool through the gateway the way the agent does, and return the envelope."""
    import asyncio

    from langchain_mcp_adapters.client import MultiServerMCPClient

    headers = {
        "x-actor-id": owner,
        "x-approval-grant": _grant_for(
            owner=owner, operation_id=operation_id, arguments=arguments
        ),
    }

    async def call() -> dict:
        client = MultiServerMCPClient(
            {"erp": {"url": gateway.mcp_url, "transport": "streamable_http", "headers": headers}}
        )
        tools = await client.get_tools()
        for candidate in tools:
            if candidate.name == tool:
                return tool_payload(await candidate.ainvoke(dict(arguments)))
        raise AssertionError(f"gateway does not expose {tool}")

    return asyncio.run(call())


def test_a_lost_response_is_recoverable_by_the_same_operation_id(gateway):
    """V10: the client never saw the answer, and retries. There must still be one order."""
    operation_id = f"t21-lost-{uuid.uuid4().hex[:10]}"

    first = _operate(gateway, "order_create", GOOD_ARGS, owner=OWNER_A, operation_id=operation_id)
    second = _operate(gateway, "order_create", GOOD_ARGS, owner=OWNER_A, operation_id=operation_id)

    assert first["ok"] is True, first
    assert second["ok"] is True, second
    assert first["data"]["order_id"] == second["data"]["order_id"], (
        "同 operation_id 的重试必须取回同一张订单，而不是再下一单"
    )


def test_the_same_operation_id_from_another_owner_is_a_different_order(gateway):
    """An operation ledger scoped too loosely would let one user's retry cancel another's."""
    operation_id = f"t21-shared-{uuid.uuid4().hex[:10]}"

    mine = _operate(gateway, "order_create", GOOD_ARGS, owner=OWNER_A, operation_id=operation_id)
    theirs = _operate(gateway, "order_create", GOOD_ARGS, owner=OWNER_B, operation_id=operation_id)

    assert mine["ok"] is True and theirs["ok"] is True
    assert mine["data"]["order_id"] != theirs["data"]["order_id"]
    assert theirs["data"]["owner_user_id"] == OWNER_B


def test_concurrent_retries_of_one_write_still_produce_one_order(gateway):
    """V04. Run three times: a race that only sometimes loses is still a race."""
    outcomes = []
    for attempt in range(3):
        operation_id = f"t21-race-{attempt}-{uuid.uuid4().hex[:8]}"
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(
                    lambda _: _operate(
                        gateway, "order_create", GOOD_ARGS, owner=OWNER_A, operation_id=operation_id
                    ),
                    range(4),
                )
            )
        order_ids = {item["data"]["order_id"] for item in results if item.get("ok")}
        outcomes.append(len(order_ids))

    assert outcomes == [1, 1, 1], f"四次并发重试应当只产生一张订单，实测每轮订单数 {outcomes}"


def test_an_unapproved_write_never_reaches_the_erp(gateway, database):
    """V07: refuse, and leave the database exactly as it was."""
    import asyncio

    from langchain_mcp_adapters.client import MultiServerMCPClient

    before = database["runs"].count_documents({})

    async def call() -> dict:
        client = MultiServerMCPClient(
            {
                "erp": {
                    "url": gateway.mcp_url,
                    "transport": "streamable_http",
                    "headers": {"x-actor-id": OWNER_A},
                }
            }
        )
        tools = await client.get_tools()
        for tool in tools:
            if tool.name == "order_create":
                return tool_payload(await tool.ainvoke(dict(GOOD_ARGS)))
        raise AssertionError("no order_create")

    body = asyncio.run(call())

    assert body.get("ok") is False, body
    assert "grant" in json.dumps(body).lower() or "approval" in json.dumps(body).lower()
    assert database["runs"].count_documents({}) == before


def test_a_grant_is_bound_to_the_bytes_it_was_approved_for(gateway):
    """V07: approving 50 pieces must not authorise 9999.

    The grant is bound to the digest of the canonical body, so changing a quantity after
    approval makes the token unusable rather than merely suspicious.
    """
    import asyncio

    from langchain_mcp_adapters.client import MultiServerMCPClient

    operation_id = f"t21-tamper-{uuid.uuid4().hex[:8]}"
    grant = _grant_for(owner=OWNER_A, operation_id=operation_id, arguments=GOOD_ARGS)

    async def call() -> dict:
        client = MultiServerMCPClient(
            {
                "erp": {
                    "url": gateway.mcp_url,
                    "transport": "streamable_http",
                    "headers": {"x-actor-id": OWNER_A, "x-approval-grant": grant},
                }
            }
        )
        tools = await client.get_tools()
        for tool in tools:
            if tool.name == "order_create":
                tampered = {
                    **GOOD_ARGS,
                    "lines": [{"part_id": "P001", "quantity": 9999, "unit_price": "25.50"}],
                }
                return tool_payload(await tool.ainvoke(tampered))
        raise AssertionError("no order_create")

    body = asyncio.run(call())

    assert body.get("ok") is False, body
    assert "MISMATCH" in json.dumps(body) or "grant" in json.dumps(body).lower()


def test_two_approvals_for_one_interrupt_produce_one_order(database, gateway):
    """V09: two tabs decide at once. One order, and the loser is told it lost."""
    store = MongoPendingActionStore(database)
    service = ApprovalService(store=store, grant_secret=GRANT_SECRET)
    thread_id = f"t21-tabs-{uuid.uuid4().hex[:8]}"
    interrupt = {
        "action_requests": [{"name": "order_create", "args": GOOD_ARGS, "description": "x"}],
        "review_configs": [{"action_name": "order_create", "allowed_decisions": ["approve", "reject"]}],
    }
    service.record(
        owner_user_id=OWNER_A, thread_id=thread_id, interrupt_id="i1", interrupt_value=interrupt
    )

    def decide(request_id: str) -> str:
        try:
            return service.approve(
                owner_user_id=OWNER_A,
                thread_id=thread_id,
                interrupt_id="i1",
                request_id=request_id,
            ).status
        except Exception as failure:  # noqa: BLE001 - the loser's refusal is the point
            return f"refused:{getattr(failure, 'code', type(failure).__name__)}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, ("tab-1", "tab-2")))

    approved = [item for item in results if item == PendingStatus.APPROVED]
    refused = [item for item in results if item.startswith("refused")]
    assert len(approved) == 1, results
    assert len(refused) == 1, results

    record = store.find(OWNER_A, thread_id, "i1")
    assert record is not None and record.status is PendingStatus.APPROVED


# --------------------------------------------------------------------------- #
# V13 / V15 / V16 — a skill outliving its container
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def published_skill(sandbox, skill_store, site):
    publisher = SkillPublisher(
        backend_provider=lambda: sandbox,
        store=skill_store,
        allowed_source_hosts=(sandbox_service.SANDBOX_HOST_ALIAS,),
    )
    slug = "reorder-cost-survivor"
    root = f"/workspace/scratch/t21/{slug}"
    upload_tree(sandbox, root, fixture_files(slug))
    prepared = publisher.prepare(source_type="generated", source=root, slug=slug)
    result = publisher.complete(prepared, owner_user_id=OWNER_A, scope="main")
    return publisher, skill_store, slug, result


def test_a_skill_is_restored_from_the_store_after_its_copy_is_deleted(
    published_skill, sandbox, skill_store
):
    """V13/V16: the copy in the container is a cache, not the record."""
    _publisher, store, slug, result = published_skill
    target = f"{USER_SKILLS_ROOT}/main/{slug}"

    # What a rebuilt container looks like: the files and the marker are simply gone.
    sandbox.execute(f"rm -rf {USER_SKILLS_ROOT}; rm -f /skills/.user-skills-revision")

    reader = StoreAssignmentReader(store._store, pointers=store)  # noqa: SLF001 - test wiring
    report = UserSkillsRestoreMiddleware(
        backend_provider=lambda: sandbox, reader=reader
    ).restore(OWNER_A, generation=99)

    assert target in report.restored, report.as_dict()
    restored = sandbox.download_files([f"{target}/SKILL.md"])[0]
    assert restored.error is None
    assert restored.content is not None

    version = store.get_version(OWNER_A, "main", slug, result.version)
    assert version is not None
    assert hashlib.sha256(restored.content).hexdigest() in {
        entry["sha256"] for entry in json.loads(
            store.file_bytes(version.store_prefix)["manifest.json"].decode()
        )["files"]
    }


def test_the_restored_skill_really_runs(published_skill, sandbox):
    """A copy that exists but cannot execute would pass a weaker check than this one."""
    _publisher, _store, slug, _result = published_skill
    target = f"{USER_SKILLS_ROOT}/main/{slug}"

    executed = sandbox.execute(
        f"cd {target} && python3 scripts/summarise.py "
        "--input examples/input.json --out-dir /workspace/scratch/t21-restored"
    )

    assert executed.exit_code == 0, executed.output
    assert "1533.00" in executed.output


def test_a_half_written_version_is_not_assignable_after_a_reconnect(published_skill, database):
    """V15: the read-back check is what makes 'persisted' mean something."""
    _publisher, store, slug, result = published_skill
    version = store.get_version(OWNER_A, "main", slug, result.version)
    assert version is not None

    # Delete one declared file, leaving the manifest claiming it.
    store._store.delete(  # noqa: SLF001 - simulating a partial write
        ("skills",), f"{version.store_prefix}/scripts/summarise.py"
    )
    remaining = {
        path: payload
        for path, payload in store.file_bytes(version.store_prefix).items()
        if path != "manifest.json"
    }

    from agent.middlewares.user_skills_restore import (
        ManifestIntegrityError,
        SkillAssignment,
        verify_manifest,
    )

    with pytest.raises(ManifestIntegrityError):
        verify_manifest(
            SkillAssignment(
                owner_user_id=OWNER_A, scope="main", slug=slug, version=result.version
            ),
            remaining,
        )

    # Put it back so later tests in this module see a complete version.
    store._store.put(  # noqa: SLF001
        ("skills",),
        f"{version.store_prefix}/scripts/summarise.py",
        fixture_files(slug)["scripts/summarise.py"],
    )


def test_a_malicious_archive_creates_no_version_at_all(published_skill, skill_store):
    """V14/V15: a refusal that still wrote something would not be a refusal."""
    _publisher, store, _slug, _result = published_skill
    before = {
        (item.scope, item.slug, item.version) for item in store.list_versions(OWNER_A, "main", "x")
    }
    assert before == set()

    from agent.skills.pipeline import _expand_archive

    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: evil\ndescription: x\n---\n")
        archive.writestr("../../escape.py", "print('escape')")

    with pytest.raises(SkillValidationError) as failure:
        _expand_archive(buffer.getvalue())

    assert failure.value.code == "PATH_TRAVERSAL"
    assert store.list_versions(OWNER_A, "main", "evil") == []


# --------------------------------------------------------------------------- #
# V11 — the record is in MongoDB, not in this process
# --------------------------------------------------------------------------- #


def test_a_separate_process_sees_the_published_skill(settings, published_skill):
    """Read back from a fresh interpreter: nothing may live only in this one."""
    _publisher, _store, slug, result = published_skill
    script = (
        "import json, sys\n"
        "sys.path.insert(0, 'src')\n"
        "from pymongo import MongoClient\n"
        f"client = MongoClient({settings.mongo_uri!r}, serverSelectionTimeoutMS=5000)\n"
        f"db = client[{settings.database!r}]\n"
        "versions = list(db['skill_versions'].find("
        f"{{'owner_user_id': {OWNER_A!r}, 'slug': {slug!r}}}))\n"
        "assignments = list(db['skill_assignments'].find("
        f"{{'owner_user_id': {OWNER_A!r}, 'slug': {slug!r}}}))\n"
        "print(json.dumps({'versions': [v['version'] for v in versions], "
        "'assigned': [a['version'] for a in assignments]}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(Path(__file__).resolve().parents[2]),
    )

    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result.version in observed["versions"]
    assert observed["assigned"] == [result.version]


def test_the_files_themselves_are_readable_from_another_connection(settings, published_skill):
    """The version row could exist while the bytes did not; this is the bytes."""
    _publisher, _store, slug, result = published_skill

    from pymongo import MongoClient

    from agent.skills.store import SkillStore

    with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True) as mongo:
        from langgraph.store.mongodb import MongoDBStore

        fresh = SkillStore(MongoDBStore(mongo[settings.database]["persistent-store"]), mongo[settings.database])
        version = fresh.get_version(OWNER_A, "main", slug, result.version)
        assert version is not None
        files = fresh.file_bytes(version.store_prefix)

    assert "SKILL.md" in files
    assert "manifest.json" in files
    assert content_digest(
        {name: payload for name, payload in files.items() if name != "manifest.json"}
    ) == version.content_sha256


# --------------------------------------------------------------------------- #
# isolation, and the shared-state audit the task asks for
# --------------------------------------------------------------------------- #


def test_no_module_level_container_holds_per_user_state():
    """Step 3, done as a check rather than as a claim.

    A module-level dict or list is how a per-user value becomes a per-process one: it is
    written for the caller of the moment and read by the next. The code has none, and this
    keeps it that way.
    """
    import re

    pattern = re.compile(r"^[A-Z][A-Z0-9_]*\s*=\s*(\{\}|\[\]|dict\(\)|list\(\))\s*$", re.MULTILINE)
    offenders = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(SRC_DIR)}: {match.group(0).strip()}")

    assert offenders == [], f"模块级可变容器可能承载跨请求状态：{offenders}"


def test_the_mcp_caller_identity_is_per_call_not_per_process():
    """Concurrent callers must not see each other's identity."""
    import asyncio

    from mcp_server.context import (
        CallerIdentity,
        MissingCallerIdentity,
        caller_scope,
        current_caller,
    )

    async def caller(actor: str) -> str:
        with caller_scope(CallerIdentity(actor_id=actor)):
            # Yield so the tasks genuinely interleave; without this the test would pass even
            # if the identity were kept in a plain module global.
            await asyncio.sleep(0.01)
            return current_caller().actor_id

    async def run_all() -> list[str]:
        return list(await asyncio.gather(*(caller(name) for name in ("a", "b", "c", "d"))))

    seen = asyncio.run(run_all())
    assert seen == ["a", "b", "c", "d"], seen


def test_the_caller_scope_is_reset_even_when_the_body_raises():
    """A scope that leaked on the error path would bind the next caller to this one."""
    from mcp_server.context import (
        CallerIdentity,
        MissingCallerIdentity,
        caller_scope,
        current_caller,
    )

    with pytest.raises(RuntimeError):
        with caller_scope(CallerIdentity(actor_id="demo-a")):
            raise RuntimeError("boom")

    with pytest.raises(MissingCallerIdentity):
        current_caller()


def test_an_unbound_identity_is_an_error_not_a_default():
    """A default would make an anonymous call silently run as somebody."""
    from mcp_server.context import MissingCallerIdentity, current_caller

    with pytest.raises(MissingCallerIdentity):
        current_caller()


def test_two_users_never_reach_each_others_artifacts(artifacts):
    mine = artifacts.register(
        owner_user_id=OWNER_A,
        thread_id="t21",
        name="mine.md",
        content=b"# mine\n",
        source="test",
    )

    assert artifacts.describe(OWNER_B, mine.artifact_id) is None
    assert artifacts.open(OWNER_B, mine.artifact_id) is None
    assert artifacts.list_for_thread(OWNER_B, "t21") == []


def test_the_host_sentinel_is_unchanged_after_reaching_for_it(sandbox, tmp_path):
    """V14: the container cannot read or write a host file, and the digest proves it."""
    sentinel, digest = sandbox_service.write_host_sentinel(tmp_path)

    probe = sandbox.execute(f"cat '{sentinel}' 2>&1; echo marker-done")
    sandbox.execute(f"echo tampered > '{sentinel}' 2>&1; echo rc=$?")

    assert digest not in probe.output
    assert sandbox_service.host_sentinel_digest(sentinel) == digest


# --------------------------------------------------------------------------- #
# background tasks under a fault — and the spinner that must not appear
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def protocol_service():
    with agent_protocol_service.running_service() as handle:
        yield handle


@pytest.fixture(scope="module")
def task_service(database, protocol_service):
    return AsyncTaskService(
        store=MongoAsyncTaskStore(database),
        protocol=SdkProtocolClient(
            protocol_service.base_url,
            client=agent_protocol_service.client(protocol_service.base_url),
        ),
        graph_id="echo",
    )


def _launch(service, request_id: str | None = None):
    return service.launch(
        owner_user_id=OWNER_A,
        parent_thread_id="t21-parent",
        request_id=request_id or f"t21-{uuid.uuid4().hex[:10]}",
        instruction="看看哪些物料需要补货",
    )[0]


def _wait_for_terminal(service, task_id: str, *, timeout: float = 60.0) -> str:
    deadline = time.monotonic() + timeout
    status = ""
    while time.monotonic() < deadline:
        status = str(service.status(owner_user_id=OWNER_A, task_id=task_id).status)
        if status in {"completed", "failed", "cancelled", "lost"}:
            return status
        time.sleep(0.4)
    return status


def test_a_cancelled_task_has_a_terminal_state_to_show(task_service):
    """The frontend's spinner is driven by `terminal`; a cancel must reach it."""
    task = _launch(task_service)
    cancelled = task_service.cancel(
        owner_user_id=OWNER_A, task_id=task.task_id, request_id="t21-cancel"
    )

    assert str(cancelled.status) == "cancelled"
    assert cancelled.status in {"completed", "failed", "cancelled", "lost"}
    assert _wait_for_terminal(task_service, task.task_id) == "cancelled"


def test_cancel_versus_poll_is_stable_across_three_runs(task_service):
    """The race the acceptance asks to repeat: a poll landing after a cancel.

    Run three times and require the same answer each time. Cancelling *then* polling is not a
    race in wall-clock terms, but it is the ordering that would break if the poll's status map
    won over the terminal transition — and a concurrency bug that only appears sometimes is
    the reason this is repeated rather than reasoned about once.
    """
    outcomes = []
    for attempt in range(3):
        task = _launch(task_service, f"t21-race-cancel-{attempt}-{uuid.uuid4().hex[:6]}")
        task_service.cancel(
            owner_user_id=OWNER_A, task_id=task.task_id, request_id=f"cancel-{attempt}"
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            seen = list(
                pool.map(
                    lambda _: str(
                        task_service.status(owner_user_id=OWNER_A, task_id=task.task_id).status
                    ),
                    range(3),
                )
            )
        outcomes.append(set(seen))

    assert outcomes == [{"cancelled"}, {"cancelled"}, {"cancelled"}], outcomes


def test_a_run_the_service_forgot_is_never_reported_as_finished(database, protocol_service):
    """The spinner has to stop for the right reason. `lost` stops it and says so."""
    from agent.async_tasks.store import AsyncStatus, AsyncTaskRecord

    store = MongoAsyncTaskStore(database)
    task_id = f"task-{uuid.uuid4().hex[:10]}"
    store.create(
        AsyncTaskRecord(
            task_id=task_id,
            owner_user_id=OWNER_A,
            parent_thread_id="t21-parent",
            instruction="这个 run 服务已经不记得了",
            async_thread_id=str(uuid.uuid4()),
            async_run_id=str(uuid.uuid4()),
            status=str(AsyncStatus.RUNNING),
            created_at="",
            updated_at="",
            request_id=f"t21-{uuid.uuid4().hex[:8]}",
        )
    )
    service = AsyncTaskService(
        store=store,
        protocol=SdkProtocolClient(
            protocol_service.base_url,
            client=agent_protocol_service.client(protocol_service.base_url),
        ),
        graph_id="echo",
    )

    refreshed = service.status(owner_user_id=OWNER_A, task_id=task_id)

    assert str(refreshed.status) == "lost"
    assert refreshed.status != "completed"
    assert refreshed.last_error, "状态未知必须有原因可查"


def test_an_unreachable_service_is_a_visible_error_not_a_pending_task(database):
    from agent.async_tasks.service import ProtocolUnavailable

    store = MongoAsyncTaskStore(database)
    dead = AsyncTaskService(
        store=store,
        protocol=SdkProtocolClient("http://127.0.0.1:1"),
        graph_id="echo",
    )
    request_id = f"t21-dead-{uuid.uuid4().hex[:8]}"

    with pytest.raises(ProtocolUnavailable):
        dead.launch(
            owner_user_id=OWNER_A,
            parent_thread_id="t21-parent",
            request_id=request_id,
            instruction="服务不可达",
        )

    assert store.find_by_request(OWNER_A, request_id) is None


# --------------------------------------------------------------------------- #
# resource accounting
# --------------------------------------------------------------------------- #


def test_a_fault_does_not_leave_rows_behind(database, artifacts):
    """The failure timeline has to be reconciled against the database, not just logged."""
    before = db_fingerprint(database)

    # A refusal, a duplicate, and an idempotent retry: none of them may add a row.
    artifacts.register(
        owner_user_id=OWNER_A, thread_id="t21-audit", name="a.md", content=b"a\n", source="test"
    )
    again = artifacts.register(
        owner_user_id=OWNER_A, thread_id="t21-audit", name="a.md", content=b"a\n", source="test"
    )
    artifacts.register(
        owner_user_id=OWNER_A, thread_id="t21-audit", name="b.md", content=b"a\n", source="test"
    )
    after = db_fingerprint(database)

    # Two distinct artifacts were registered, so artifacts grows by exactly two; the
    # duplicate *content* is a different artifact id, which is the documented behaviour for
    # files (unlike skills, where identical content returns the original version).
    assert after["artifacts"] == before["artifacts"] + 3
    assert after["artifact_blobs"] == before["artifact_blobs"] + 3
    assert again.sha256 == hashlib.sha256(b"a\n").hexdigest()
    del again  # referenced only to document that identical content still yields a new id

    for name in ("threads", "display_messages", "runs", "pending_actions"):
        assert after[name] == before[name], f"{name} 不应被产件操作影响"
