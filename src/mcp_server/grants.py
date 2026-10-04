"""Signed approval grants for write tools.

A grant binds five things together:

* the owner it was issued for,
* the tool it authorises,
* the operation id,
* the SHA-256 of the exact frozen request body,
* an expiry.

The gateway verifies the signature and every binding before forwarding a write to
the ERP. Approval is therefore enforced at the point of execution, not merely
described in a prompt.

The API service that records approvals issues grants with :func:`issue_grant`
from this module, so the wire format has exactly one definition.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

PREFIX = "v1"
DEFAULT_TTL_SECONDS = 300


class GrantError(RuntimeError):
    """A grant was absent, malformed, expired or did not match this call."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Grant:
    owner: str
    tool: str
    target: str
    operation_id: str
    payload_sha256: str
    issued_at: int
    expires_at: int
    approval_ref: dict[str, str] | None = None


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(secret: str, payload_b64: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return _b64url_encode(digest)


def issue_grant(
    *,
    secret: str,
    owner: str,
    tool: str,
    target: str,
    operation_id: str,
    payload_sha256: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: int | None = None,
    approval_ref: dict[str, str] | None = None,
) -> str:
    """Mint a grant. Called by the approval service once a user approves a frozen payload."""
    issued_at = int(time.time() if now is None else now)
    payload = {
        "owner": owner,
        "tool": tool,
        "target": target,
        "operation_id": operation_id,
        "payload_sha256": payload_sha256,
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
    }
    if approval_ref is not None:
        payload["approval_ref"] = approval_ref
    encoded = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return f"{PREFIX}.{encoded}.{_sign(secret, encoded)}"


def verify_grant(
    token: str | None,
    *,
    secret: str,
    expect_owner: str,
    expect_tool: str,
    expect_target: str,
    expect_payload_sha256: str,
    now: int | None = None,
) -> Grant:
    """Verify a grant for one specific call, or raise :class:`GrantError`."""
    if not token:
        raise GrantError(
            "APPROVAL_REQUIRED",
            "write tools require an approval grant; approve the frozen action first",
        )

    parts = token.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise GrantError("INVALID_GRANT", "grant is malformed")

    _, payload_b64, signature = parts
    if not hmac.compare_digest(_sign(secret, payload_b64), signature):
        raise GrantError("INVALID_GRANT", "grant signature does not verify")

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise GrantError("INVALID_GRANT", f"grant payload is unreadable: {exc}") from exc

    try:
        grant = Grant(
            owner=str(payload["owner"]),
            tool=str(payload["tool"]),
            target=str(payload["target"]),
            operation_id=str(payload["operation_id"]),
            payload_sha256=str(payload["payload_sha256"]),
            issued_at=int(payload["iat"]),
            expires_at=int(payload["exp"]),
            approval_ref=payload.get("approval_ref"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GrantError("INVALID_GRANT", f"grant is missing fields: {exc}") from exc

    if grant.approval_ref is not None and (
        not isinstance(grant.approval_ref, dict)
        or set(grant.approval_ref) != {"thread_id", "interrupt_id"}
        or not all(isinstance(v, str) and v for v in grant.approval_ref.values())
    ):
        raise GrantError("INVALID_GRANT", "invalid planning approval reference")

    moment = int(time.time() if now is None else now)
    if moment >= grant.expires_at:
        raise GrantError("GRANT_EXPIRED", "grant has expired; ask the user to approve again")

    if grant.owner != expect_owner:
        raise GrantError(
            "GRANT_MISMATCH", "grant belongs to a different user and cannot be reused"
        )
    if grant.tool != expect_tool:
        raise GrantError("GRANT_MISMATCH", f"grant authorises {grant.tool}, not {expect_tool}")
    if grant.target != expect_target:
        raise GrantError(
            "GRANT_MISMATCH",
            f"grant targets {grant.target}, not {expect_target}",
        )
    if not hmac.compare_digest(grant.payload_sha256, expect_payload_sha256):
        raise GrantError(
            "GRANT_MISMATCH",
            "grant was issued for different parameters; changed parameters need a new approval",
        )
    return grant
