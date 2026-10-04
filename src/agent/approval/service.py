"""The approval service: recording, deciding and authorising one frozen write.

The flow it implements is deliberately narrow.

1. The graph stops at ``interrupt_on`` **before** the write tool runs. The interrupt's
   arguments are frozen into bytes, hashed, and stored with a freshly generated operation
   id. Nothing has been written anywhere at this point.
2. A user decision arrives through the resume endpoint. It moves the record out of
   ``PENDING`` with a conditional update, so a second click does nothing rather than
   approving twice.
3. When the graph replays and the write tool finally runs, the tool asks this service for
   an authorisation. The service re-checks that the arguments the graph is about to send
   hash to the bytes that were approved, claims the record (``APPROVED`` → ``EXECUTING``,
   one winner) and mints a grant bound to those bytes and that operation id.
4. The gateway verifies the grant and forwards the *frozen* bytes to the ERP, which
   deduplicates on the operation id.

Anything that changes the payload between steps 1 and 3 fails at step 3 with
``PARAMETERS_CHANGED``. That is the rule "changed parameters need a new approval" expressed
as a hash comparison rather than as advice.
"""

from __future__ import annotations

import calendar
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from agent.approval.models import (
    ActionSummary,
    ApprovalError,
    PendingAction,
    PendingStatus,
    new_operation_id,
    payload_digest,
    summarise,
)
from agent.approval.store import DEFAULT_APPROVAL_TTL, PendingActionStore
from mcp_server.grants import issue_grant
from mcp_server.tools.registry import (
    ORDER_CREATE,
    ORDER_UPDATE,
    OrderLineInput,
    canonical_order_body,
    canonical_update_body,
    create_target,
    update_target,
)

LOGGER = logging.getLogger("rush_harness.approval")

#: The write tools this service governs.
APPROVABLE_TOOLS: tuple[str, ...] = (ORDER_CREATE, ORDER_UPDATE)


def _now() -> datetime:
    return datetime.now(UTC)


def _epoch(moment: datetime) -> int:
    return calendar.timegm(moment.utctimetuple())


@dataclass(frozen=True)
class WriteAuthorization:
    """Everything the write tool needs to perform exactly one approved write."""

    grant: str
    operation_id: str
    payload_bytes: bytes
    action: PendingAction


def canonical_bytes(tool_name: str, arguments: Mapping[str, Any]) -> bytes:
    """The frozen bytes for a write, built by the same builder the gateway uses.

    Importing the builder rather than re-deriving the JSON here is what makes "the bytes
    the user approved" and "the bytes the ERP receives" the same bytes by construction.
    """
    lines = [
        OrderLineInput(
            part_id=str(line["part_id"]),
            quantity=int(line["quantity"]),
            unit_price=str(line["unit_price"]),
        )
        for line in arguments.get("lines") or []
    ]
    if tool_name == ORDER_CREATE:
        return canonical_order_body(
            supplier_id=str(arguments["supplier_id"]),
            currency=str(arguments.get("currency") or "CNY"),
            lines=lines,
            note=arguments.get("note") or None,
        )
    if tool_name == ORDER_UPDATE:
        return canonical_update_body(
            expected_version=int(arguments["expected_version"]),
            supplier_id=str(arguments["supplier_id"]),
            currency=str(arguments.get("currency") or "CNY"),
            lines=lines,
            note=arguments.get("note") or None,
        )
    raise ApprovalError("NOT_APPROVABLE", f"{tool_name} is not an approvable write tool")


def target_for(tool_name: str, arguments: Mapping[str, Any]) -> str:
    if tool_name == ORDER_CREATE:
        return create_target()
    if tool_name == ORDER_UPDATE:
        return update_target(str(arguments["order_id"]))
    raise ApprovalError("NOT_APPROVABLE", f"{tool_name} is not an approvable write tool")


@dataclass
class ApprovalService:
    """Records, decides and authorises pending writes."""

    store: PendingActionStore
    grant_secret: str
    grant_ttl_seconds: int = 300
    approval_ttl: timedelta = DEFAULT_APPROVAL_TTL
    clock: Callable[[], datetime] = _now
    planning_guard: Callable[..., None] | None = None

    # ------------------------------------------------------------------ recording

    def record(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        interrupt_id: str,
        interrupt_value: Mapping[str, Any],
        before_snapshot: Mapping[str, Any] | None = None,
        planning_binding: Mapping[str, Any] | None = None,
    ) -> PendingAction:
        """Freeze one interrupt into a pending action.

        A batch of ``action_requests`` is refused rather than partially handled: this demo
        reviews one write per approval, and silently approving only the first of several
        would be worse than stopping.
        """
        requests: Sequence[Mapping[str, Any]] = interrupt_value.get("action_requests") or []
        if len(requests) != 1:
            raise ApprovalError(
                "BATCH_NOT_SUPPORTED",
                f"expected exactly one action request, got {len(requests)}; "
                "batch writes are not supported in this demo",
            )

        request = requests[0]
        tool_name = str(request["name"])
        arguments = dict(request.get("args") or {})
        if tool_name not in APPROVABLE_TOOLS:
            raise ApprovalError("NOT_APPROVABLE", f"{tool_name} is not an approvable write tool")

        # The framework's HITL interrupt does not carry the tool call id, so it is recorded
        # when the caller supplies one (a direct API path can) and left empty otherwise.
        tool_call_id = str(
            request.get("tool_call_id")
            or interrupt_value.get("tool_call_id")
            or interrupt_value.get("id")
            or ""
        )
        payload = canonical_bytes(tool_name, arguments)
        summary, _total, _lines = summarise(tool_name, arguments)
        moment = self.clock()

        action = PendingAction(
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            interrupt_id=interrupt_id,
            tool_call_id=tool_call_id,
            planning_binding=dict(planning_binding) if planning_binding else None,
            tool_name=tool_name,
            target=target_for(tool_name, arguments),
            payload_bytes=payload,
            payload_sha256=payload_digest(payload),
            operation_id=new_operation_id("order"),
            before_snapshot=dict(before_snapshot) if before_snapshot else None,
            summary=summary,
            created_at=moment.isoformat(),
            updated_at=moment.isoformat(),
            expires_at=(moment + self.approval_ttl).isoformat(),
        )
        return self.store.save(action)

    # -------------------------------------------------------------------- deciding

    def approve(
        self, *, owner_user_id: str, thread_id: str, interrupt_id: str, request_id: str
    ) -> PendingAction:
        return self._decide(
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            interrupt_id=interrupt_id,
            request_id=request_id,
            target=PendingStatus.APPROVED,
        )

    def reject(
        self, *, owner_user_id: str, thread_id: str, interrupt_id: str, request_id: str
    ) -> PendingAction:
        return self._decide(
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            interrupt_id=interrupt_id,
            request_id=request_id,
            target=PendingStatus.REJECTED,
        )

    def _decide(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        interrupt_id: str,
        request_id: str,
        target: PendingStatus,
    ) -> PendingAction:
        """Move a pending action to a decided status. Idempotent per request id.

        Repeating the *same* request id is a network retry and returns the original
        decision. A *different* request id means a second click, and is refused with
        ``ALREADY_DECIDED`` — which is what "两个 tab 同时点击只成功一次" means at this layer.
        """
        existing = self._load(owner_user_id, thread_id, interrupt_id)
        self._check_planning(existing)

        if existing.resume_request_id == request_id and existing.status is target:
            LOGGER.info("resume %s replayed; returning the original decision", request_id)
            return existing

        if existing.status is not PendingStatus.PENDING:
            raise ApprovalError(
                "ALREADY_DECIDED",
                f"pending action is already {existing.status}; a second decision is ignored",
            )

        if self._is_expired(existing):
            self.store.transition(
                owner_user_id,
                thread_id,
                existing.interrupt_id,
                expect=PendingStatus.PENDING,
                target=PendingStatus.EXPIRED,
            )
            raise ApprovalError(
                "APPROVAL_EXPIRED", "this approval window has closed; ask the agent again"
            )

        moved = self.store.transition(
            owner_user_id,
            thread_id,
            existing.interrupt_id,
            expect=PendingStatus.PENDING,
            target=target,
            decided_at=self.clock().isoformat(),
            resume_request_id=request_id,
        )
        if moved is None:
            # We lost the race between the check above and the update.
            raise ApprovalError(
                "ALREADY_DECIDED",
                "another decision landed first; a second decision is ignored",
            )
        LOGGER.info("pending action %s -> %s by request %s", moved.operation_id, target, request_id)
        return moved

    # ----------------------------------------------------------------- authorising

    def authorize(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        interrupt_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> WriteAuthorization:
        """Authorise one tool call, or explain why it cannot proceed.

        The write is matched to the **interrupt the user answered**, not to a tool call id.
        That is not a shortcut: the framework's HITL interrupt does not carry a
        ``tool_call_id`` (only the tool name, the arguments and a description), so an
        approval could not be matched to a specific call even in principle. Binding to the
        interrupt is also the more accurate statement of what was approved — the user
        answered *that* question — and it keeps two legitimately identical purchases in one
        thread distinguishable, which matching on the payload hash would not.

        On a replay the record is already ``EXECUTING`` or ``EXECUTED``; both re-issue a
        grant for the *same* operation id, so the retry is deduplicated by the ERP rather
        than creating a second order.
        """
        action = self.store.find(owner_user_id, thread_id, interrupt_id)
        if action is None:
            raise ApprovalError(
                "APPROVAL_REQUIRED",
                "no approved action matches this interrupt; approve it before writing",
            )

        self._check_planning(action, executing=True)
        if action.tool_name != tool_name:
            raise ApprovalError(
                "GRANT_MISMATCH",
                f"the approval covers {action.tool_name}, not {tool_name}",
            )

        if self._is_expired(action) and action.status in (
            PendingStatus.PENDING,
            PendingStatus.APPROVED,
        ):
            self.store.transition(
                owner_user_id,
                thread_id,
                action.interrupt_id,
                expect=action.status,
                target=PendingStatus.EXPIRED,
            )
            raise ApprovalError(
                "APPROVAL_EXPIRED", "this approval has expired; ask the user to approve again"
            )

        if action.status is PendingStatus.REJECTED:
            raise ApprovalError("APPROVAL_REJECTED", "the user rejected this action")

        if action.status is PendingStatus.PENDING:
            raise ApprovalError(
                "APPROVAL_REQUIRED", "this action has not been approved yet"
            )

        if action.status not in (PendingStatus.APPROVED, PendingStatus.EXECUTING,
                                  PendingStatus.EXECUTED):
            raise ApprovalError("APPROVAL_REQUIRED", f"action is {action.status}")

        # The decisive check: the arguments about to be sent must be the arguments that were
        # approved. A model that edits a price between approval and execution fails here.
        candidate = canonical_bytes(tool_name, arguments)
        if not action.matches_bytes(candidate):
            raise ApprovalError(
                "PARAMETERS_CHANGED",
                "the parameters differ from the approved ones; "
                "changed content needs a new approval",
            )

        if action.status is PendingStatus.APPROVED:
            claimed = self.store.transition(
                owner_user_id,
                thread_id,
                action.interrupt_id,
                expect=PendingStatus.APPROVED,
                target=PendingStatus.EXECUTING,
            )
            if claimed is None:
                # Another executor claimed it; re-read and continue with the same operation
                # id rather than refusing, because the ERP will deduplicate.
                claimed = self._load(owner_user_id, thread_id, action.interrupt_id)
                if claimed.status not in (PendingStatus.EXECUTING, PendingStatus.EXECUTED):
                    raise ApprovalError(
                        "ALREADY_DECIDED", f"action is {claimed.status} and cannot be executed"
                    )
            action = claimed

        grant = issue_grant(
            secret=self.grant_secret,
            owner=owner_user_id,
            tool=tool_name,
            target=action.target,
            operation_id=action.operation_id,
            payload_sha256=action.payload_sha256,
            ttl_seconds=self.grant_ttl_seconds,
            now=_epoch(self.clock()),
            approval_ref={"thread_id": thread_id, "interrupt_id": interrupt_id}
            if action.planning_binding else None,
        )
        return WriteAuthorization(
            grant=grant,
            operation_id=action.operation_id,
            payload_bytes=action.payload_bytes,
            action=action,
        )

    def finish(
        self,
        *,
        authorized: WriteAuthorization,
        ok: bool,
        error: str | None = None,
    ) -> None:
        """Record the outcome of an execution, from ``EXECUTING`` only."""
        action = authorized.action
        self.store.transition(
            action.owner_user_id,
            action.thread_id,
            action.interrupt_id,
            expect=PendingStatus.EXECUTING,
            target=PendingStatus.EXECUTED if ok else PendingStatus.FAILED,
            last_error=None if ok else error,
        )

    # -------------------------------------------------------------------- queries

    def review(self, *, owner_user_id: str, thread_id: str, interrupt_id: str) -> ActionSummary:
        """The reviewable view for the API and the UI."""
        action = self._load(owner_user_id, thread_id, interrupt_id)
        _summary, total, lines = summarise(action.tool_name, action.payload)
        return ActionSummary(
            interrupt_id=action.interrupt_id,
            tool_name=action.tool_name,
            target=action.target,
            status=str(action.status),
            summary=action.summary,
            payload=action.payload,
            before_snapshot=action.before_snapshot,
            suggested_total=total,
            lines=lines,
        )

    def verify_internal(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        interrupt_id: str,
        tool_name: str,
        target: str,
        payload_sha256: str,
        operation_id: str,
    ) -> PendingAction:
        """Confirm a grant against the recorded approval.

        This is what the gateway asks the API before forwarding a write. The signature on
        the grant already proves the API issued it; this second check proves the approval
        still exists, still belongs to that owner, and has not been revoked or superseded.
        """
        action = self._load(owner_user_id, thread_id, interrupt_id)
        self._check_planning(action, executing=True)
        mismatches = [
            name
            for name, actual, expected in (
                ("operation_id", action.operation_id, operation_id),
                ("tool_name", action.tool_name, tool_name),
                ("target", action.target, target),
                ("payload_sha256", action.payload_sha256, payload_sha256),
            )
            if actual != expected
        ]
        if mismatches:
            raise ApprovalError(
                "GRANT_MISMATCH",
                f"the recorded approval disagrees on: {', '.join(mismatches)}",
            )
        if action.status not in (
            PendingStatus.APPROVED,
            PendingStatus.EXECUTING,
            PendingStatus.EXECUTED,
        ):
            raise ApprovalError(
                "APPROVAL_REQUIRED", f"the recorded approval is {action.status}"
            )
        return action

    def pending_for_thread(self, owner_user_id: str, thread_id: str) -> list[PendingAction]:
        return [
            action
            for action in self.store.list_for_thread(owner_user_id, thread_id)
            if action.status is PendingStatus.PENDING
        ]

    # -------------------------------------------------------------------- helpers

    def _load(self, owner_user_id: str, thread_id: str, interrupt_id: str) -> PendingAction:
        action = self.store.find(owner_user_id, thread_id, interrupt_id)
        if action is None:
            raise ApprovalError(
                "ACTION_NOT_FOUND", "no pending action matches this thread and interrupt"
            )
        return action

    def _check_planning(self, action: PendingAction, *, executing: bool = False) -> None:
        if action.planning_binding is not None:
            if self.planning_guard is None:
                raise ApprovalError("PLANNING_UNAVAILABLE", "planning revision guard is required")
            self.planning_guard(action, executing=executing)

    def _is_expired(self, action: PendingAction) -> bool:
        if not action.expires_at:
            return False
        return self.clock() >= datetime.fromisoformat(action.expires_at)


__all__ = [
    "APPROVABLE_TOOLS",
    "ApprovalService",
    "WriteAuthorization",
    "canonical_bytes",
    "target_for",
]
