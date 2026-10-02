"""Restore the owner's persisted skills from the Store onto the sandbox.

Two recovery channels exist and they are counted separately (see
``contracts/skills-memory.md``): preset skills are re-synced from ``src/skills/`` by
:mod:`agent.middlewares.skills_sync`, while *user-published* skills are re-materialised
from the Store. This module owns the second channel.

Guarantees:

* **Verified before materialised.** Every file's SHA-256 is checked against the stored
  manifest before anything is written. A version whose manifest does not add up is
  reported as ``restore_failed`` and is **not** materialised — and, because it may be the
  currently assigned version, nothing already on disk is deleted either.
* **Empty state is normal.** A user who has published nothing gets a clean report, not an
  error and not an empty directory pretending to be a skill.
* **Atomic replacement.** Files are staged, then moved into place with a rename, so a
  concurrent reader never sees a half-written skill directory.
* **Store is the source.** Nothing is restored from a download or staging directory; a
  temporary directory must not be able to masquerade as a persisted version.
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from agent.main_agent import (
    SKILLS_ROOT,
    USERS_SEGMENT,
    OwnerScopeError,
    owner_key_prefix,
)

LOGGER = logging.getLogger("rush_harness.skills.restore")

MANIFEST_FILENAME = "manifest.json"
ASSIGNMENTS_SEGMENT = "assignments"

#: Where the agent finds restored user skills inside its sandbox.
USER_SKILLS_ROOT = f"{SKILLS_ROOT}/{USERS_SEGMENT}"

STAGING_ROOT = f"{SKILLS_ROOT}/.staging"

#: Stamp of the assignment revision last materialised into the sandbox.
#:
#: It lives in the container, not in this process, so a restarted API does not have to
#: re-download every file to find out whether anything changed — and a *rebuilt* container
#: has no marker at all, which is exactly the signal that everything must be restored again.
USER_SKILLS_REVISION_MARKER = f"{SKILLS_ROOT}/.user-skills-revision"

#: The assignment pointers this restorer understands.
KNOWN_SCOPES: tuple[str, ...] = ("main", "procurement-analyst", "procurement-order")


def assignments_revision(assignments: Sequence[SkillAssignment]) -> str:
    """A digest of *which* versions are assigned, independent of file content.

    Derived rather than counted, so nothing has to remember to bump it: the publisher moves
    the pointer, the digest changes, and the next ``before_agent`` sees a marker mismatch.
    A counter kept in two places is a counter that eventually disagrees with itself.
    """
    digest = hashlib.sha256()
    for assignment in sorted(assignments, key=lambda item: (item.scope, item.slug)):
        digest.update(f"{assignment.scope}/{assignment.slug}/{assignment.version}".encode())
        digest.update(b"\x00")
    return digest.hexdigest()


class ManifestIntegrityError(ValueError):
    """A stored version's manifest is missing, malformed, or does not match its files."""


@dataclass(frozen=True)
class SkillAssignment:
    """The pointer saying which version of one skill is currently published."""

    owner_user_id: str
    scope: str
    slug: str
    version: str

    @property
    def version_prefix(self) -> str:
        return f"{owner_key_prefix(self.owner_user_id)}/{self.scope}/{self.slug}/{self.version}"

    @property
    def assignment_key(self) -> str:
        return f"{owner_key_prefix(self.owner_user_id)}/{ASSIGNMENTS_SEGMENT}/{self.scope}/{self.slug}"

    @property
    def sandbox_directory(self) -> str:
        return f"{USER_SKILLS_ROOT}/{self.scope}/{self.slug}"


@dataclass
class RestoreReport:
    """What a restore did, per skill."""

    restored: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    empty: bool = False
    reason: str = ""
    generation: int | None = None
    #: Digest of which versions were assigned when this ran. The publisher moves it, and a
    #: marker mismatch is what makes the next call reload instead of trusting the cache.
    revision: str = ""

    @property
    def restored_count(self) -> int:
        return len(self.restored)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "generation": self.generation,
            "revision": self.revision,
            "empty": self.empty,
            "restored": self.restored,
            "skipped": self.skipped,
            "failed": self.failed,
        }


class StoreAssignmentReader:
    """Reads assignment pointers and file bytes out of a LangGraph Store.

    The publishing flow (T13) owns how versions come to exist; this reader only needs the
    two conventions: a pointer at ``users/{owner}/assignments/{scope}/{slug}`` holding
    ``{"version": "..."}``, and files under ``users/{owner}/{scope}/{slug}/{version}/``.
    """

    #: Page size for Store reads, and the ceiling beyond which a read is reported as
    #: truncated rather than silently returning a partial answer.
    PAGE_SIZE = 200
    MAX_ITEMS = 5000

    def __init__(
        self,
        store: Any,
        *,
        namespace: Sequence[str] = ("skills",),
        pointers: Any | None = None,
    ) -> None:
        self._store = store
        self._namespace = tuple(namespace)
        #: ``skill_assignments`` rows, when the caller has them. MongoDB holds the pointer's
        #: authority (it is the only store that can update it conditionally); the Store scan
        #: below stays as the fallback for callers that only have a Store.
        self._pointers = pointers

    def _items_with_key_prefix(self, key_prefix: str) -> list[Any]:
        """Every item whose key starts with ``key_prefix``.

        The Store's ``search`` has no key-prefix parameter (only ``namespace_prefix``), and
        its default ``limit`` is small, so the filtering is done here and the pages are
        walked explicitly. Truncation is reported, never silent.
        """
        matched: list[Any] = []
        offset = 0
        while True:
            page = self._store.search(
                self._namespace, limit=self.PAGE_SIZE, offset=offset
            )
            if not page:
                break
            matched.extend(item for item in page if str(item.key).startswith(key_prefix))
            if len(page) < self.PAGE_SIZE:
                break
            offset += self.PAGE_SIZE
            if offset >= self.MAX_ITEMS:
                LOGGER.warning(
                    "store scan stopped at %d items; results may be truncated for prefix %s",
                    self.MAX_ITEMS,
                    key_prefix,
                )
                break
        return matched

    def assignments_for(self, owner_user_id: str) -> list[SkillAssignment]:
        if self._pointers is not None:
            return [
                SkillAssignment(
                    owner_user_id=owner_user_id,
                    scope=pointer.scope,
                    slug=pointer.slug,
                    version=pointer.version,
                )
                for pointer in self._pointers.list_for_owner(owner_user_id)
            ]

        prefix = f"{owner_key_prefix(owner_user_id)}/{ASSIGNMENTS_SEGMENT}/"
        assignments: list[SkillAssignment] = []
        for scope in KNOWN_SCOPES:
            for item in self._items_with_key_prefix(f"{prefix}{scope}/"):
                key = str(item.key)
                version = None
                if isinstance(item.value, dict):
                    version = item.value.get("version")
                if not version:
                    LOGGER.warning("assignment %s has no version; ignored", key)
                    continue
                slug = key.rsplit("/", 1)[-1]
                assignments.append(
                    SkillAssignment(
                        owner_user_id=owner_user_id, scope=scope, slug=slug, version=str(version)
                    )
                )
        return assignments

    def file_bytes(self, prefix: str) -> dict[str, bytes]:
        """Every file of one version, keyed by its path relative to the version prefix."""
        payload: dict[str, bytes] = {}
        for item in self._items_with_key_prefix(f"{prefix}/"):
            relative = str(item.key)[len(prefix) + 1 :]
            if not relative:
                continue
            value = item.value
            if isinstance(value, dict) and "content" in value:
                value = value["content"]
            if isinstance(value, str):
                payload[relative] = value.encode("utf-8")
            elif isinstance(value, bytes):
                payload[relative] = value
            else:
                payload[relative] = json.dumps(value, ensure_ascii=False).encode("utf-8")
        return payload


class UserSkillsRestoreMiddleware(AgentMiddleware):
    """Materialises the owner's published skills onto the sandbox."""

    def __init__(
        self,
        *,
        backend_provider: Callable[[], Any],
        reader: StoreAssignmentReader,
        scopes: Sequence[str] = KNOWN_SCOPES,
    ) -> None:
        self._backend_provider = backend_provider
        self._reader = reader
        self._scopes = tuple(scopes)
        #: Last generation each owner was restored for. The marker in the container is the
        #: durable half of this check; this is the in-process half.
        self._last_generation: dict[str, int | None] = {}

    # --------------------------------------------------------------- discovery

    def restore(self, owner_user_id: str, *, generation: int | None = None) -> RestoreReport:
        """Restore every assigned skill for one owner."""
        if not owner_user_id:
            raise OwnerScopeError("owner_user_id is required to restore user skills")

        assignments = [
            assignment
            for assignment in self._reader.assignments_for(owner_user_id)
            if assignment.scope in self._scopes
        ]
        revision = assignments_revision(assignments)
        if not assignments:
            LOGGER.info("user=%s has no published skills; nothing to restore", owner_user_id)
            return RestoreReport(
                empty=True, reason="no_assignments", generation=generation, revision=revision
            )

        backend = self._backend_provider()

        # Nothing changed since the last restore *and* the container still says so. The
        # marker is read from the sandbox rather than trusted from this process: a rebuilt
        # container has no marker, which is the case that must not be skipped — a populated
        # container is the only evidence that the copies are actually there.
        if (
            generation is not None
            and self._last_generation.get(owner_user_id) == generation
            and _read_revision_marker(backend) == revision
        ):
            LOGGER.info("user=%s skills already at revision %s", owner_user_id, revision[:12])
            return RestoreReport(
                reason="up_to_date",
                skipped=[assignment.sandbox_directory for assignment in assignments],
                generation=generation,
                revision=revision,
            )

        report = RestoreReport(reason="restored", generation=generation, revision=revision)

        for assignment in assignments:
            target = assignment.sandbox_directory
            try:
                payload = self._reader.file_bytes(assignment.version_prefix)
                verified = verify_manifest(assignment, payload)
            except ManifestIntegrityError as failure:
                # Leave whatever is already on disk alone: this may be the assigned
                # version, and a failed restore must not delete a stored one.
                LOGGER.warning("restore failed for %s: %s", target, failure)
                report.failed.append(target)
                continue

            uploaded = atomic_replace(backend, target, verified, assignment)
            if uploaded:
                report.restored.append(target)
            else:
                report.skipped.append(target)

        if report.failed and not report.restored:
            report.reason = "restore_failed"
        elif report.skipped and not report.restored:
            report.reason = "up_to_date"

        # Only a clean run may claim the revision. A partial restore that recorded the marker
        # would make the next call skip the files this one failed to write, which is worse
        # than a slow call.
        #
        # The trade-off is real and worth stating: one permanently broken assignment means the
        # short-circuit never engages for that owner, so every turn re-scans and re-verifies.
        # That is a performance cost, not a correctness one — the broken skill keeps being
        # reported, which is the outcome that matters — and making it finer-grained would mean
        # caching a failure set, which is a bigger claim than this revision can support.
        if not report.failed:
            _write_revision_marker(backend, revision)
            self._last_generation[owner_user_id] = generation
        else:
            LOGGER.warning(
                "not caching revision %s for %s: %d assignment(s) failed to restore",
                revision[:12],
                owner_user_id,
                len(report.failed),
            )
        return report

    # -------------------------------------------------------------- middleware

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ANN401
        owner = _owner_from(runtime)
        if not owner:
            return None
        generation = _generation_from(runtime)
        report = self.restore(owner, generation=generation)
        return {
            "user_skills_restored": report.restored_count,
            "user_skills_revision": report.revision,
        }

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ANN401
        import asyncio

        owner = _owner_from(runtime)
        if not owner:
            return None
        generation = _generation_from(runtime)
        report = await asyncio.to_thread(self.restore, owner, generation=generation)
        return {
            "user_skills_restored": report.restored_count,
            "user_skills_revision": report.revision,
        }


# --------------------------------------------------------------------------- #
# manifest verification and materialisation
# --------------------------------------------------------------------------- #


def _read_revision_marker(backend: Any) -> str:
    """The revision the container currently holds, or an empty string when there is none."""
    import shlex

    result = backend.execute(
        f"cat {shlex.quote(USER_SKILLS_REVISION_MARKER)} 2>/dev/null || true"
    )
    return (result.output or "").strip().splitlines()[0] if (result.output or "").strip() else ""


def _write_revision_marker(backend: Any, revision: str) -> None:
    backend.upload_files([(USER_SKILLS_REVISION_MARKER, f"{revision}\n".encode())])


def verify_manifest(
    assignment: SkillAssignment, payload: dict[str, bytes]
) -> dict[str, bytes]:
    """Check the stored manifest against the stored files.

    Returns the file map with the manifest itself removed (it is bookkeeping, not part of
    the skill an agent reads). Raises :class:`ManifestIntegrityError` when anything is
    missing, unparsable, or does not match its recorded digest.
    """
    if MANIFEST_FILENAME not in payload:
        raise ManifestIntegrityError(f"{assignment.slug}: stored manifest is missing")

    try:
        manifest = json.loads(payload[MANIFEST_FILENAME].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as failure:
        raise ManifestIntegrityError(f"{assignment.slug}: manifest is not valid JSON: {failure}") from failure

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ManifestIntegrityError(f"{assignment.slug}: manifest lists no files")

    declared: dict[str, str] = {}
    for entry in files:
        if not isinstance(entry, dict) or "path" not in entry or "sha256" not in entry:
            raise ManifestIntegrityError(f"{assignment.slug}: manifest entry is malformed: {entry!r}")
        relative = str(entry["path"])
        if relative.startswith("/") or ".." in relative.split("/"):
            raise ManifestIntegrityError(f"{assignment.slug}: unsafe manifest path {relative!r}")
        declared[relative] = str(entry["sha256"])

    verified: dict[str, bytes] = {}
    for relative, expected in declared.items():
        if relative not in payload:
            raise ManifestIntegrityError(
                f"{assignment.slug}: manifest declares {relative!r} but it is not stored"
            )
        actual = hashlib.sha256(payload[relative]).hexdigest()
        if actual != expected:
            raise ManifestIntegrityError(
                f"{assignment.slug}: {relative!r} digest mismatch (expected {expected[:12]}…, "
                f"got {actual[:12]}…)"
            )
        verified[relative] = payload[relative]

    if SKILL_DOCUMENT not in verified:
        raise ManifestIntegrityError(f"{assignment.slug}: manifest lacks {SKILL_DOCUMENT}")

    return verified


SKILL_DOCUMENT = "SKILL.md"


def atomic_replace(
    backend: Any,
    target: str,
    files: dict[str, bytes],
    assignment: SkillAssignment,
) -> bool:
    """Stage the files, then rename the directory into place.

    A rename is atomic within one filesystem, so a reader either sees the previous version
    or the new one — never a partially written skill directory.
    """
    if not files:
        return False

    previous = _read_directory(backend, target)
    if previous is not None and _matches(previous, files):
        return False

    staging = f"{STAGING_ROOT}/{assignment.scope}-{assignment.slug}"
    uploads = [(f"{staging}/{relative}", content) for relative, content in sorted(files.items())]
    responses = backend.upload_files(uploads)
    failures = [response for response in responses if getattr(response, "error", None)]
    if failures:
        raise ManifestIntegrityError(
            f"{assignment.slug}: staging upload failed for "
            + ", ".join(f"{item.path}: {item.error}" for item in failures)
        )

    parent = posixpath.dirname(target)
    script = (
        f"set -e; mkdir -p '{parent}'; rm -rf '{target}.old'; "
        f"if [ -d '{target}' ]; then mv '{target}' '{target}.old'; fi; "
        f"mv '{staging}' '{target}'; rm -rf '{target}.old'"
    )
    result = backend.execute(script)
    if getattr(result, "exit_code", 1) != 0:
        raise ManifestIntegrityError(
            f"{assignment.slug}: atomic replace failed: {getattr(result, 'output', '')[:200]}"
        )
    return True


def _read_directory(backend: Any, target: str) -> dict[str, bytes] | None:
    listing = backend.ls(target)
    if getattr(listing, "error", None):
        return None
    entries = getattr(listing, "entries", None) or []
    files = [
        entry["path"]
        for entry in entries
        if not entry.get("is_dir") and isinstance(entry.get("path"), str)
    ]
    if not files:
        return None
    responses = backend.download_files(files)
    payload: dict[str, bytes] = {}
    for response in responses:
        if response.error or response.content is None:
            return None
        payload[posixpath.relpath(response.path, target)] = response.content
    return payload


def _matches(existing: dict[str, bytes], incoming: dict[str, bytes]) -> bool:
    if set(existing) != set(incoming):
        return False
    return all(
        hashlib.sha256(existing[relative]).digest() == hashlib.sha256(content).digest()
        for relative, content in incoming.items()
    )


def _owner_from(runtime: Any) -> str | None:
    from agent.runtime_context import runtime_owner

    return runtime_owner(runtime)


def _generation_from(runtime: Any) -> int | None:
    context = getattr(runtime, "context", None)
    for source in (context, runtime):
        if source is None:
            continue
        value = getattr(source, "sandbox_generation", None)
        if isinstance(value, int):
            return value
        if isinstance(source, dict) and isinstance(source.get("sandbox_generation"), int):
            return source["sandbox_generation"]
    return None


__all__ = [
    "ASSIGNMENTS_SEGMENT",
    "KNOWN_SCOPES",
    "MANIFEST_FILENAME",
    "SKILL_DOCUMENT",
    "STAGING_ROOT",
    "USER_SKILLS_REVISION_MARKER",
    "USER_SKILLS_ROOT",
    "ManifestIntegrityError",
    "RestoreReport",
    "SkillAssignment",
    "StoreAssignmentReader",
    "UserSkillsRestoreMiddleware",
    "assignments_revision",
    "atomic_replace",
    "verify_manifest",
]
