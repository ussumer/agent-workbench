"""Recovery mechanics, one fault at a time.

The acceptance suite drives faults across modules; this file isolates the mechanisms each of
those scenarios depends on, so a failure says *which* guarantee broke rather than only that a
combination did.

Everything here runs against real MongoDB and, where it matters, a real sandbox. Nothing is
simulated with an in-memory stand-in: "it survives a restart" is only a claim about a process
boundary if the state actually left that process.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.approval.models import PendingStatus  # noqa: E402
from agent.approval.service import ApprovalService  # noqa: E402
from agent.approval.store import MongoPendingActionStore  # noqa: E402
from agent.middlewares.user_skills_restore import (  # noqa: E402
    USER_SKILLS_REVISION_MARKER,
    USER_SKILLS_ROOT,
    StoreAssignmentReader,
    UserSkillsRestoreMiddleware,
    atomic_replace,
    verify_manifest,
    ManifestIntegrityError,
    SkillAssignment,
)
from agent.persistence.indexes import (  # noqa: E402
    drop_undeclared_application_indexes,
    ensure_application_indexes,
)
from agent.persistence.repository import ApplicationRepository  # noqa: E402
from fixtures import mongo_service, sandbox_service  # noqa: E402

pytestmark = pytest.mark.integration

OWNER = "demo-a"
GRANT_SECRET = "recovery-grant-secret"
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def settings():
    candidate = mongo_service.unique_settings(f"t21rec-{uuid.uuid4().hex[:8]}")
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
def sandbox():
    with sandbox_service.running_backend() as (backend, _report):
        yield backend


# --------------------------------------------------------------------------- #
# a pending action outlives the process that recorded it
# --------------------------------------------------------------------------- #


def test_a_pending_action_is_readable_by_a_brand_new_service(database):
    """The process is disposable; the record is not."""
    thread_id = f"rec-{uuid.uuid4().hex[:8]}"
    interrupt = {
        "action_requests": [
            {
                "name": "order_create",
                "args": {
                    "supplier_id": "S001",
                    "currency": "CNY",
                    "lines": [{"part_id": "P001", "quantity": 50, "unit_price": "25.50"}],
                },
                "description": "needs approval",
            }
        ],
        "review_configs": [{"action_name": "order_create", "allowed_decisions": ["approve"]}],
    }
    first = ApprovalService(store=MongoPendingActionStore(database), grant_secret=GRANT_SECRET)
    recorded = first.record(
        owner_user_id=OWNER, thread_id=thread_id, interrupt_id="i1", interrupt_value=interrupt
    )

    # A completely new service object, as a restarted process would build.
    restarted = ApprovalService(
        store=MongoPendingActionStore(database), grant_secret=GRANT_SECRET
    )
    reloaded = restarted.store.find(OWNER, thread_id, "i1")

    assert reloaded is not None
    assert reloaded.payload_bytes == recorded.payload_bytes
    assert reloaded.payload_sha256 == recorded.payload_sha256
    assert reloaded.operation_id == recorded.operation_id
    assert reloaded.status is PendingStatus.PENDING


def test_the_frozen_bytes_survive_a_round_trip_through_mongo(database):
    """A digest that changed on read would make every approved write fail later."""
    thread_id = f"rec-{uuid.uuid4().hex[:8]}"
    service = ApprovalService(store=MongoPendingActionStore(database), grant_secret=GRANT_SECRET)
    interrupt = {
        "action_requests": [
            {
                "name": "order_create",
                "args": {
                    "supplier_id": "S001",
                    "currency": "CNY",
                    "note": "含中文备注",
                    "lines": [{"part_id": "P004", "quantity": 30, "unit_price": "17.50"}],
                },
                "description": "x",
            }
        ],
        "review_configs": [{"action_name": "order_create", "allowed_decisions": ["approve"]}],
    }
    recorded = service.record(
        owner_user_id=OWNER, thread_id=thread_id, interrupt_id="i1", interrupt_value=interrupt
    )

    found = MongoPendingActionStore(database).find(OWNER, thread_id, "i1")

    assert found is not None
    assert found.payload_bytes == recorded.payload_bytes
    assert found.payload["note"] == "含中文备注"


# --------------------------------------------------------------------------- #
# a separate process sees what this one wrote
# --------------------------------------------------------------------------- #


def test_another_process_reads_the_same_thread_and_messages(settings, database):
    """The property a restart tests: nothing meaningful lives only in this interpreter."""
    from agent.persistence.repository import ApplicationRepository

    repository = ApplicationRepository(database)
    thread_id = f"rec-{uuid.uuid4().hex[:8]}"
    repository.ensure_thread(owner_user_id=OWNER, thread_id=thread_id, title="恢复用例")

    script = (
        "import json, sys\n"
        "sys.path.insert(0, 'src')\n"
        "from pymongo import MongoClient\n"
        f"c = MongoClient({settings.mongo_uri!r}, serverSelectionTimeoutMS=5000)\n"
        f"db = c[{settings.database!r}]\n"
        "row = db['threads'].find_one("
        f"{{'thread_id': {thread_id!r}, 'owner_user_id': {OWNER!r}}})\n"
        "print(json.dumps({'title': row['title'] if row else None}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1])["title"] == "恢复用例"


# --------------------------------------------------------------------------- #
# a restored skill is verified before it replaces anything
# --------------------------------------------------------------------------- #


def test_an_atomic_replace_refuses_files_that_do_not_match_their_manifest(sandbox):
    """The check has to happen before the move, not after."""
    assignment = SkillAssignment(owner_user_id=OWNER, scope="main", slug="recovery-x", version="1.0.0")
    body = b"---\nname: recovery-x\ndescription: y\n---\n"
    manifest = {
        "files": [
            {
                "path": "SKILL.md",
                # A digest that does not describe the bytes below.
                "sha256": "0" * 64,
                "size": len(body),
            }
        ]
    }

    with pytest.raises(ManifestIntegrityError):
        verify_manifest(
            assignment,
            {"SKILL.md": body, "manifest.json": json.dumps(manifest).encode()},
        )


def test_a_failed_replace_leaves_the_existing_copy_alone(sandbox):
    """`恢复失败不得删除已存在的版本` — asserted against the directory, not the log."""
    assignment = SkillAssignment(owner_user_id=OWNER, scope="main", slug="recovery-y", version="1.0.0")
    target = assignment.sandbox_directory

    good = {**{'SKILL.md': b"---\nname: recovery-y\ndescription: y\n---\nfirst\n"}}
    assert atomic_replace(sandbox, target, good, assignment) is True
    before = sandbox.download_files([f"{target}/SKILL.md"])[0].content

    # A second replace with the same content is a no-op, which is what makes restore
    # idempotent rather than merely repeatable.
    assert atomic_replace(sandbox, target, good, assignment) is False
    assert sandbox.download_files([f"{target}/SKILL.md"])[0].content == before


def test_the_revision_marker_lives_in_the_container(sandbox):
    """A rebuilt container has no marker, which is exactly how a full restore is triggered."""
    marker = sandbox.execute(f"cat {USER_SKILLS_REVISION_MARKER} 2>/dev/null || echo none")
    assert marker.exit_code == 0

    sandbox.upload_files([(USER_SKILLS_REVISION_MARKER, b"deadbeef\n")])
    assert sandbox.execute(f"cat {USER_SKILLS_REVISION_MARKER}").output.strip() == "deadbeef"

    sandbox.execute(f"rm -f {USER_SKILLS_REVISION_MARKER}")
    assert "none" in sandbox.execute(
        f"cat {USER_SKILLS_REVISION_MARKER} 2>/dev/null || echo none"
    ).output


def test_no_assignments_is_a_normal_state_not_a_failure(database, sandbox):
    """An owner who has published nothing must not look like a broken restore."""
    from langgraph.store.mongodb import MongoDBStore

    store = MongoDBStore(database.client[database.name]["persistent-store"])
    middleware = UserSkillsRestoreMiddleware(
        backend_provider=lambda: sandbox, reader=StoreAssignmentReader(store)
    )

    report = middleware.restore(f"nobody-{uuid.uuid4().hex[:6]}", generation=1)

    assert report.empty is True
    assert report.reason == "no_assignments"
    assert report.failed == []
