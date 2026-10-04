"""The one place an artifact is created, so its digest and owner are never guessed at.

Both producers go through :meth:`ArtifactService.register` — the sandbox file tool and the
chart tool. That matters because the digest is what the user checks after downloading: if
two code paths computed it differently (or one trusted a size the remote reported), the
check would be answering the wrong question.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from agent.artifacts.models import (
    ArtifactRecord,
    guess_mime,
    new_artifact_id,
    payload_sha256,
    safe_filename,
)
from agent.artifacts.store import ArtifactStore

#: A single artifact above this size is refused. Reports and charts are small; anything
#: larger is a sign the caller is trying to move a dataset through a channel built for
#: deliverables, and BSON documents have a hard 16MB ceiling.
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024

#: Run-config keys carrying the trusted scope. Set by the API from the session, never by a
#: model — a tool call cannot name the owner, so it cannot file a document under someone
#: else's account. Defined here because both artifact producers need to agree on them.
SCOPE_OWNER_KEY = "owner_user_id"
SCOPE_THREAD_KEY = "thread_id"


def _now() -> datetime:
    return datetime.now(UTC)


def resolve_scope(config: Mapping[str, Any] | None) -> tuple[str, str] | None:
    """Owner and thread for this run, or ``None`` when the run carries no scope.

    ``None`` is a refusal, not a default: an artifact registered without an owner would be
    one nobody could be held to and, worse, one that a later lookup might match for the
    wrong person.
    """
    configurable = ((config or {}).get("configurable")) or {}
    owner = configurable.get(SCOPE_OWNER_KEY)
    thread = configurable.get(SCOPE_THREAD_KEY)
    if not owner or not thread:
        return None
    return str(owner), str(thread)


class ArtifactService:
    """Create, describe and open artifacts."""

    def __init__(
        self,
        store: ArtifactStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or _now

    @property
    def store(self) -> ArtifactStore:
        return self._store

    def register(
        self,
        *,
        owner_user_id: str,
        thread_id: str,
        name: str,
        content: bytes,
        mime: str | None = None,
        source: str = "",
    ) -> ArtifactRecord:
        """Store bytes and their metadata together.

        An empty payload is refused rather than registered: a zero-byte link looks like a
        successful download to everyone involved, which is worse than an error.
        """
        if not content:
            raise ValueError("refusing to register an empty artifact")
        if len(content) > MAX_ARTIFACT_BYTES:
            raise ValueError(
                f"artifact is {len(content)} bytes, above the {MAX_ARTIFACT_BYTES} limit"
            )

        filename = safe_filename(name)
        record = ArtifactRecord(
            artifact_id=new_artifact_id(),
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            name=filename,
            mime=guess_mime(filename, mime),
            size=len(content),
            sha256=payload_sha256(content),
            source=source,
            created_at=self._clock().isoformat(),
        )
        self._store.save(record, content)
        return record

    def describe(
        self, owner_user_id: str, artifact_id: str
    ) -> tuple[ArtifactRecord, bool] | None:
        """Metadata plus whether the bytes are still present.

        ``None`` means "no such artifact for this owner" and must be reported as 404. The
        boolean is separate from the record because the row and the blob can legitimately
        disagree, and the caller needs to distinguish "never existed" from "existed, file
        is gone".
        """
        record = self._store.find(owner_user_id, artifact_id)
        if record is None:
            return None
        return record, self._store.has_content(owner_user_id, artifact_id)

    def open(self, owner_user_id: str, artifact_id: str) -> tuple[ArtifactRecord, bytes] | None:
        """The bytes, or ``None``. A registry row whose blob is missing yields ``None``."""
        record = self._store.find(owner_user_id, artifact_id)
        if record is None:
            return None
        content = self._store.read_content(owner_user_id, artifact_id)
        if content is None:
            return None
        return record, content

    def list_for_thread(self, owner_user_id: str, thread_id: str) -> list[ArtifactRecord]:
        return self._store.list_for_thread(owner_user_id, thread_id)

    def chart_hook(
        self, *, name_prefix: str = "chart", download_url_template: str = ""
    ) -> Callable[..., Mapping[str, Any] | None]:
        """The callback ``chart_generator`` calls once it has the image bytes.

        A chart is drawn by a remote service and arrives in the agent process, so this is the
        only point at which it can become a file the user downloads — there is no sandbox
        copy to export. Registering it here rather than in the tool keeps the digest and the
        owner decided in one place for both producers.

        Returns ``None`` (and registers nothing) when the run carries no scope: an artifact
        with no owner must not exist, and the chart itself is still returned to the model.
        """

        def on_asset(
            content: bytes,
            mime: str,
            *,
            chart_type: str = "",
            title: str | None = None,
            config: Mapping[str, Any] | None = None,
        ) -> Mapping[str, Any] | None:
            scope = resolve_scope(config)
            if scope is None:
                return None
            owner_user_id, thread_id = scope

            stem = safe_filename(title or f"{name_prefix}-{chart_type or 'image'}")
            if "." not in stem:
                stem = f"{stem}.png"
            try:
                record = self.register(
                    owner_user_id=owner_user_id,
                    thread_id=thread_id,
                    name=stem,
                    content=content,
                    mime=mime,
                    source=f"{name_prefix}:{chart_type}" if chart_type else name_prefix,
                )
            except ValueError:
                # An oversized or empty chart is a failure of the chart, not of the turn;
                # the model still gets its image and can mention that the file was not kept.
                return None

            extra: dict[str, Any] = {
                "artifact_id": record.artifact_id,
                "sha256": record.sha256,
                "artifact_name": record.name,
                "artifact_size": record.size,
            }
            if download_url_template:
                extra["download_url"] = download_url_template.format(
                    artifact_id=record.artifact_id
                )
            return extra

        return on_asset


__all__ = [
    "MAX_ARTIFACT_BYTES",
    "SCOPE_OWNER_KEY",
    "SCOPE_THREAD_KEY",
    "ArtifactService",
    "resolve_scope",
]
