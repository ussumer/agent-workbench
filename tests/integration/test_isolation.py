"""Isolation mechanics, one boundary at a time.

The acceptance suite checks that two users cannot reach each other's things; this file checks
*where* that is enforced, so a regression shows up as a specific layer failing rather than as
a vague leak. Every assertion is against the mechanism the application actually depends on:
the Store key namespace, the owner-scoped backend, and the MCP caller's identity.

None of it is enforced by a prompt. That distinction is the whole point of the file: the
contract says "不能靠提示词限制读取", so each layer below is asked directly, without a model
in the loop.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.main_agent import (  # noqa: E402
    MEMORIES_ROOT,
    PERSISTED_SKILLS_ROOT,
    SKILLS_ROOT,
    build_virtual_backend,
    owner_key_prefix,
)
from agent.schema import is_within  # noqa: E402
from agent.persistence.namespaces import (  # noqa: E402
    NamespaceViolation,
    assert_key_allowed,
    assert_namespace_allowed,
    key_belongs_to_user,
    skills_namespace,
    user_skill_key,
)
from fixtures import mongo_service, sandbox_service  # noqa: E402

pytestmark = pytest.mark.integration

OWNER_A = "demo-a"
OWNER_B = "demo-b"


@pytest.fixture(scope="module")
def settings():
    candidate = mongo_service.unique_settings(f"t21iso-{uuid.uuid4().hex[:8]}")
    mongo_service.require_reachable(candidate)
    mongo_service.drop_test_database(candidate)
    yield candidate
    mongo_service.drop_test_database(candidate)


@pytest.fixture(scope="module")
def store(settings):
    from langgraph.store.mongodb import MongoDBStore
    from pymongo import MongoClient

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    yield MongoDBStore(client[settings.database][settings.store_collection])
    client.close()


@pytest.fixture(scope="module")
def sandbox():
    with sandbox_service.running_backend() as (backend, _report):
        yield backend


@pytest.fixture(scope="module")
def virtual_a(sandbox, store):
    return build_virtual_backend(sandbox_backend=sandbox, store=store, owner_user_id=OWNER_A)


@pytest.fixture(scope="module")
def virtual_b(sandbox, store):
    return build_virtual_backend(sandbox_backend=sandbox, store=store, owner_user_id=OWNER_B)


# --------------------------------------------------------------------------- #
# the key namespace
# --------------------------------------------------------------------------- #


def test_a_skill_key_is_built_under_its_owner():
    """The Store key carries a leading slash; ``owner_key_prefix`` does not.

    Both forms exist in the codebase and mean the same owner, so the assertion is written
    against ``user_skill_prefix`` — the function that actually builds the key — rather than
    against a hand-written string that could drift from it.
    """
    from agent.persistence.namespaces import user_skill_prefix

    key = user_skill_key(OWNER_A, "main", "some-skill", "1.0.0", "SKILL.md")

    assert key.startswith(f"{user_skill_prefix(OWNER_A)}/")
    assert owner_key_prefix(OWNER_A) in key
    assert key_belongs_to_user(key, OWNER_A) is True
    assert key_belongs_to_user(key, OWNER_B) is False


def test_a_key_from_another_user_is_refused_by_the_namespace_check():
    key = user_skill_key(OWNER_A, "main", "some-skill", "1.0.0", "SKILL.md")

    assert_key_allowed(skills_namespace(), key, OWNER_A)
    with pytest.raises(NamespaceViolation):
        assert_key_allowed(skills_namespace(), key, OWNER_B)


def test_a_memories_namespace_is_only_ever_the_callers_own():
    from agent.persistence.namespaces import memories_namespace

    assert memories_namespace(OWNER_A) == ("memories", OWNER_A)
    assert memories_namespace(OWNER_A) != memories_namespace(OWNER_B)

    assert_namespace_allowed(memories_namespace(OWNER_B), OWNER_B)
    with pytest.raises(NamespaceViolation):
        assert_namespace_allowed(memories_namespace(OWNER_B), OWNER_A)


def test_a_bad_slug_cannot_be_smuggled_into_a_key():
    """A slug is part of a path, so a traversal in one would escape the owner's prefix."""
    with pytest.raises(NamespaceViolation):
        user_skill_key(OWNER_A, "main", "../other", "1.0.0", "SKILL.md")


# --------------------------------------------------------------------------- #
# the virtual filesystem
# --------------------------------------------------------------------------- #


def test_the_store_mounts_are_scoped_to_the_owner(virtual_a, virtual_b):
    virtual_a.write(f"{MEMORIES_ROOT}/note.md", "owner A's note")
    virtual_b.write(f"{MEMORIES_ROOT}/note.md", "owner B's note")

    assert "owner A" in virtual_a.read(f"{MEMORIES_ROOT}/note.md").file_data["content"]
    assert "owner B" in virtual_b.read(f"{MEMORIES_ROOT}/note.md").file_data["content"]


def test_naming_another_owner_prefix_is_a_permission_error(virtual_b):
    result = virtual_b.read(f"{PERSISTED_SKILLS_ROOT}/{owner_key_prefix(OWNER_A)}/x/SKILL.md")

    assert result.error == "permission_denied"


def test_search_does_not_cross_owners(virtual_a, virtual_b):
    """A grep that ignored the owner would leak content just as thoroughly as a read."""
    virtual_a.write(f"{PERSISTED_SKILLS_ROOT}/only-a/marker.md", "unique-marker-t21-isolation")

    assert virtual_b.grep("unique-marker-t21-isolation", PERSISTED_SKILLS_ROOT).matches == []
    assert virtual_a.grep("unique-marker-t21-isolation", PERSISTED_SKILLS_ROOT).matches


def test_the_workspace_route_reaches_the_container_not_the_store(sandbox, virtual_a):
    """Three mounts, three backends: a shared root would make isolation decorative."""
    virtual_a.write("/workspace/scratch/isolation.txt", "in the container")

    assert "in the container" in sandbox.read("/workspace/scratch/isolation.txt").file_data["content"]
    assert "No such file" in sandbox.execute("ls -d /memories 2>&1").output


def test_the_path_helper_does_not_accept_a_sibling_with_a_shared_prefix():
    """`/skills-other` starts with `/skills` as a string and is not inside it."""
    assert is_within("/skills/users/demo-a/x", (SKILLS_ROOT,)) is True
    assert is_within("/skills-other/x", (SKILLS_ROOT,)) is False
    assert is_within("skills/x", (SKILLS_ROOT,)) is False


# --------------------------------------------------------------------------- #
# two owners at once
# --------------------------------------------------------------------------- #


def test_two_owners_writing_concurrently_never_cross(sandbox, store):
    """The same backend object, interleaved from two threads, with different owners."""
    a = build_virtual_backend(sandbox_backend=sandbox, store=store, owner_user_id=OWNER_A)
    b = build_virtual_backend(sandbox_backend=sandbox, store=store, owner_user_id=OWNER_B)
    run = uuid.uuid4().hex[:8]

    import concurrent.futures

    def write_a(index: int) -> None:
        a.write(f"{MEMORIES_ROOT}/concurrent-{run}.md", f"a-{index}")

    def write_b(index: int) -> None:
        b.write(f"{MEMORIES_ROOT}/concurrent-{run}.md", f"b-{index}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(write_a if index % 2 else write_b, index) for index in range(8)
        ]
        for future in futures:
            future.result()

    content_a = a.read(f"{MEMORIES_ROOT}/concurrent-{run}.md").file_data["content"]
    content_b = b.read(f"{MEMORIES_ROOT}/concurrent-{run}.md").file_data["content"]

    assert content_a.startswith("a-"), content_a
    assert content_b.startswith("b-"), content_b
    assert content_a != content_b


def test_an_unnamed_or_malformed_owner_is_refused_rather_than_defaulted():
    """There is no "current user" to fall back to, and a slash in an owner would escape.

    A single ``"."`` is *accepted* by this layer, which is worth stating rather than
    assuming: the check rejects an empty owner and one containing ``/``, and nothing else.
    That is sufficient because owners are drawn from a fixed server-side list (``DEMO_USERS``)
    and never come from a request — the namespace layer is the last line, not the only one.
    """
    from agent.persistence.namespaces import memories_namespace

    for bad in ("", "   ", "a/b", "demo-a/"):
        with pytest.raises(NamespaceViolation):
            memories_namespace(bad)


# --------------------------------------------------------------------------- #
# the MCP caller
# --------------------------------------------------------------------------- #


def test_the_mcp_identity_is_bound_for_one_call_only():
    from mcp_server.context import (
        CallerIdentity,
        MissingCallerIdentity,
        caller_scope,
        current_caller,
    )

    with pytest.raises(MissingCallerIdentity):
        current_caller()

    with caller_scope(CallerIdentity(actor_id=OWNER_A)):
        assert current_caller().actor_id == OWNER_A

    with pytest.raises(MissingCallerIdentity):
        current_caller()


def test_concurrent_mcp_callers_do_not_see_each_other():
    from mcp_server.context import CallerIdentity, caller_scope, current_caller

    async def caller(actor: str) -> str:
        with caller_scope(CallerIdentity(actor_id=actor)):
            await asyncio.sleep(0.005)
            return current_caller().actor_id

    async def run_all() -> list[str]:
        return list(await asyncio.gather(*(caller(name) for name in (OWNER_A, OWNER_B))))

    assert asyncio.run(run_all()) == [OWNER_A, OWNER_B]


def test_headers_are_read_case_insensitively_and_an_absent_actor_is_refused():
    from mcp_server.context import MissingCallerIdentity, identity_from_headers

    assert identity_from_headers({"X-Actor-Id": OWNER_A}).actor_id == OWNER_A
    assert identity_from_headers({"x-actor-id": OWNER_B}).actor_id == OWNER_B

    with pytest.raises(MissingCallerIdentity):
        identity_from_headers({})
    with pytest.raises(MissingCallerIdentity):
        identity_from_headers({"x-actor-id": "   "})


def test_a_grant_travels_with_the_identity_not_in_it():
    from mcp_server.context import CallerIdentity, identity_from_headers

    identity = identity_from_headers({"x-actor-id": OWNER_A, "x-approval-grant": "v1.x.y"})

    assert identity == CallerIdentity(actor_id=OWNER_A, grant="v1.x.y")
    assert json.dumps({"actor": identity.actor_id}) == '{"actor": "demo-a"}'
