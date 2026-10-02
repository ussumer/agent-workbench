"""Application records: threads, display messages, runs and pending actions.

These collections describe *the application's* view of a conversation. They are
deliberately separate from the graph checkpointer:

* A display message is what the user saw. It survives compaction, and it is
  upserted by id so a reconnecting client cannot duplicate history.
* A run records one attempt to execute, keyed so a repeated ``request_id`` maps
  to the same run instead of starting a second one.

Every read and write is filtered by ``owner_user_id``. Another user's thread is
indistinguishable from a missing one, which is what the HTTP contract requires.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pymongo import DESCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from agent.persistence.indexes import (
    COLLECTION_DISPLAY_MESSAGES,
    COLLECTION_PENDING_ACTIONS,
    COLLECTION_RUNS,
    COLLECTION_THREADS,
)

RUN_STATUSES = ("running", "interrupted", "completed", "failed", "cancelled")
PENDING_ACTION_STATUSES = ("pending", "approved", "rejected", "expired", "consumed")


class OwnershipViolation(PermissionError):
    """A record exists but belongs to a different owner."""


class RequestConflict(RuntimeError):
    """The same identifier was reused with different content."""


@dataclass(frozen=True)
class ThreadRecord:
    thread_id: str
    owner_user_id: str
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> ThreadRecord:
        return cls(
            thread_id=document["thread_id"],
            owner_user_id=document["owner_user_id"],
            title=document.get("title", ""),
            created_at=document["created_at"],
            updated_at=document["updated_at"],
        )


@dataclass(frozen=True)
class RunReservation:
    """Outcome of reserving a run for one ``request_id``."""

    run_id: str
    thread_id: str
    status: str
    replayed: bool

    @property
    def is_new(self) -> bool:
        return not self.replayed


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ApplicationRepository:
    """All application collections, owner-scoped."""

    def __init__(self, database: Database) -> None:
        self._database = database

    # ------------------------------------------------------------------ threads

    def ensure_thread(self, *, owner_user_id: str, thread_id: str, title: str = "") -> ThreadRecord:
        """Create the thread if absent; never reassign an existing owner."""
        now = _now()
        collection = self._database[COLLECTION_THREADS]
        existing = collection.find_one({"thread_id": thread_id})
        if existing is not None:
            if existing["owner_user_id"] != owner_user_id:
                raise OwnershipViolation(
                    f"thread {thread_id} belongs to another user"
                )
            collection.update_one({"thread_id": thread_id}, {"$set": {"updated_at": now}})
            return ThreadRecord.from_document({**existing, "updated_at": now})

        document = {
            "thread_id": thread_id,
            "owner_user_id": owner_user_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }
        try:
            collection.insert_one(document)
        except DuplicateKeyError:
            # Another request created it first; re-read and apply the same ownership check.
            return self.ensure_thread(
                owner_user_id=owner_user_id, thread_id=thread_id, title=title
            )
        return ThreadRecord.from_document(document)

    def get_thread(self, *, owner_user_id: str, thread_id: str) -> ThreadRecord | None:
        document = self._database[COLLECTION_THREADS].find_one(
            {"thread_id": thread_id, "owner_user_id": owner_user_id}
        )
        return ThreadRecord.from_document(document) if document else None

    def list_threads(
        self, *, owner_user_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[list[ThreadRecord], int]:
        query = {"owner_user_id": owner_user_id}
        collection = self._database[COLLECTION_THREADS]
        total = collection.count_documents(query)
        cursor = (
            collection.find(query)
            .sort([("updated_at", DESCENDING), ("thread_id", DESCENDING)])
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        return [ThreadRecord.from_document(document) for document in cursor], total

    def delete_thread(self, *, owner_user_id: str, thread_id: str) -> dict[str, int]:
        """Remove one thread's application records. Returns what was deleted.

        Graph checkpoints are removed by the caller through the checkpointer API;
        this method only owns the application collections.
        """
        thread = self.get_thread(owner_user_id=owner_user_id, thread_id=thread_id)
        if thread is None:
            return {}
        return {
            COLLECTION_THREADS: self._database[COLLECTION_THREADS].delete_many(
                {"thread_id": thread_id, "owner_user_id": owner_user_id}
            ).deleted_count,
            COLLECTION_DISPLAY_MESSAGES: self._database[COLLECTION_DISPLAY_MESSAGES].delete_many(
                {"thread_id": thread_id, "owner_user_id": owner_user_id}
            ).deleted_count,
            COLLECTION_RUNS: self._database[COLLECTION_RUNS].delete_many(
                {"thread_id": thread_id, "owner_user_id": owner_user_id}
            ).deleted_count,
            COLLECTION_PENDING_ACTIONS: self._database[COLLECTION_PENDING_ACTIONS].delete_many(
                {"thread_id": thread_id, "owner_user_id": owner_user_id}
            ).deleted_count,
        }

    # -------------------------------------------------------- display messages

    def upsert_display_message(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        message_id: str,
        seq: int,
        role: str,
        content: str,
    ) -> str:
        """Insert or refresh one display message.

        Returns ``"inserted"``, ``"unchanged"`` or ``"updated"``. Re-delivering the
        same message is a no-op; reusing an id with different content is a conflict,
        because silently overwriting history would hide a real bug.
        """
        if self.get_thread(owner_user_id=owner_user_id, thread_id=thread_id) is None:
            raise OwnershipViolation(f"no thread {thread_id} for this owner")

        collection = self._database[COLLECTION_DISPLAY_MESSAGES]
        content_hash = _digest(content)
        existing = collection.find_one({"thread_id": thread_id, "message_id": message_id})
        if existing is not None:
            if existing["content_sha256"] != content_hash:
                raise RequestConflict(
                    f"display message {message_id} already exists with different content"
                )
            if existing.get("seq") == seq and existing.get("role") == role:
                return "unchanged"
            collection.update_one(
                {"thread_id": thread_id, "message_id": message_id},
                {"$set": {"seq": seq, "role": role, "updated_at": _now()}},
            )
            return "updated"

        document = {
            "thread_id": thread_id,
            "message_id": message_id,
            "owner_user_id": owner_user_id,
            "seq": seq,
            "role": role,
            "content": content,
            "content_sha256": content_hash,
            "created_at": _now(),
            "updated_at": _now(),
        }
        try:
            collection.insert_one(document)
        except DuplicateKeyError:
            return self.upsert_display_message(
                owner_user_id=owner_user_id,
                thread_id=thread_id,
                message_id=message_id,
                seq=seq,
                role=role,
                content=content,
            )
        return "inserted"

    def list_display_messages(
        self, *, owner_user_id: str, thread_id: str, limit: int = 500
    ) -> list[dict[str, Any]]:
        cursor = (
            self._database[COLLECTION_DISPLAY_MESSAGES]
            .find(
                {"thread_id": thread_id, "owner_user_id": owner_user_id},
                {"_id": False},
            )
            .sort([("seq", 1), ("message_id", 1)])
            .limit(limit)
        )
        return list(cursor)

    def count_display_messages(self, *, owner_user_id: str, thread_id: str) -> int:
        return self._database[COLLECTION_DISPLAY_MESSAGES].count_documents(
            {"thread_id": thread_id, "owner_user_id": owner_user_id}
        )

    # -------------------------------------------------------------------- runs

    def reserve_run(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        run_id: str,
        request_id: str,
        request_digest: str,
    ) -> RunReservation:
        """Reserve the run for one request, or replay the existing one.

        The unique ``(owner, request_id)`` index is what makes this safe under
        concurrency: whoever loses the insert race reads the winner's row. Reusing
        a ``request_id`` with a different body is a conflict.
        """
        if self.get_thread(owner_user_id=owner_user_id, thread_id=thread_id) is None:
            raise OwnershipViolation(f"no thread {thread_id} for this owner")

        collection = self._database[COLLECTION_RUNS]
        existing = collection.find_one(
            {"owner_user_id": owner_user_id, "request_id": request_id}
        )
        if existing is not None:
            return self._replay_run(existing, request_digest)

        document = {
            "run_id": run_id,
            "thread_id": thread_id,
            "owner_user_id": owner_user_id,
            "request_id": request_id,
            "request_digest": request_digest,
            "status": "running",
            "created_at": _now(),
            "updated_at": _now(),
        }
        try:
            collection.insert_one(document)
        except DuplicateKeyError:
            winner = collection.find_one(
                {"owner_user_id": owner_user_id, "request_id": request_id}
            )
            if winner is None:
                raise RequestConflict(
                    f"request {request_id} is in flight; retry with the same body"
                ) from None
            return self._replay_run(winner, request_digest)
        return RunReservation(
            run_id=run_id, thread_id=thread_id, status="running", replayed=False
        )

    def _replay_run(self, document: dict[str, Any], request_digest: str) -> RunReservation:
        if document["request_digest"] != request_digest:
            raise RequestConflict(
                f"request_id {document['request_id']} was already used with a different body"
            )
        return RunReservation(
            run_id=document["run_id"],
            thread_id=document["thread_id"],
            status=document["status"],
            replayed=True,
        )

    def get_run(self, *, owner_user_id: str, run_id: str) -> dict[str, Any] | None:
        return self._database[COLLECTION_RUNS].find_one(
            {"run_id": run_id, "owner_user_id": owner_user_id}, {"_id": False}
        )

    def list_runs(self, *, owner_user_id: str, thread_id: str) -> list[dict[str, Any]]:
        cursor = (
            self._database[COLLECTION_RUNS]
            .find({"thread_id": thread_id, "owner_user_id": owner_user_id}, {"_id": False})
            .sort([("created_at", 1)])
        )
        return list(cursor)

    def update_run_status(
        self, *, owner_user_id: str, run_id: str, status: str, **fields: Any
    ) -> bool:
        if status not in RUN_STATUSES:
            raise ValueError(f"unknown run status {status!r}")
        update = {"status": status, "updated_at": _now(), **fields}
        result = self._database[COLLECTION_RUNS].update_one(
            {"run_id": run_id, "owner_user_id": owner_user_id}, {"$set": update}
        )
        return result.modified_count > 0 or result.matched_count > 0

    # --------------------------------------------------------- pending actions

    def save_pending_action(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        interrupt_id: str,
        tool_call_id: str,
        tool_name: str,
        action_digest: str,
        payload: dict[str, Any],
        status: str = "pending",
    ) -> str:
        """Record (or refresh) one pending approval.

        Approval is bound to the frozen payload digest, so a later edit produces a
        different digest and cannot silently inherit the old decision.
        """
        if status not in PENDING_ACTION_STATUSES:
            raise ValueError(f"unknown pending action status {status!r}")

        collection = self._database[COLLECTION_PENDING_ACTIONS]
        key = {"interrupt_id": interrupt_id, "tool_call_id": tool_call_id}
        existing = collection.find_one(key)
        if existing is not None:
            if existing["owner_user_id"] != owner_user_id:
                raise OwnershipViolation("pending action belongs to another user")
            if existing["action_digest"] != action_digest:
                raise RequestConflict(
                    "the same interrupt id arrived with a different action digest"
                )
            return "unchanged"

        try:
            collection.insert_one(
                {
                    **key,
                    "owner_user_id": owner_user_id,
                    "thread_id": thread_id,
                    "tool_name": tool_name,
                    "action_digest": action_digest,
                    "payload": payload,
                    "status": status,
                    "created_at": _now(),
                    "updated_at": _now(),
                }
            )
        except DuplicateKeyError:
            return self.save_pending_action(
                owner_user_id=owner_user_id,
                thread_id=thread_id,
                interrupt_id=interrupt_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                action_digest=action_digest,
                payload=payload,
                status=status,
            )
        return "inserted"

    def get_pending_action(
        self, *, owner_user_id: str, interrupt_id: str
    ) -> dict[str, Any] | None:
        return self._database[COLLECTION_PENDING_ACTIONS].find_one(
            {"interrupt_id": interrupt_id, "owner_user_id": owner_user_id}, {"_id": False}
        )

    def list_pending_actions(
        self, *, owner_user_id: str, thread_id: str, status: str | None = "pending"
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {
            "thread_id": thread_id,
            "owner_user_id": owner_user_id,
        }
        if status is not None:
            query["status"] = status
        cursor = (
            self._database[COLLECTION_PENDING_ACTIONS]
            .find(query, {"_id": False})
            .sort([("created_at", 1)])
        )
        return list(cursor)

    def decide_pending_action(
        self, *, owner_user_id: str, interrupt_id: str, status: str
    ) -> dict[str, Any] | None:
        """Conditionally move a pending action to approved/rejected.

        The filter includes the current status, so a second concurrent click cannot
        produce a second decision.
        """
        if status not in PENDING_ACTION_STATUSES:
            raise ValueError(f"unknown pending action status {status!r}")
        return self._database[COLLECTION_PENDING_ACTIONS].find_one_and_update(
            {
                "interrupt_id": interrupt_id,
                "owner_user_id": owner_user_id,
                "status": "pending",
            },
            {"$set": {"status": status, "decided_at": _now(), "updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
            projection={"_id": False},
        )
