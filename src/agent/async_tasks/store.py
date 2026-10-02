"""The local record of a background task: which Protocol run it is, and whose it is.

The Agent Protocol service keeps the run. This collection keeps the **mapping** — owner,
parent thread, the async thread/run ids it created, and what the task is doing — because the
Protocol service does not know about this project's owners and must not be trusted to enforce
them. Every read here is owner-scoped, so a task id is only useful to the person who launched
it.

Two uniqueness constraints carry behaviour rather than merely indexing:

* ``(owner_user_id, request_id)`` — a retried launch returns the original task instead of
  starting a second background run. Without it, a double-submitted form would analyse twice.
* ``(owner_user_id, parent_thread_id, async_run_id)`` — the poll path writes the status it
  just observed. Making the run id part of the key means a late response for a previous run
  cannot overwrite the status of the current one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

LOGGER = logging.getLogger("rush_harness.async_tasks.store")

TASK_ID_PREFIX = "task-"


class AsyncStatus(StrEnum):
    """What the user is shown. The contract fixes the first five spellings.

    ``LOST`` is the sixth, and it exists because the alternative is a lie: the Agent Protocol
    dev server does not survive a restart with its runs, so a task this process remembers and
    the service does not is *unknown*, not *finished*. Reporting it as ``completed`` would be
    the single worst outcome — the user would wait for a report that is never coming.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"


#: Statuses after which nothing more will happen.
TERMINAL: frozenset[str] = frozenset(
    {AsyncStatus.COMPLETED, AsyncStatus.FAILED, AsyncStatus.CANCELLED, AsyncStatus.LOST}
)

#: How the Protocol's own run status maps onto what we show.
RUN_STATUS_MAP: dict[str, str] = {
    "pending": AsyncStatus.QUEUED,
    "queued": AsyncStatus.QUEUED,
    "running": AsyncStatus.RUNNING,
    "success": AsyncStatus.COMPLETED,
    "completed": AsyncStatus.COMPLETED,
    "error": AsyncStatus.FAILED,
    "failed": AsyncStatus.FAILED,
    "timeout": AsyncStatus.FAILED,
    "interrupted": AsyncStatus.FAILED,
}


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_task_id() -> str:
    return f"{TASK_ID_PREFIX}{uuid4().hex}"


@dataclass(frozen=True)
class AsyncTaskRecord:
    task_id: str
    owner_user_id: str
    parent_thread_id: str
    instruction: str
    async_thread_id: str
    async_run_id: str
    status: str
    created_at: str
    updated_at: str
    #: Idempotency key of the launch, as supplied by the caller.
    request_id: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    last_error: str = ""
    #: Instructions accepted after launch, in order. The contract is explicit that if the SDK
    #: cannot rewrite a running input we must not claim we did — so this is a record of what
    #: was *asked for*, each with how it was handled.
    updates: list[dict[str, Any]] = field(default_factory=list)

    def as_document(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "owner_user_id": self.owner_user_id,
            "parent_thread_id": self.parent_thread_id,
            "instruction": self.instruction,
            "async_thread_id": self.async_thread_id,
            "async_run_id": self.async_run_id,
            "status": str(self.status),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "request_id": self.request_id,
            "artifact_ids": list(self.artifact_ids),
            "last_error": self.last_error,
            "updates": list(self.updates),
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> AsyncTaskRecord:
        return cls(
            task_id=str(document["task_id"]),
            owner_user_id=str(document["owner_user_id"]),
            parent_thread_id=str(document.get("parent_thread_id") or ""),
            instruction=str(document.get("instruction") or ""),
            async_thread_id=str(document.get("async_thread_id") or ""),
            async_run_id=str(document.get("async_run_id") or ""),
            status=str(document.get("status") or AsyncStatus.QUEUED),
            created_at=str(document.get("created_at") or ""),
            updated_at=str(document.get("updated_at") or ""),
            request_id=str(document.get("request_id") or ""),
            artifact_ids=[str(item) for item in (document.get("artifact_ids") or [])],
            last_error=str(document.get("last_error") or ""),
            updates=list(document.get("updates") or []),
        )

    def public(self) -> dict[str, Any]:
        """What the browser is told. The Protocol ids are included on purpose.

        The contract says the frontend's ``task_id`` maps to the service's real thread/run
        ids, and that the mapping is what the endpoints are for. Hiding them would make the
        "this is a real Protocol run" property unverifiable from the outside, which is the
        opposite of what the acceptance asks for.
        """
        return {
            "task_id": self.task_id,
            "status": str(self.status),
            "parent_thread_id": self.parent_thread_id,
            "async_thread_id": self.async_thread_id,
            "async_run_id": self.async_run_id,
            "instruction": self.instruction,
            "artifact_ids": list(self.artifact_ids),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "terminal": str(self.status) in TERMINAL,
            "error": self.last_error,
            "updates": list(self.updates),
        }


class MongoAsyncTaskStore:
    """Mongo implementation. Every lookup takes the owner; there is no id-only overload."""

    def __init__(self, database: Any, collection: str | None = None) -> None:
        from agent.persistence.indexes import COLLECTION_ASYNC_TASKS

        self._collection = database[collection or COLLECTION_ASYNC_TASKS]

    @property
    def collection(self) -> Any:
        return self._collection

    def create(self, record: AsyncTaskRecord) -> AsyncTaskRecord | None:
        """Insert a new task, or return ``None`` when this request already has one.

        ``None`` means "you already launched this", which is a success for the caller, not an
        error — the same shape the approval store uses for a lost race.
        """
        try:
            self._collection.insert_one(record.as_document())
        except DuplicateKeyError:
            LOGGER.info(
                "launch for owner=%s request=%s already exists",
                record.owner_user_id,
                record.request_id,
            )
            return None
        return record

    def find_by_request(
        self, owner_user_id: str, request_id: str
    ) -> AsyncTaskRecord | None:
        document = self._collection.find_one(
            {"owner_user_id": owner_user_id, "request_id": request_id}
        )
        return AsyncTaskRecord.from_document(document) if document else None

    def find(self, owner_user_id: str, task_id: str) -> AsyncTaskRecord | None:
        document = self._collection.find_one(
            {"owner_user_id": owner_user_id, "task_id": task_id}
        )
        return AsyncTaskRecord.from_document(document) if document else None

    def list_for_parent(
        self, owner_user_id: str, parent_thread_id: str
    ) -> list[AsyncTaskRecord]:
        cursor = self._collection.find(
            {"owner_user_id": owner_user_id, "parent_thread_id": parent_thread_id}
        ).sort("created_at", ASCENDING)
        return [AsyncTaskRecord.from_document(document) for document in cursor]

    def transition(
        self,
        owner_user_id: str,
        task_id: str,
        *,
        status: str,
        expect: frozenset[str] | None = None,
        **fields: Any,
    ) -> AsyncTaskRecord | None:
        """Move a task's status, optionally only out of certain statuses.

        ``expect`` is what stops a cancel from being overwritten by a poll that read the run
        a moment earlier: the update filters on the status still being non-terminal, so a
        terminal status is final no matter which request arrives last.
        """
        query: dict[str, Any] = {"owner_user_id": owner_user_id, "task_id": task_id}
        if expect is not None:
            query["status"] = {"$in": sorted(str(item) for item in expect)}
        document = self._collection.find_one_and_update(
            query,
            {"$set": {**fields, "status": str(status), "updated_at": now()}},
            return_document=ReturnDocument.AFTER,
        )
        return AsyncTaskRecord.from_document(document) if document else None

    def append_update(
        self, owner_user_id: str, task_id: str, entry: dict[str, Any]
    ) -> AsyncTaskRecord | None:
        document = self._collection.find_one_and_update(
            {"owner_user_id": owner_user_id, "task_id": task_id},
            {"$push": {"updates": entry}, "$set": {"updated_at": now()}},
            return_document=ReturnDocument.AFTER,
        )
        return AsyncTaskRecord.from_document(document) if document else None


__all__ = [
    "RUN_STATUS_MAP",
    "TASK_ID_PREFIX",
    "TERMINAL",
    "AsyncStatus",
    "AsyncTaskRecord",
    "MongoAsyncTaskStore",
    "new_task_id",
    "now",
]
