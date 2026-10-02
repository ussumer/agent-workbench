"""Where a published skill lives: files in the Store, publish metadata in MongoDB.

The split is forced by what each store can do, and it is the same split
``docs/plan/contracts/storage-sandbox.md`` already names (``skill_versions``,
``skill_assignments``):

* **Files** go to the LangGraph Store under ``users/{owner}/{scope}/{slug}/{version}/``,
  because that is where the restore path reads them from and the contract fixes that key
  shape (``contracts/skills-memory.md`` item 6).
* **Pointers** go to MongoDB, because assigning a version is a *conditional update* —
  "move the current pointer to this version, but only if nobody else moved it" — and the
  Store has no compare-and-swap. A read-then-write would pass a functional test and lose
  the race, which is exactly the mistake ``agent/approval/store.py`` exists to avoid.

Versions are immutable: a version prefix is written once and never rewritten. That is what
makes "same content returns the original version" a lookup rather than a guess, and it means
a failed publish leaves earlier versions untouched.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from agent.main_agent import owner_key_prefix

LOGGER = logging.getLogger("rush_harness.skills.store")

#: Where the pointer for an assignment lives inside the Store namespace. Kept in step with
#: the key ``StoreAssignmentReader`` scans, so a Store-backed reader still finds it.
ASSIGNMENTS_SEGMENT = "assignments"

MANIFEST_FILENAME = "manifest.json"

#: Store namespace for published skill files. The course namespace, as the contract requires.
DEFAULT_NAMESPACE: tuple[str, ...] = ("skills",)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class SkillVersion:
    """One immutable published version."""

    owner_user_id: str
    scope: str
    slug: str
    version: str
    content_sha256: str
    manifest_sha256: str
    source_type: str
    source: str
    source_sha256: str
    file_count: int
    total_size: int
    scripts_entry: str | None
    created_at: str

    @property
    def store_prefix(self) -> str:
        """The Store key prefix holding this version's files."""
        return f"{owner_key_prefix(self.owner_user_id)}/{self.scope}/{self.slug}/{self.version}"

    def as_document(self) -> dict[str, Any]:
        return {
            "owner_user_id": self.owner_user_id,
            "scope": self.scope,
            "slug": self.slug,
            "version": self.version,
            "content_sha256": self.content_sha256,
            "manifest_sha256": self.manifest_sha256,
            "source_type": self.source_type,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "scripts_entry": self.scripts_entry,
            "created_at": self.created_at,
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> SkillVersion:
        return cls(
            owner_user_id=str(document["owner_user_id"]),
            scope=str(document["scope"]),
            slug=str(document["slug"]),
            version=str(document["version"]),
            content_sha256=str(document.get("content_sha256") or ""),
            manifest_sha256=str(document.get("manifest_sha256") or ""),
            source_type=str(document.get("source_type") or ""),
            source=str(document.get("source") or ""),
            source_sha256=str(document.get("source_sha256") or ""),
            file_count=int(document.get("file_count") or 0),
            total_size=int(document.get("total_size") or 0),
            scripts_entry=(str(document["scripts_entry"]) if document.get("scripts_entry") else None),
            created_at=str(document.get("created_at") or ""),
        )


@dataclass(frozen=True)
class SkillPointer:
    """The current version of one ``owner + scope + slug``, plus the revision that guards it."""

    owner_user_id: str
    scope: str
    slug: str
    version: str
    #: Monotonic. Every move bumps it, so a conditional update has something to filter on.
    revision: int
    updated_at: str

    def as_document(self) -> dict[str, Any]:
        return {
            "owner_user_id": self.owner_user_id,
            "scope": self.scope,
            "slug": self.slug,
            "version": self.version,
            "revision": self.revision,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> SkillPointer:
        return cls(
            owner_user_id=str(document["owner_user_id"]),
            scope=str(document["scope"]),
            slug=str(document["slug"]),
            version=str(document["version"]),
            revision=int(document.get("revision") or 0),
            updated_at=str(document.get("updated_at") or ""),
        )


class SkillStore:
    """Published skill files (Store) and publish metadata (MongoDB)."""

    def __init__(
        self,
        store: Any,
        database: Any,
        *,
        namespace: Sequence[str] = DEFAULT_NAMESPACE,
    ) -> None:
        from agent.persistence.indexes import (
            COLLECTION_SKILL_ASSIGNMENTS,
            COLLECTION_SKILL_SMOKE_ATTEMPTS,
            COLLECTION_SKILL_VERSIONS,
        )

        self._store = store
        self._versions = database[COLLECTION_SKILL_VERSIONS]
        self._assignments = database[COLLECTION_SKILL_ASSIGNMENTS]
        self._smoke_attempts = database[COLLECTION_SKILL_SMOKE_ATTEMPTS]
        self._namespace = tuple(namespace)

    # ------------------------------------------------------------------ repairs

    def smoke_failures(self, owner_user_id: str, thread_id: str, slug: str) -> int:
        """How many failed smoke runs this conversation has already spent on ``slug``.

        Kept here rather than in the publishing loop because the repair is now the *model's*
        turn: it writes a skill, the smoke test fails, it reads why and edits the files, and it
        calls again. That loop spans tool calls, so a counter local to one call would reset
        every time and bound nothing. Keyed by thread as well as owner so one slug being fixed
        in a conversation is not charged to a different one.
        """
        document = self._smoke_attempts.find_one(
            {"owner_user_id": owner_user_id, "thread_id": thread_id, "slug": slug}
        )
        return int((document or {}).get("failures") or 0)

    def note_smoke_failure(self, owner_user_id: str, thread_id: str, slug: str) -> int:
        """Record one failed smoke run. Returns the total spent, including this one."""
        document = self._smoke_attempts.find_one_and_update(
            {"owner_user_id": owner_user_id, "thread_id": thread_id, "slug": slug},
            {
                "$inc": {"failures": 1},
                "$set": {"updated_at": datetime.now(UTC).isoformat()},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int((document or {}).get("failures") or 0)

    def clear_smoke_failures(self, owner_user_id: str, thread_id: str, slug: str) -> None:
        """Forget the count — a skill that published is not being repaired any more."""
        self._smoke_attempts.delete_one(
            {"owner_user_id": owner_user_id, "thread_id": thread_id, "slug": slug}
        )

    # ------------------------------------------------------------------ files

    def write_version_files(
        self, version: SkillVersion, files: dict[str, bytes], manifest: dict[str, Any]
    ) -> None:
        """Write every file plus the manifest under the version prefix. Never overwrites.

        A pre-existing version prefix is refused rather than merged: versions are immutable,
        and silently accepting a rewrite would mean two different skills could share a
        version number depending on which publish ran last.
        """
        prefix = version.store_prefix
        existing = (self._store.get(self._namespace, f"{prefix}/{MANIFEST_FILENAME}") is not None
                    or any(self._store.get(self._namespace, f"{prefix}/{name}") is not None for name in files)
                    or self._items_with_prefix(f"{prefix}/"))
        if existing:
            raise ValueError(f"version {version.version} already exists for {version.slug}")

        self._store.put(
            self._namespace, f"{prefix}/{MANIFEST_FILENAME}", serialise_manifest(manifest)
        )
        # Bytes go in as raw bytes, so a script is read back byte-identical instead of being
        # round-tripped through text and mangled.
        for relative, payload in files.items():
            self._store.put(self._namespace, f"{prefix}/{relative}", payload)

    def read_version_files(self, version: SkillVersion) -> dict[str, bytes]:
        return self.file_bytes(version.store_prefix)

    def file_bytes(self, prefix: str) -> dict[str, bytes]:
        """Every file under one version prefix, keyed relative to it."""
        import json

        payload: dict[str, bytes] = {}
        for item in self._items_with_prefix(f"{prefix}/"):
            relative = str(item.key)[len(prefix) + 1 :]
            if not relative:
                continue
            value = item.value
            if isinstance(value, dict) and "content" in value:
                value = value["content"]
            if isinstance(value, bytes):
                payload[relative] = value
            elif isinstance(value, str):
                payload[relative] = value.encode("utf-8")
            else:
                payload[relative] = json.dumps(value, ensure_ascii=False).encode("utf-8")
        return payload

    def _items_with_prefix(self, key_prefix: str, *, page_size: int = 200) -> list[Any]:
        """Walk the Store for keys under ``key_prefix``.

        The Store's ``search`` filters by namespace prefix only and defaults to a small
        limit, so filtering and paging are explicit here. Integrity checks must reach the
        end rather than silently treating a truncated scan as the complete version.
        """
        matched: list[Any] = []
        offset = 0
        while True:
            page = self._store.search(self._namespace, limit=page_size, offset=offset)
            if not page:
                break
            matched.extend(item for item in page if str(item.key).startswith(key_prefix))
            if len(page) < page_size:
                break
            offset += page_size
        return matched

    # --------------------------------------------------------------- versions

    def reserve_version(self, owner_user_id: str, scope: str, slug: str) -> tuple[str, str]:
        """Claim an immutable number before Store writes, including failed reservations.

        The existing unique version index arbitrates concurrent claims across processes.
        A reservation is durable but never returned as a published version.
        """
        identity = {"owner_user_id": owner_user_id, "scope": scope, "slug": slug}
        numbers = []
        for document in self._versions.find(identity, {"version": 1}):
            match = re.fullmatch(r"1\.0\.(\d+)", str(document.get("version", "")))
            if match:
                numbers.append(int(match.group(1)))
        number = max(numbers, default=-1) + 1
        for _attempt in range(100):
            version = f"1.0.{number}"
            token = uuid4().hex
            try:
                self._versions.insert_one({**identity, "version": version,
                    "publication_status": "reserved", "reservation_id": token, "created_at": _now()})
            except DuplicateKeyError:
                number += 1
                continue
            prefix = f"{owner_key_prefix(owner_user_id)}/{scope}/{slug}/{version}/"
            if (self._store.get(self._namespace, f"{prefix}{MANIFEST_FILENAME}") is not None
                    or self._items_with_prefix(prefix)):
                # Older releases could leave files without any metadata reservation.
                self.fail_reservation(owner_user_id, scope, slug, version, token, "LEGACY_PREFIX_EXISTS")
                number += 1
                continue
            return version, token
        raise RuntimeError("Could not reserve a skill version after concurrent conflicts")

    def fail_reservation(self, owner: str, scope: str, slug: str, version: str,
                         token: str, reason: str) -> None:
        self._versions.update_one(
            {"owner_user_id": owner, "scope": scope, "slug": slug, "version": version,
             "publication_status": "reserved", "reservation_id": token},
            {"$set": {"publication_status": "failed", "failure_type": reason}},
        )

    @staticmethod
    def _published() -> dict[str, Any]:
        # Records created before reservations were introduced are published metadata.
        return {"$or": [{"publication_status": {"$exists": False}}, {"publication_status": "persisted"}]}

    def record_version(self, version: SkillVersion, *, reservation_id: str | None = None) -> SkillVersion:
        """Finish our reserved row conditionally, or insert legacy/imported metadata."""
        if reservation_id is None:
            self._versions.insert_one(version.as_document())
        else:
            result = self._versions.replace_one(
                {"owner_user_id": version.owner_user_id, "scope": version.scope,
                 "slug": version.slug, "version": version.version,
                 "publication_status": "reserved", "reservation_id": reservation_id},
                {**version.as_document(), "publication_status": "persisted", "reservation_id": reservation_id},
            )
            if result.modified_count != 1:
                raise RuntimeError("Skill reservation was lost before publication")
        return version

    def get_version(
        self, owner_user_id: str, scope: str, slug: str, version: str
    ) -> SkillVersion | None:
        document = self._versions.find_one(
            {
                "owner_user_id": owner_user_id,
                "scope": scope,
                "slug": slug,
                "version": version,
                **self._published(),
            }
        )
        return SkillVersion.from_document(document) if document else None

    def find_by_content(
        self, owner_user_id: str, scope: str, slug: str, content_sha256: str
    ) -> SkillVersion | None:
        """The existing version with this exact content, if any.

        This is what makes "same content returns the original version" a fact rather than a
        hope: the digest is recorded when the version is created, so the lookup does not
        have to re-read and re-hash every version on every publish.
        """
        document = self._versions.find_one(
            {
                "owner_user_id": owner_user_id,
                "scope": scope,
                "slug": slug,
                "content_sha256": content_sha256,
                **self._published(),
            },
            sort=[("created_at", DESCENDING)],
        )
        return SkillVersion.from_document(document) if document else None

    def list_versions(self, owner_user_id: str, scope: str, slug: str) -> list[SkillVersion]:
        cursor = self._versions.find(
            {"owner_user_id": owner_user_id, "scope": scope, "slug": slug, **self._published()}
        ).sort("created_at", ASCENDING)
        return [SkillVersion.from_document(document) for document in cursor]

    # ------------------------------------------------------------- assignments

    def current_assignment(
        self, owner_user_id: str, scope: str, slug: str
    ) -> SkillPointer | None:
        document = self._assignments.find_one(
            {"owner_user_id": owner_user_id, "scope": scope, "slug": slug}
        )
        return SkillPointer.from_document(document) if document else None

    def list_for_owner(self, owner_user_id: str) -> list[SkillPointer]:
        """Every assignment for one owner.

        ``StoreAssignmentReader`` calls this when it is given a pointer store, which is how
        the restore path sees the real pointers instead of scanning keys.
        """
        cursor = self._assignments.find({"owner_user_id": owner_user_id}).sort(
            "scope", ASCENDING
        )
        return [SkillPointer.from_document(document) for document in cursor]

    def assign(
        self,
        owner_user_id: str,
        scope: str,
        slug: str,
        version: str,
        *,
        expected_revision: int | None,
    ) -> SkillPointer | None:
        """Move the current pointer, or return ``None`` if somebody else already moved it.

        The whole guarantee is in the filter. ``expected_revision`` is the revision the
        caller read; the update matches only while the stored revision is still that value,
        so two publishes racing for the same slug cannot both win, and ``None`` means "you
        lost" rather than "an error occurred" — the same contract as
        ``agent/approval/store.py``.

        ``expected_revision=None`` means "there is no pointer yet", and the filter asserts
        exactly that, so a first publish cannot clobber one that appeared in the meantime.
        """
        if self.get_version(owner_user_id, scope, slug, version) is None:
            LOGGER.info("refusing assignment of unpublished version %s/%s/%s", scope, slug, version)
            return None
        now = _now()
        if expected_revision is None:
            # "There is no pointer yet", asserted rather than assumed. A find-and-update with
            # upsert would happily match an existing row, so the creation path is an *insert*
            # and the unique index on (owner, scope, slug) is what decides who was first.
            document: dict[str, Any] = {
                "owner_user_id": owner_user_id,
                "scope": scope,
                "slug": slug,
                "version": version,
                "revision": 1,
                "updated_at": now,
            }
            try:
                self._assignments.insert_one(dict(document))
            except DuplicateKeyError:
                LOGGER.info("assignment for %s/%s already exists; not overwriting", scope, slug)
                return None
            return SkillPointer.from_document(document)

        document = self._assignments.find_one_and_update(
            {
                "owner_user_id": owner_user_id,
                "scope": scope,
                "slug": slug,
                "revision": expected_revision,
            },
            {
                "$set": {
                    "version": version,
                    "revision": expected_revision + 1,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return SkillPointer.from_document(document) if document else None

    def withdraw(self, owner_user_id: str, scope: str, slug: str) -> bool:
        """Remove one assignment. Other scopes for the same slug are untouched.

        Only the pointer goes; the versions stay, so re-assigning is a pointer move rather
        than a re-publish, and a withdrawal cannot destroy a stored version.
        """
        result = self._assignments.delete_one(
            {"owner_user_id": owner_user_id, "scope": scope, "slug": slug}
        )
        return bool(result.deleted_count)

    def store_pointer_mirror(
        self, owner_user_id: str, scope: str, slug: str, version: str, revision: int
    ) -> None:
        """Also write the pointer under the Store key the reading path has always used.

        MongoDB holds the pointer's authority; this mirror is what a Store-backed reader
        scans. It is written *after* the conditional update succeeds, so it can only ever
        trail the authority — never contradict it — and a crash between the two leaves a
        stale mirror that the next successful assign overwrites.
        """
        import json

        key = f"{owner_key_prefix(owner_user_id)}/{ASSIGNMENTS_SEGMENT}/{scope}/{slug}"
        self._store.put(
            self._namespace, key, json.dumps({"version": version, "revision": revision})
        )


def serialise_manifest(manifest: Mapping[str, Any]) -> bytes:
    """The one serialisation of a manifest.

    Kept in one place because the digest recorded on the version row has to be the digest of
    the bytes actually written. Two ``json.dumps`` calls with different arguments produce two
    different files and the recorded hash would describe neither.
    """
    import json

    return json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")


def content_digest(files: dict[str, bytes]) -> str:
    """A digest over the whole version's content, stable across orderings.

    Paths are part of the hash, not just bytes: moving a script from ``scripts/a.py`` to
    ``a.py`` is a different skill even though the byte multiset is identical.
    """
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(hashlib.sha256(files[path]).digest())
    return digest.hexdigest()


__all__ = [
    "ASSIGNMENTS_SEGMENT",
    "DEFAULT_NAMESPACE",
    "MANIFEST_FILENAME",
    "SkillPointer",
    "SkillStore",
    "SkillVersion",
    "content_digest",
    "serialise_manifest",
]
