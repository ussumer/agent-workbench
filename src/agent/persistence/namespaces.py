"""Store namespace layout and per-user key isolation.

Two rules from docs/plan/contracts/storage-sandbox.md:

* Preferences live in a namespace that *contains* the user id — ``('memories',
  user_id)`` — so the filename is never the only thing separating two users.
* The course's logical ``('skills',)`` namespace is preserved, and user-created
  skills are isolated by key prefix inside it:
  ``/users/{user_id}/{scope}/{slug}/{version}/...``.

Isolation is enforced here, in code, because "the prompt says not to read other
users' files" is not a security boundary.
"""

from __future__ import annotations

import re

MEMORIES_ROOT = "memories"
SKILLS_ROOT = "skills"

#: Agent-facing virtual paths that route to the Store (see the file routing table).
MEMORIES_VIRTUAL_PATH = "/memories"
PERSISTED_SKILLS_VIRTUAL_PATH = "/persisted-skills"

SKILL_SCOPE_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class NamespaceViolation(PermissionError):
    """A caller tried to reach outside its own namespace or key prefix."""


def memories_namespace(user_id: str) -> tuple[str, ...]:
    """Namespace for one user's long-term preferences."""
    _require_user(user_id)
    return (MEMORIES_ROOT, user_id)


def skills_namespace() -> tuple[str, ...]:
    """The course's logical skills namespace, shared by all users for the prefix."""
    return (SKILLS_ROOT,)


def memory_key(relative: str) -> str:
    """Normalise a preference file key inside the user's memories namespace.

    A leading slash is *accepted and stripped*. It used to be refused, on the reading that
    "relative" is an invariant worth enforcing; it is not written down anywhere — not in the
    contract, not in a test — and the refusal was reachable by the framework itself. The
    memories mount is at ``/memories/``, and ``CompositeBackend`` strips the mount and hands
    the backend ``/preferences.md``, leading slash included. So a model writing to the mount's
    own documented path killed the run with ``NamespaceViolation`` (found by T23, D06 #1/#2).

    A key cannot leave its namespace by starting with a slash — the namespace is fixed by the
    mount, not derived from the key — so the checks that carry weight are the ones below:
    no traversal and not empty.
    """
    if not relative or not relative.strip():
        raise NamespaceViolation("memory key must not be empty")
    parts = [part for part in relative.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise NamespaceViolation(f"memory key must not traverse, got {relative!r}")
    if not parts:
        raise NamespaceViolation(f"memory key must not be empty, got {relative!r}")
    return "/".join(parts)


def user_skill_prefix(user_id: str, scope: str | None = None) -> str:
    """Key prefix that isolates one user's published skills."""
    _require_user(user_id)
    if scope is None:
        return f"/users/{user_id}"
    _require_scope(scope)
    return f"/users/{user_id}/{scope}"


def user_skill_key(
    user_id: str, scope: str, slug: str, version: str, relative: str = ""
) -> str:
    """Absolute Store key for one file of one published skill version."""
    if not SKILL_SCOPE_PATTERN.match(slug):
        raise NamespaceViolation(f"invalid skill slug {slug!r}")
    base = f"{user_skill_prefix(user_id, scope)}/{slug}/{version}"
    if not relative:
        return base
    return f"{base}/{memory_key(relative)}"


def key_belongs_to_user(key: str, user_id: str) -> bool:
    """Whether a skills-namespace key is inside this user's prefix."""
    if not key.startswith("/"):
        return False
    return key == f"/users/{user_id}" or key.startswith(f"/users/{user_id}/")


def assert_namespace_allowed(namespace: tuple[str, ...], user_id: str) -> None:
    """Validate a namespace for a namespace-wide operation (search, list).

    There is no key to check here, so the skills namespace is allowed as a whole
    and individual keys are filtered by :func:`key_belongs_to_user` afterwards.
    """
    _require_user(user_id)
    if not namespace:
        raise NamespaceViolation("namespace must not be empty")

    root = namespace[0]
    if root == MEMORIES_ROOT:
        if tuple(namespace) != memories_namespace(user_id):
            raise NamespaceViolation(
                f"memories namespace {namespace} does not belong to {user_id}"
            )
        return
    if root == SKILLS_ROOT:
        return
    raise NamespaceViolation(f"unsupported namespace root {root!r}")


def assert_key_allowed(namespace: tuple[str, ...], key: str, user_id: str) -> None:
    """Raise unless ``key`` is reachable by ``user_id`` in ``namespace``.

    Unknown roots are rejected outright: a typo must not silently create a
    world-readable namespace.
    """
    assert_namespace_allowed(namespace, user_id)

    if namespace[0] == MEMORIES_ROOT:
        memory_key(key)
        return

    if not key_belongs_to_user(key, user_id):
        raise NamespaceViolation(
            f"skills key {key!r} is outside {user_id}'s prefix {user_skill_prefix(user_id)}"
        )


def _require_user(user_id: str) -> None:
    if not user_id or not user_id.strip():
        raise NamespaceViolation("user_id is required; the server resolves it, not the model")
    if "/" in user_id:
        raise NamespaceViolation(f"user_id must not contain '/', got {user_id!r}")


def _require_scope(scope: str) -> None:
    if not SKILL_SCOPE_PATTERN.match(scope):
        raise NamespaceViolation(f"invalid skill scope {scope!r}")
