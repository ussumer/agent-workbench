"""Persistence for pending actions, with the concurrency guarantees built into the queries.

Every state change is a conditional update, never a read-then-write. That is the whole
mechanism behind two requirements that sound like policy but are really database behaviour:

* **Two tabs clicking approve produce one approval.** The transition out of ``PENDING``
  filters on ``status: pending``, so the second update matches nothing.
* **One executor per approval.** The transition into ``EXECUTING`` filters on
  ``status: approved``; exactly one caller is handed the document.

A read-then-write would pass a functional test and still lose the race.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from agent.approval.models import (
    ApprovalError,
    PendingAction,
    PendingStatus,
    new_operation_id,
)

LOGGER = logging.getLogger("rush_harness.approval.store")

#: How long an approval stays usable. Long enough for a human to answer twice, short
#: enough that a stale tab cannot execute last week's decision.
DEFAULT_APPROVAL_TTL = timedelta(minutes=30)


def _now() -> datetime:
    return datetime.now(UTC)


class PendingActionStore(Protocol):
    """What the service needs from storage."""

    def save(self, action: PendingAction) -> PendingAction: ...

    def find(
        self, owner_user_id: str, thread_id: str, interrupt_id: str
    ) -> PendingAction | None: ...

    def find_by_tool_call(
        self, owner_user_id: str, thread_id: str, tool_call_id: str
    ) -> PendingAction | None: ...

    def transition(
        self,
        owner_user_id: str,
        thread_id: str,
        interrupt_id: str,
        *,
        expect: PendingStatus,
        target: PendingStatus,
        **fields: Any,
    ) -> PendingAction | None: ...

    def list_for_thread(self, owner_user_id: str, thread_id: str) -> list[PendingAction]: ...


class MongoPendingActionStore:
    """Mongo implementation. Byte fields are stored as BSON binary, not text."""

    def __init__(self, database: Any, collection: str | None = None) -> None:
        from agent.persistence.indexes import COLLECTION_PENDING_ACTIONS

        self._collection = database[collection or COLLECTION_PENDING_ACTIONS]

    @property
    def collection(self) -> Any:
        return self._collection

    def save(self, action: PendingAction) -> PendingAction:
        """Insert a new pending action, refusing a second record for the same interrupt.

        The unique index is what makes a double-click safe even before the conditional
        updates are considered: the second insert cannot land.
        """
        document = action.as_document()
        document.setdefault("created_at", _now().isoformat())
        try:
            self._collection.insert_one(document)
        except DuplicateKeyError as failure:
            existing = self.find(action.owner_user_id, action.thread_id, action.interrupt_id)
            if existing is not None:
                LOGGER.info(
                    "pending action for interrupt %s already recorded; reusing it",
                    action.interrupt_id,
                )
                return existing
            raise ApprovalError(
                "DUPLICATE_ACTION",
                f"a different pending action already claims operation {action.operation_id}",
            ) from failure
        return action

    def find(
        self, owner_user_id: str, thread_id: str, interrupt_id: str
    ) -> PendingAction | None:
        document = self._collection.find_one(
            {
                "owner_user_id": owner_user_id,
                "thread_id": thread_id,
                "interrupt_id": interrupt_id,
            }
        )
        return PendingAction.from_document(document) if document else None

    def find_by_tool_call(
        self, owner_user_id: str, thread_id: str, tool_call_id: str
    ) -> PendingAction | None:
        """The action a replayed tool call belongs to.

        Ordering by recency matters: a thread may legitimately hold several actions for the
        same tool over its life, and the replay belongs to the newest one.
        """
        document = self._collection.find_one(
            {
                "owner_user_id": owner_user_id,
                "thread_id": thread_id,
                "tool_call_id": tool_call_id,
            },
            sort=[("created_at", -1)],
        )
        return PendingAction.from_document(document) if document else None

    def transition(
        self,
        owner_user_id: str,
        thread_id: str,
        interrupt_id: str,
        *,
        expect: PendingStatus,
        target: PendingStatus,
        **fields: Any,
    ) -> PendingAction | None:
        """Move one action between statuses, or return None if the filter no longer matches.

        ``None`` is not an error: it means somebody else already moved it, which is exactly
        what happens when two tabs or two resumes race.
        """
        updates = {**fields, "status": str(target), "updated_at": _now().isoformat()}
        document = self._collection.find_one_and_update(
            {
                "owner_user_id": owner_user_id,
                "thread_id": thread_id,
                "interrupt_id": interrupt_id,
                "status": str(expect),
            },
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        return PendingAction.from_document(document) if document else None

    def bump_attempt(
        self, owner_user_id: str, thread_id: str, interrupt_id: str, *, error: str | None
    ) -> None:
        self._collection.update_one(
            {
                "owner_user_id": owner_user_id,
                "thread_id": thread_id,
                "interrupt_id": interrupt_id,
            },
            {
                "$inc": {"attempts": 1},
                "$set": {"last_error": error, "updated_at": _now().isoformat()},
            },
        )

    def list_for_thread(self, owner_user_id: str, thread_id: str) -> list[PendingAction]:
        cursor = self._collection.find(
            {"owner_user_id": owner_user_id, "thread_id": thread_id}
        ).sort("created_at", ASCENDING)
        return [PendingAction.from_document(document) for document in cursor]


__all__ = [
    "DEFAULT_APPROVAL_TTL",
    "MongoPendingActionStore",
    "PendingActionStore",
    "new_operation_id",
]
