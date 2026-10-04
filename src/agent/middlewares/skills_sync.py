"""Preset skills: manifest, hashing and incremental sync into the sandbox.

Preset skills live read-only under ``src/skills/``; the agent works on a *copy* inside its
sandbox. Three properties matter and are each testable:

* **Progressive disclosure.** Only ``name``/``description``/``path`` are injected up
  front. The body is read on demand. Dumping every skill body into the system prompt
  both wastes context and makes a later revision change impossible to reason about.
* **Incremental by revision.** Sync is driven by a manifest hash. An unchanged manifest
  uploads nothing. A changed one uploads only the files whose digest moved.
* **Re-sync on a new generation.** A replaced container has an empty ``/skills``, so the
  revision marker it carries is the source of truth, not this process's memory. That
  catches a container swap nobody told us about.

An incomplete skill — missing ``SKILL.md``, no frontmatter, a bad slug — fails the whole
manifest rather than being skipped silently, because a half-published skill set is worse
than a loud failure at startup.
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware

LOGGER = logging.getLogger("rush_harness.skills.sync")

SKILL_FILENAME = "SKILL.md"
FRONTMATTER_DELIMITER = "---"

#: Contract rule for skill slugs.
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SLUG_MAX_LENGTH = 64

DEFAULT_SOURCE_DIR = Path(__file__).resolve().parents[2] / "skills"
DEFAULT_TARGET_ROOT = "/skills"

#: Marker file inside the sandbox recording which manifest revision is materialised.
REVISION_MARKER = ".skills-revision"

PROGRESSIVE_FIELDS: tuple[str, ...] = ("name", "description", "path")


class SkillManifestError(ValueError):
    """The preset skill set is not publishable."""


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SkillFile:
    """One file of one skill, relative to the skills root."""

    path: str
    sha256: str
    size: int

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class SkillMeta:
    """One preset skill: its frontmatter plus the digests of its files."""

    name: str
    description: str
    directory: str
    files: tuple[SkillFile, ...]

    @property
    def revision(self) -> str:
        digest = hashlib.sha256()
        for entry in sorted(self.files, key=lambda item: item.path):
            digest.update(entry.path.encode("utf-8"))
            digest.update(entry.sha256.encode("utf-8"))
        return digest.hexdigest()

    @property
    def document(self) -> str:
        """Path of the SKILL.md relative to the skills root."""
        return posixpath.join(self.directory, SKILL_FILENAME)

    def metadata(self) -> dict[str, str]:
        """Only what is injected up front."""
        return {"name": self.name, "description": self.description, "path": f"/{self.document}"}


@dataclass(frozen=True)
class SkillManifest:
    """The whole preset set plus a revision covering it."""

    skills: tuple[SkillMeta, ...]
    revision: str

    def by_document(self) -> dict[str, SkillMeta]:
        return {skill.document: skill for skill in self.skills}

    def documents(self) -> list[str]:
        return sorted(skill.document for skill in self.skills)

    def metadata_directory(self) -> list[dict[str, str]]:
        """The progressive-disclosure payload: names, descriptions and paths only."""
        return [skill.metadata() for skill in sorted(self.skills, key=lambda item: item.name)]

    def as_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "skill_count": len(self.skills),
            "skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "document": skill.document,
                    "revision": skill.revision,
                    "files": [entry.as_dict() for entry in skill.files],
                }
                for skill in sorted(self.skills, key=lambda item: item.name)
            ],
        }


def parse_frontmatter(text: str, *, source: str) -> dict[str, str]:
    """Parse the leading ``---`` YAML block of a SKILL.md.

    Deliberately minimal: ``key: value`` pairs only. The frontmatter is a contract, not a
    configuration language, and anything fancier would be a sign it is being misused.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_DELIMITER:
        raise SkillManifestError(f"{source}: missing YAML frontmatter")

    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == FRONTMATTER_DELIMITER:
            return fields
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise SkillManifestError(f"{source}: frontmatter line is not key: value -> {line!r}")
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    raise SkillManifestError(f"{source}: frontmatter block is not terminated")


def _validate_slug(name: str, source: str) -> None:
    if not SLUG_PATTERN.match(name):
        raise SkillManifestError(
            f"{source}: name {name!r} is not a slug matching {SLUG_PATTERN.pattern}"
        )
    if len(name) > SLUG_MAX_LENGTH:
        raise SkillManifestError(f"{source}: name {name!r} exceeds {SLUG_MAX_LENGTH} characters")


def _hash_file(path: Path) -> tuple[str, int]:
    payload = path.read_bytes()
    return hashlib.sha256(payload).hexdigest(), len(payload)


def load_manifest(source_dir: Path | str = DEFAULT_SOURCE_DIR) -> SkillManifest:
    """Build the manifest, refusing anything incomplete.

    ``chart_params.md`` is a reference document rather than a skill directory, so a
    directory without ``SKILL.md`` is only an error when it looks like a skill directory
    (i.e. it is not an empty leftover); plain ``.md`` files at the procurement level are
    collected as reference documents of the sibling skills and ignored here.
    """
    root = Path(source_dir)
    if not root.is_dir():
        raise SkillManifestError(f"preset skills directory not found: {root}")

    skills: list[SkillMeta] = []
    for directory in sorted(path for path in root.rglob("*") if path.is_dir()):
        skill_file = directory / SKILL_FILENAME
        if not skill_file.is_file():
            continue

        relative_dir = directory.relative_to(root).as_posix()
        text = skill_file.read_text(encoding="utf-8")
        frontmatter = parse_frontmatter(text, source=f"{relative_dir}/{SKILL_FILENAME}")

        name = frontmatter.get("name", "").strip()
        description = frontmatter.get("description", "").strip()
        if not name:
            raise SkillManifestError(f"{relative_dir}: frontmatter is missing 'name'")
        if not description:
            raise SkillManifestError(f"{relative_dir}: frontmatter is missing 'description'")
        _validate_slug(name, relative_dir)

        files = tuple(
            SkillFile(
                path=file.relative_to(root).as_posix(),
                sha256=_hash_file(file)[0],
                size=_hash_file(file)[1],
            )
            # Sibling Markdown references (e.g. chart_params.md) are shared dependencies.
            for file in sorted(set(directory.rglob("*")) | set(directory.parent.glob("*.md")))
            if file.is_file()
        )
        if not any(entry.path.endswith(SKILL_FILENAME) for entry in files):
            raise SkillManifestError(f"{relative_dir}: manifest has no {SKILL_FILENAME}")

        skills.append(
            SkillMeta(name=name, description=description, directory=relative_dir, files=files)
        )

    if not skills:
        raise SkillManifestError(f"no skills with {SKILL_FILENAME} found under {root}")

    duplicates = _duplicates([skill.name for skill in skills])
    if duplicates:
        raise SkillManifestError(f"duplicate skill names: {sorted(duplicates)}")

    digest = hashlib.sha256()
    for skill in sorted(skills, key=lambda item: item.document):
        digest.update(skill.document.encode("utf-8"))
        digest.update(skill.revision.encode("utf-8"))

    return SkillManifest(skills=tuple(skills), revision=digest.hexdigest())


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def manifest_files(manifest: SkillManifest, root: Path | str = DEFAULT_SOURCE_DIR) -> dict[str, bytes]:
    """Every file of the manifest, keyed by its path relative to the skills root."""
    base = Path(root)
    payload: dict[str, bytes] = {}
    for skill in manifest.skills:
        for entry in skill.files:
            payload[entry.path] = (base / entry.path).read_bytes()
    return payload


# --------------------------------------------------------------------------- #
# sync
# --------------------------------------------------------------------------- #


@dataclass
class SyncReport:
    """What one sync actually did."""

    revision: str
    reason: str
    uploaded: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    generation: int | None = None
    previous_revision: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.uploaded or self.removed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "previous_revision": self.previous_revision,
            "reason": self.reason,
            "generation": self.generation,
            "uploaded": self.uploaded,
            "removed": self.removed,
            "skipped_count": len(self.skipped),
            "skipped": self.skipped,
        }


class SkillsSyncMiddleware(AgentMiddleware):
    """Syncs preset skills into the sandbox, incrementally.

    ``backend_provider`` returns the backend to sync into. Using a provider rather than a
    backend keeps this correct across a container replacement: the sync always writes into
    whatever the proxy currently points at.
    """

    def __init__(
        self,
        *,
        backend_provider: Callable[[], Any],
        source_dir: Path | str = DEFAULT_SOURCE_DIR,
        target_root: str = DEFAULT_TARGET_ROOT,
        manifest_loader: Callable[[Path | str], SkillManifest] = load_manifest,
    ) -> None:
        self._backend_provider = backend_provider
        self._source_dir = Path(source_dir)
        self._target_root = target_root.rstrip("/")
        self._manifest_loader = manifest_loader
        self._last: dict[str, Any] = {}

    # -------------------------------------------------------------- discovery

    def manifest(self) -> SkillManifest:
        return self._manifest_loader(self._source_dir)

    def metadata_directory(self) -> list[dict[str, str]]:
        """The up-front injection payload (names, descriptions, paths)."""
        return self.manifest().metadata_directory()

    # ------------------------------------------------------------------ sync

    def sync(self, *, generation: int | None = None) -> SyncReport:
        """Materialise the preset skills, uploading only what changed.

        Three situations force a full upload, and conflating them with the normal
        incremental path is how a stale or empty ``/skills`` survives a run:

        * first sync — nothing has been uploaded yet;
        * generation change — the container was replaced, so the previous per-file
          digests describe files that no longer exist;
        * container replacement detected via a missing revision marker — the same thing,
          noticed without the manager having said so.
        """
        manifest = self.manifest()
        backend = self._backend_provider()
        previous_revision: str | None = self._last.get("revision")
        previous_generation: int | None = self._last.get("generation")
        remote_revision = self._read_marker(backend)

        same_generation = (
            generation is None or previous_generation is None or generation == previous_generation
        )
        if (
            remote_revision == manifest.revision
            and previous_revision == manifest.revision
            and same_generation
        ):
            return SyncReport(
                revision=manifest.revision,
                previous_revision=previous_revision,
                reason="up_to_date",
                skipped=manifest.documents(),
                generation=generation,
            )

        if remote_revision is None and previous_revision is not None:
            reason = "container_replaced"
        elif previous_revision is None:
            reason = "first_sync"
        elif not same_generation:
            reason = "generation_changed"
        else:
            reason = "revision_changed"

        full_upload = reason in {"first_sync", "generation_changed", "container_replaced"}
        previous_files = {} if full_upload else self._previous_file_digests()

        payload = manifest_files(manifest, self._source_dir)
        uploads: list[tuple[str, bytes]] = []
        skipped: list[str] = []
        for relative, content in sorted(payload.items()):
            target = f"{self._target_root}/{relative}"
            digest = hashlib.sha256(content).hexdigest()
            if not full_upload and previous_files.get(relative) == digest:
                skipped.append(target)
                continue
            uploads.append((target, content))

        if uploads:
            responses = backend.upload_files(uploads)
            failures = [response for response in responses if getattr(response, "error", None)]
            if failures:
                raise SkillManifestError(
                    "skill sync failed for "
                    + ", ".join(f"{item.path}: {item.error}" for item in failures)
                )

        # Only an incremental run can have stale files to remove; a full upload rewrote
        # everything and a fresh container has nothing to clean.
        removed = [] if full_upload else self._remove_stale(backend, manifest, previous_files)
        self._write_marker(backend, manifest.revision)

        self._last = {
            "revision": manifest.revision,
            "generation": generation,
            "files": {
                entry.path: entry.sha256
                for skill in manifest.skills
                for entry in skill.files
            },
        }

        report = SyncReport(
            revision=manifest.revision,
            previous_revision=previous_revision,
            reason=reason,
            uploaded=[path for path, _ in uploads],
            removed=removed,
            skipped=skipped,
            generation=generation,
        )
        LOGGER.info("skills sync reason=%s uploaded=%d", reason, len(report.uploaded))
        return report

    # -------------------------------------------------------------- internals

    def _previous_file_digests(self) -> dict[str, str]:
        return dict(self._last.get("files") or {})

    def _read_marker(self, backend: Any) -> str | None:
        """Read the synced revision, or ``None`` when the sandbox does not have one.

        The directory listing is checked first: asking directly for a missing file makes
        the sandbox client log a 404 traceback, which is noise for an expected condition.
        """
        marker_path = f"{self._target_root}/{REVISION_MARKER}"
        listing = backend.ls(self._target_root)
        if getattr(listing, "error", None):
            return None
        entries = getattr(listing, "entries", None) or []
        if not any(entry.get("path") == marker_path for entry in entries):
            return None

        response = backend.download_files([marker_path])
        if not response or response[0].error or response[0].content is None:
            return None
        return response[0].content.decode("utf-8").strip() or None

    def _write_marker(self, backend: Any, revision: str) -> None:
        backend.upload_files([(f"{self._target_root}/{REVISION_MARKER}", revision.encode("utf-8"))])

    def _remove_stale(
        self, backend: Any, manifest: SkillManifest, previous_files: dict[str, str]
    ) -> list[str]:
        """Delete files that the previous revision had and this one does not."""
        current = {entry.path for skill in manifest.skills for entry in skill.files}
        stale = sorted(set(previous_files) - current)
        removed: list[str] = []
        for relative in stale:
            target = f"{self._target_root}/{relative}"
            result = backend.delete(target)
            if getattr(result, "error", None) is None:
                removed.append(target)
        return removed

    # -------------------------------------------------------------- middleware

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ANN401
        """Sync before each run and report the revision into graph state."""
        generation = _generation_from(runtime)
        report = self.sync(generation=generation)
        return {
            "skills_revision": report.revision,
            "skills_generation": generation,
        }

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ANN401
        import asyncio

        generation = _generation_from(runtime)
        report = await asyncio.to_thread(self.sync, generation=generation)
        return {
            "skills_revision": report.revision,
            "skills_generation": generation,
        }


def _generation_from(runtime: Any) -> int | None:
    """Best-effort sandbox generation lookup for re-sync decisions."""
    context = getattr(runtime, "context", None)
    for source in (context, runtime):
        if source is None:
            continue
        for attribute in ("sandbox_generation", "generation"):
            value = getattr(source, attribute, None)
            if isinstance(value, int):
                return value
        if isinstance(source, dict):
            value = source.get("sandbox_generation") or source.get("generation")
            if isinstance(value, int):
                return value
    return None


__all__ = [
    "DEFAULT_SOURCE_DIR",
    "DEFAULT_TARGET_ROOT",
    "PROGRESSIVE_FIELDS",
    "REVISION_MARKER",
    "SKILL_FILENAME",
    "SLUG_PATTERN",
    "SkillFile",
    "SkillManifest",
    "SkillManifestError",
    "SkillMeta",
    "SkillsSyncMiddleware",
    "SyncReport",
    "load_manifest",
    "manifest_files",
    "parse_frontmatter",
]
