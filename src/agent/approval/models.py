"""The pending-action record: one reviewable write, frozen before anyone approves it.

The record is the single source of truth for "what did the user actually approve". It
stores the payload as **bytes** and their SHA-256, because the approval binds to those exact
bytes: the gateway re-hashes what it sends and refuses if anything moved. Storing only the
parsed arguments would let a formatting difference silently invalidate — or worse, silently
match — the approved request.

``operation_id`` is generated once, when the action is recorded, and never regenerated. The
framework is allowed to replay a node from its start (documented behaviour under
``Command(resume=...)``), so the same write may be *attempted* more than once; it is the
stable operation id that makes the retry land on the ERP's idempotency ledger instead of
creating a second order.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Mapping, Sequence


class ApprovalError(RuntimeError):
    """An approval could not be created, resolved or used."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PendingStatus(StrEnum):
    """Lifecycle of one pending action.

    ``EXECUTING`` exists so that claiming an approved action and performing the write are
    not the same step: two concurrent resumes race on the transition into ``EXECUTING``,
    and exactly one of them wins.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"
    EXPIRED = "expired"


#: Allowed status transitions. Anything not listed is refused, which is what keeps a
#: rejected action from being quietly re-approved.
ALLOWED_TRANSITIONS: frozenset[tuple[PendingStatus, PendingStatus]] = frozenset(
    {
        (PendingStatus.PENDING, PendingStatus.APPROVED),
        (PendingStatus.PENDING, PendingStatus.REJECTED),
        (PendingStatus.PENDING, PendingStatus.EXPIRED),
        (PendingStatus.APPROVED, PendingStatus.EXECUTING),
        (PendingStatus.APPROVED, PendingStatus.EXPIRED),
        (PendingStatus.EXECUTING, PendingStatus.EXECUTED),
        (PendingStatus.EXECUTING, PendingStatus.FAILED),
    }
)

#: Statuses from which an action can still be approved or rejected.

DECIDABLE: frozenset[PendingStatus] = frozenset({PendingStatus.PENDING})


def can_transition(current: PendingStatus, target: PendingStatus) -> bool:
    return (current, target) in ALLOWED_TRANSITIONS


def freeze_payload(payload: Mapping[str, Any]) -> bytes:
    """The frozen bytes of a write request.

    Canonical form (sorted keys, no whitespace, UTF-8) and identical to what the gateway
    will send, because the same function is used on both sides.
    """
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def payload_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def new_operation_id(prefix: str = "op") -> str:
    """A stable operation id, generated once per pending action."""
    return f"{prefix}-{uuid.uuid4().hex}"


@dataclass(frozen=True)
class PendingAction:
    """One write the user has been asked to approve."""

    owner_user_id: str
    thread_id: str
    interrupt_id: str
    tool_call_id: str
    tool_name: str
    target: str
    payload_bytes: bytes
    payload_sha256: str
    operation_id: str
    status: PendingStatus = PendingStatus.PENDING
    before_snapshot: dict[str, Any] | None = None
    summary: str = ""
    created_at: str = ""
    updated_at: str = ""
    expires_at: str | None = None
    decided_at: str | None = None
    resume_request_id: str | None = None
    attempts: int = 0
    last_error: str | None = None

    # ------------------------------------------------------------------ views

    @property
    def payload(self) -> dict[str, Any]:
        """The frozen payload, decoded for display. Never used to authorise anything."""
        return json.loads(self.payload_bytes.decode("utf-8"))

    def matches_bytes(self, candidate: bytes) -> bool:
        return payload_digest(candidate) == self.payload_sha256

    def decision_allowed(self) -> bool:
        return self.status in DECIDABLE

    def as_document(self) -> dict[str, Any]:
        """Mongo document form. ``payload_bytes`` is stored as-is, not as text."""
        return {
            "owner_user_id": self.owner_user_id,
            "thread_id": self.thread_id,
            "interrupt_id": self.interrupt_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "target": self.target,
            "payload_bytes": self.payload_bytes,
            "payload_sha256": self.payload_sha256,
            "operation_id": self.operation_id,
            "status": str(self.status),
            "before_snapshot": self.before_snapshot,
            "summary": self.summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "decided_at": self.decided_at,
            "resume_request_id": self.resume_request_id,
            "attempts": self.attempts,
            "last_error": self.last_error,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> PendingAction:
        return cls(
            owner_user_id=str(document["owner_user_id"]),
            thread_id=str(document["thread_id"]),
            interrupt_id=str(document["interrupt_id"]),
            tool_call_id=str(document["tool_call_id"]),
            tool_name=str(document["tool_name"]),
            target=str(document.get("target") or ""),
            payload_bytes=bytes(document["payload_bytes"]),
            payload_sha256=str(document["payload_sha256"]),
            operation_id=str(document["operation_id"]),
            status=PendingStatus(document["status"]),
            before_snapshot=document.get("before_snapshot"),
            summary=str(document.get("summary") or ""),
            created_at=str(document.get("created_at") or ""),
            updated_at=str(document.get("updated_at") or ""),
            expires_at=document.get("expires_at"),
            decided_at=document.get("decided_at"),
            resume_request_id=document.get("resume_request_id"),
            attempts=int(document.get("attempts") or 0),
            last_error=document.get("last_error"),
        )

    def with_status(self, status: PendingStatus, **changes: Any) -> PendingAction:
        """A copy in a new status, refusing illegal transitions."""
        if not can_transition(self.status, status):
            raise ApprovalError(
                "ILLEGAL_TRANSITION",
                f"pending action {self.operation_id} cannot move from "
                f"{self.status} to {status}",
            )
        return replace(self, status=status, **changes)


@dataclass(frozen=True)
class ActionSummary:
    """The reviewable view handed to the API and the UI.

    Deliberately excludes ``payload_bytes`` internals that do not belong in a prompt: the
    grant, the operation id and the actor are never part of what the model or SSE sees.
    """

    interrupt_id: str
    tool_name: str
    target: str
    status: str
    summary: str
    payload: dict[str, Any]
    before_snapshot: dict[str, Any] | None = None
    suggested_total: str | None = None
    lines: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "interrupt_id": self.interrupt_id,
            "tool_name": self.tool_name,
            "target": self.target,
            "status": self.status,
            "summary": self.summary,
            "payload": self.payload,
            "before_snapshot": self.before_snapshot,
            "suggested_total": self.suggested_total,
            "lines": self.lines,
        }


def _dec(value: str, places: int = 2) -> str:
    """Two-decimal string arithmetic, via integers so no float ever appears."""
    whole, _, fraction = value.partition(".")
    fraction = (fraction + "0" * places)[:places]
    return f"{whole}.{fraction}"


def total_amount(lines: Sequence[Mapping[str, Any]]) -> str:
    """Line total from quantity × unit_price, in integer cents.

    Written here rather than in the prompt so the number the user approves is the number
    the server computed, not one the model reported.
    """
    cents = 0
    for line in lines:
        price_cents = int(round(float(_dec(str(line["unit_price"]))) * 100))
        cents += int(line["quantity"]) * price_cents
    return f"{cents // 100}.{cents % 100:02d}"


def summarise(tool_name: str, payload: Mapping[str, Any]) -> tuple[str, str | None, list[dict]]:
    """A human-readable summary plus the per-line breakdown shown at approval."""
    lines = [dict(line) for line in payload.get("lines") or []]
    for line in lines:
        price = _dec(str(line.get("unit_price", "0.00")))
        quantity = int(line.get("quantity", 0))
        price_cents = int(round(float(price) * 100))
        amount_cents = price_cents * quantity
        line["unit_price"] = price
        line["line_amount"] = f"{amount_cents // 100}.{amount_cents % 100:02d}"

    total = total_amount(lines) if lines else None
    verb = "创建订单" if tool_name == "order_create" else "修改订单"
    supplier = payload.get("supplier_id", "?")
    summary = f"{verb}：供应商 {supplier}，{len(lines)} 行，合计 {total or '0.00'} {payload.get('currency', 'CNY')}"
    return summary, total, lines


__all__ = [
    "ALLOWED_TRANSITIONS",
    "DECIDABLE",
    "ActionSummary",
    "ApprovalError",
    "PendingAction",
    "PendingStatus",
    "can_transition",
    "freeze_payload",
    "new_operation_id",
    "payload_digest",
    "summarise",
    "total_amount",
]
