"""What an artifact is, and the two things every artifact must be able to prove.

An artifact is a file the agent produced that the user can download: a report, its CSV, a
chart. Two properties are non-negotiable and both are recorded at the moment the bytes are
in hand, never reconstructed later:

* **Its digest.** The download route hands the client the same sha256 it advertises, so a
  client can prove the file it received is the file the agent made. A digest computed at
  read time would prove nothing about what was stored.
* **Its owner.** Artifacts are looked up by owner, and a miss is a 404 rather than a 403 —
  the same rule threads follow, for the same reason: a 403 confirms the artifact exists.

Metadata and bytes are kept in **separate collections on purpose**. A registry row whose
bytes are gone is a real state (a volume was cleaned, a write was interrupted), and the
whole point of "缺产物不发假链接" is that this state must be representable and reported,
not papered over by a row that implies a downloadable file.
"""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from dataclasses import dataclass
from typing import Any

#: Prefix so an artifact id is recognisable in a log or a URL at a glance.
ARTIFACT_ID_PREFIX = "art-"

#: Extensions the demo produces. ``mimetypes`` knows most of these; the explicit table
#: exists because it guesses ``.md`` inconsistently across platforms.
KNOWN_MIME: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".txt": "text/plain",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".html": "text/html",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
}

DEFAULT_MIME = "application/octet-stream"


class ArtifactError(RuntimeError):
    """Something refused to happen, with a code the caller can act on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def new_artifact_id() -> str:
    """A fresh, unguessable id.

    Random rather than sequential: the download route is reachable without authentication
    beyond the demo cookie, so an enumerable id would let one demo user discover the
    other's artifact ids. (Ownership is still checked — this removes the invitation, it is
    not the check.)
    """
    return f"{ARTIFACT_ID_PREFIX}{uuid.uuid4().hex}"


def payload_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def guess_mime(name: str, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    lowered = name.lower()
    for extension, mime in KNOWN_MIME.items():
        if lowered.endswith(extension):
            return mime
    guessed, _encoding = mimetypes.guess_type(name)
    return guessed or DEFAULT_MIME


def safe_filename(name: str) -> str:
    """A name that can go in a ``Content-Disposition`` header without escaping games.

    Everything outside a conservative set is replaced with ``_``, and any path component is
    dropped: the name is attacker-influenced (it comes from a tool call), and a filename
    carrying a newline or a slash is a header-injection or path problem downstream.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned = "".join(
        character if (character.isalnum() or character in "._- ") else "_" for character in base
    )
    cleaned = cleaned.strip(" .")
    if not cleaned:
        return "artifact"
    return cleaned[:120]


@dataclass(frozen=True)
class ArtifactRecord:
    """One registered artifact. No bytes — those live in the blob collection."""

    artifact_id: str
    owner_user_id: str
    thread_id: str
    name: str
    mime: str
    size: int
    sha256: str
    #: Where it came from, for the trace: ``sandbox:/workspace/report/x.md`` or ``chart:bar``.
    source: str
    created_at: str

    def as_document(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "owner_user_id": self.owner_user_id,
            "thread_id": self.thread_id,
            "name": self.name,
            "mime": self.mime,
            "size": self.size,
            "sha256": self.sha256,
            "source": self.source,
            "created_at": self.created_at,
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> ArtifactRecord:
        return cls(
            artifact_id=str(document["artifact_id"]),
            owner_user_id=str(document["owner_user_id"]),
            thread_id=str(document.get("thread_id") or ""),
            name=str(document.get("name") or ""),
            mime=str(document.get("mime") or DEFAULT_MIME),
            size=int(document.get("size") or 0),
            sha256=str(document.get("sha256") or ""),
            source=str(document.get("source") or ""),
            created_at=str(document.get("created_at") or ""),
        )

    def public(self, *, download_ready: bool) -> dict[str, Any]:
        """What a client is allowed to see.

        The host path or blob location is never included: the client gets an id and a
        digest, and fetches the bytes through the owner-checked route.
        """
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "mime": self.mime,
            "size": self.size,
            "sha256": self.sha256,
            # Where it came from — ``sandbox:/workspace/report/x.md`` or ``chart:bar``. A
            # virtual path, not a host path, so it is safe to show and helps the reader tell
            # a report file from a chart.
            "source": self.source,
            # Stated rather than implied, so "the row exists" is never mistaken for
            # "the file is there".
            "download_ready": download_ready,
            "created_at": self.created_at,
        }


__all__ = [
    "ARTIFACT_ID_PREFIX",
    "DEFAULT_MIME",
    "KNOWN_MIME",
    "ArtifactError",
    "ArtifactRecord",
    "guess_mime",
    "new_artifact_id",
    "payload_sha256",
    "safe_filename",
]
