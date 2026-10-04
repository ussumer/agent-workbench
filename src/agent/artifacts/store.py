"""Artifact persistence: metadata and bytes in two collections, both owner-scoped.

The split is the design, not an optimisation. ``artifacts`` answers "what exists and what
should it hash to"; ``artifact_blobs`` holds the bytes. Keeping them apart is what makes
"the registry entry is there but the file is gone" a state the system can *report* instead
of a state it silently lies about — the download route reads the row, looks for the blob,
and returns 404 with ``download_ready: false`` when it is missing.

Every lookup takes ``owner_user_id`` as a required argument. There is no ``find(artifact_id)``
overload, because the one time somebody adds it is the time a cross-user read appears.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

LOGGER = logging.getLogger("rush_harness.artifacts.store")


class ArtifactStore(Protocol):
    """What the service needs from storage."""

    def save(self, record: Any, content: bytes) -> Any: ...

    def find(self, owner_user_id: str, artifact_id: str) -> Any | None: ...

    def has_content(self, owner_user_id: str, artifact_id: str) -> bool: ...

    def read_content(self, owner_user_id: str, artifact_id: str) -> bytes | None: ...

    def list_for_thread(self, owner_user_id: str, thread_id: str) -> list[Any]: ...


class MongoArtifactStore:
    """Mongo implementation."""

    def __init__(
        self,
        database: Any,
        *,
        collection: str | None = None,
        blob_collection: str | None = None,
    ) -> None:
        from agent.persistence.indexes import (
            COLLECTION_ARTIFACT_BLOBS,
            COLLECTION_ARTIFACTS,
        )

        self._records = database[collection or COLLECTION_ARTIFACTS]
        self._blobs = database[blob_collection or COLLECTION_ARTIFACT_BLOBS]

    @property
    def collection(self) -> Any:
        return self._records

    @property
    def blob_collection(self) -> Any:
        return self._blobs

    def save(self, record: Any, content: bytes) -> Any:
        """Write the bytes first, then the row that points at them.

        Order matters. If the process dies between the two writes, the surviving state is a
        blob nobody references — garbage, and reclaimable. The other order would leave a
        registry row advertising a file that was never stored, which is exactly the
        fake-link case the metadata split exists to prevent.
        """
        self._blobs.replace_one(
            {"owner_user_id": record.owner_user_id, "artifact_id": record.artifact_id},
            {
                "owner_user_id": record.owner_user_id,
                "artifact_id": record.artifact_id,
                "sha256": record.sha256,
                "size": record.size,
                "content": content,
            },
            upsert=True,
        )
        self._records.replace_one(
            {"owner_user_id": record.owner_user_id, "artifact_id": record.artifact_id},
            record.as_document(),
            upsert=True,
        )
        return record

    def find(self, owner_user_id: str, artifact_id: str) -> Any | None:
        from agent.artifacts.models import ArtifactRecord

        document = self._records.find_one(
            {"owner_user_id": owner_user_id, "artifact_id": artifact_id}
        )
        return ArtifactRecord.from_document(document) if document else None

    def has_content(self, owner_user_id: str, artifact_id: str) -> bool:
        """Whether the bytes are still there, without transferring them.

        A projection rather than a ``find_one`` of the whole blob: the metadata route is
        called for every artifact in a conversation, and pulling megabytes of chart to
        answer a yes/no question would be a poor trade.
        """
        found = self._blobs.find_one(
            {"owner_user_id": owner_user_id, "artifact_id": artifact_id},
            projection={"_id": 1, "size": 1},
        )
        return found is not None and int(found.get("size") or 0) > 0

    def read_content(self, owner_user_id: str, artifact_id: str) -> bytes | None:
        found = self._blobs.find_one(
            {"owner_user_id": owner_user_id, "artifact_id": artifact_id}
        )
        if not found:
            return None
        content = found.get("content")
        if isinstance(content, (bytes, bytearray, memoryview)):
            return bytes(content)
        # A row without bytes is a corrupted store, not an empty file; saying so beats
        # returning b"" and letting the caller serve a zero-byte download.
        LOGGER.warning("artifact %s has no readable content", artifact_id)
        return None

    def list_for_thread(self, owner_user_id: str, thread_id: str) -> list[Any]:
        from agent.artifacts.models import ArtifactRecord

        cursor = self._records.find(
            {"owner_user_id": owner_user_id, "thread_id": thread_id}
        ).sort("created_at", 1)
        return [ArtifactRecord.from_document(document) for document in cursor]


__all__ = [
    "ArtifactStore",
    "MongoArtifactStore",
]
