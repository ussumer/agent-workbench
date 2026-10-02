"""Per-call caller identity for the MCP gateway.

Identity arrives on the MCP request's HTTP headers and is held in a
:class:`contextvars.ContextVar` for exactly the duration of one tool call. That
is what keeps two concurrent users from crossing: nothing is written onto the
shared HTTP client, so there is no global "current user" to leak.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass

ACTOR_HEADER = "x-actor-id"
GRANT_HEADER = "x-approval-grant"


class MissingCallerIdentity(RuntimeError):
    """Raised when a tool call arrives without a trusted actor."""


@dataclass(frozen=True)
class CallerIdentity:
    """Who is calling, and with which approval grant (writes only)."""

    actor_id: str
    grant: str | None = None


_current: ContextVar[CallerIdentity | None] = ContextVar("mcp_caller_identity", default=None)


def identity_from_headers(headers: Mapping[str, str]) -> CallerIdentity:
    """Read the trusted actor (and optional grant) from request headers.

    Header lookup is case-insensitive because HTTP header casing is not stable.
    """
    lowered = {str(key).lower(): value for key, value in headers.items()}
    actor = (lowered.get(ACTOR_HEADER) or "").strip()
    if not actor:
        raise MissingCallerIdentity(
            f"{ACTOR_HEADER} is required: the assistant's identity comes from the runtime, "
            "never from a tool argument"
        )
    grant = (lowered.get(GRANT_HEADER) or "").strip() or None
    return CallerIdentity(actor_id=actor, grant=grant)


@contextlib.contextmanager
def caller_scope(identity: CallerIdentity) -> Iterator[CallerIdentity]:
    token = _current.set(identity)
    try:
        yield identity
    finally:
        _current.reset(token)


def current_caller() -> CallerIdentity:
    identity = _current.get()
    if identity is None:
        raise MissingCallerIdentity("no caller identity is bound to this tool call")
    return identity
