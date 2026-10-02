"""Turning a source into an assigned skill, with every step able to refuse.

The state machine is ``draft -> validating -> validated -> persisted -> assigned`` and the
order is the safety property. Nothing is written to a version prefix until validation has
passed, nothing is assigned until the version has been written **and read back**, and
nothing reaches ``/skills/users/...`` until it has been assigned.

Two boundaries are deliberate and easy to get wrong:

**Staging is not the executable directory.** A skill under construction is unpacked into
``/skills/.staging/...``, never into ``/skills/users/{scope}/{slug}``. A half-finished or
hostile skill must not be one directory listing away from being loaded by an agent, and the
agent's filesystem would happily serve it if it were.

**A smoke test is a real execution.** The claim "this skill works" is only worth something if
a process actually ran the declared entry point with the shipped example and with a broken
input, and both outcomes were recorded. Reading the script and deciding it looks fine is not
a test.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import posixpath
import re
import shlex
import stat
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from agent.middlewares.user_skills_restore import KNOWN_SCOPES
from agent.skills.store import SkillStore, SkillVersion, content_digest

LOGGER = logging.getLogger("rush_harness.skills.pipeline")

#: Limits from contracts/skills-memory.md item 2. Enforced before anything is expanded.
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_EXPANDED_BYTES = 10 * 1024 * 1024
MAX_FILES = 100

#: Same slug rule the preset manifest uses, so a published skill and a shipped one are
#: validated identically.
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SLUG_MAX_LENGTH = 64

SKILL_DOCUMENT = "SKILL.md"
EXAMPLE_INPUT = "examples/input.json"

#: Where a skill under construction is unpacked. Inside ``/skills`` so the sandbox can run it,
#: but under a dot-directory that is not a scope, so it can never be mistaken for an
#: assigned skill.
DEFAULT_STAGING_ROOT = "/skills/.staging"

#: The CLI convention every skill script follows. Stated here because the smoke test has to
#: invoke *something*, and guessing per-skill would make the test unreproducible.
INPUT_FLAG = "--input"
OUTPUT_DIR_FLAG = "--out-dir"


class SkillValidationError(Exception):
    """A source that cannot become a skill, with a code the caller can act on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourceBundle:
    """A source, read out of the sandbox, before validation."""

    source_type: str
    source: str
    files: dict[str, bytes]
    source_sha256: str
    fetched_at: str


@dataclass(frozen=True)
class ValidatedSkill:
    slug: str
    description: str
    files: dict[str, bytes]
    scripts_entry: str | None
    bundle: SourceBundle

    @property
    def content_sha256(self) -> str:
        return content_digest(self.files)


@dataclass
class PreparedSkill:
    """Validated and smoke-tested, ready to persist. Still not visible to any agent."""

    skill: ValidatedSkill
    smoke: list[dict[str, Any]] = field(default_factory=list)
    staging_directory: str = ""
    repairs: int = 0


@dataclass
class PublishResult:
    status: str
    slug: str
    scope: str
    version: str
    content_sha256: str
    reused: bool = False
    smoke: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "slug": self.slug,
            "scope": self.scope,
            "version": self.version,
            "content_sha256": self.content_sha256,
            "reused": self.reused,
            "smoke": self.smoke,
            "reason": self.reason,
        }


class SmokeFailed(Exception):
    """The skill ran and did not behave. Carries every attempt so the model can be told."""

    def __init__(self, message: str, records: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.records = records


# --------------------------------------------------------------------------- #
# reading the source
# --------------------------------------------------------------------------- #


def _run(backend: Any, command: str, *, timeout: float = 180.0) -> Any:
    return backend.execute(command, timeout=timeout)


def _list_tree(backend: Any, root: str) -> tuple[list[str], list[str]]:
    """Every regular file and every symlink under ``root``, as paths relative to it.

    Symlinks are collected separately rather than filtered out: a link is the thing this
    check exists to find, so it has to be reported, not skipped.
    """
    quoted = shlex.quote(root)
    listing = _run(backend, f"find {quoted} -type f -print; echo '--links--'; find {quoted} -type l -print")
    if listing.exit_code != 0:
        output = listing.output or ""
        if "No such file" in output or "cannot access" in output:
            raise SkillValidationError("SOURCE_NOT_FOUND", f"生成目录不存在：{root}")
        raise SkillValidationError("SOURCE_UNREADABLE", f"读取 {root} 失败：{output.strip()[:300]}")

    files: list[str] = []
    links: list[str] = []
    target = files
    for line in listing.output.splitlines():
        line = line.strip()
        if line == "--links--":
            target = links
            continue
        if not line:
            continue
        target.append(line[len(root) :].lstrip("/") if line.startswith(root) else line)
    return files, links


def collect_generated(backend: Any, directory: str) -> SourceBundle:
    """Read a model-generated directory out of the sandbox."""
    if not directory.startswith("/"):
        raise SkillValidationError("SOURCE_INVALID", f"生成目录必须是沙箱内绝对路径：{directory!r}")

    names, links = _list_tree(backend, directory)
    if links:
        raise SkillValidationError(
            "SYMLINK_REFUSED", f"技能目录不得包含符号链接：{sorted(links)[:5]}"
        )
    if not names:
        raise SkillValidationError("SOURCE_EMPTY", f"{directory} 里没有任何文件")

    paths = [f"{directory.rstrip('/')}/{name}" for name in sorted(names)]
    responses = backend.download_files(paths)
    files: dict[str, bytes] = {}
    for name, response in zip(sorted(names), responses, strict=True):
        error = getattr(response, "error", None)
        content = getattr(response, "content", None)
        if error or content is None:
            raise SkillValidationError(
                "SOURCE_UNREADABLE", f"读取 {name} 失败（{error or 'no content'}）"
            )
        files[name] = content

    return SourceBundle(
        source_type="generated",
        source=directory,
        files=files,
        source_sha256=content_digest(files),
        fetched_at=datetime.now(UTC).isoformat(),
    )


def collect_package(
    backend: Any, url: str, *, allowed_hosts: Sequence[str], staging_root: str = DEFAULT_STAGING_ROOT
) -> SourceBundle:
    """Download an approved archive inside the sandbox and read it out.

    ``allowed_hosts`` is an allow-list rather than a block-list because the contract says
    "已批准的 URL": there is no list of bad hosts to enumerate, only a list of places a
    package is permitted to come from. An empty list refuses every URL — the fail-closed
    reading, so a misconfiguration cannot become an open fetch.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise SkillValidationError("SOURCE_INVALID", f"只接受 http(s) 的资源地址：{url!r}")
    if parsed.hostname not in set(allowed_hosts):
        raise SkillValidationError(
            "SOURCE_NOT_ALLOWED",
            f"{parsed.hostname!r} 不在已批准的资源站列表里：{sorted(allowed_hosts)}",
        )

    archive = f"{staging_root.rstrip('/')}/downloads/{uuid4().hex}/package.zip"
    quoted_archive = shlex.quote(archive)
    fetched = _run(
        backend,
        "set -e\n"
        f"mkdir -p {shlex.quote(posixpath.dirname(archive))}\n"
        f"curl -sS -L -o {quoted_archive} -w '%{{http_code}}' {shlex.quote(url)}\n",
    )
    if fetched.exit_code != 0:
        raise SkillValidationError(
            "SOURCE_UNREACHABLE", f"下载 {url} 失败：{fetched.output.strip()[:300]}"
        )
    code = fetched.output.strip().splitlines()[-1] if fetched.output.strip() else ""
    if code and code != "200":
        raise SkillValidationError("SOURCE_UNREACHABLE", f"下载 {url} 返回 HTTP {code}")

    response = backend.download_files([archive])[0]
    if response.error or response.content is None:
        raise SkillValidationError("SOURCE_UNREADABLE", f"读取下载的压缩包失败：{response.error}")
    payload = response.content

    files = _expand_archive(payload)
    return SourceBundle(
        source_type="package",
        source=url,
        files=files,
        source_sha256=hashlib.sha256(payload).hexdigest(),
        fetched_at=datetime.now(UTC).isoformat(),
    )


def _expand_archive(payload: bytes) -> dict[str, bytes]:
    """Unpack an archive, refusing everything the contract refuses.

    A path is judged after normalisation and never by ``startswith``: ``../../x`` and
    ``/etc/passwd`` both look harmless to a prefix test on the raw member name, and a
    symlink member is a way to make a later read escape a directory nobody wrote to.
    """
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise SkillValidationError(
            "ARCHIVE_TOO_LARGE",
            f"压缩包 {len(payload)} 字节，超过 {MAX_ARCHIVE_BYTES} 上限",
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as failure:
        raise SkillValidationError("ARCHIVE_INVALID", f"不是有效的 ZIP：{failure}") from failure

    members = [info for info in archive.infolist() if not info.is_dir()]
    if len(members) > MAX_FILES:
        raise SkillValidationError("TOO_MANY_FILES", f"{len(members)} 个文件，超过 {MAX_FILES} 上限")

    expanded = 0
    files: dict[str, bytes] = {}
    for info in members:
        name = info.filename
        normalised = posixpath.normpath(name)
        if name.startswith("/") or normalised.startswith("../") or normalised == "..":
            raise SkillValidationError("PATH_TRAVERSAL", f"压缩包成员路径越界：{name!r}")

        mode = info.external_attr >> 16
        if mode and stat.S_ISLNK(mode):
            raise SkillValidationError("SYMLINK_REFUSED", f"压缩包包含符号链接：{name!r}")

        expanded += info.file_size
        if expanded > MAX_EXPANDED_BYTES:
            raise SkillValidationError(
                "ARCHIVE_TOO_LARGE",
                f"展开后超过 {MAX_EXPANDED_BYTES} 上限（在 {name!r} 处）",
            )
        files[normalised] = archive.read(info)

    if not files:
        raise SkillValidationError("SOURCE_EMPTY", "压缩包里没有文件")
    return files


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


def parse_frontmatter(text: str, *, source: str) -> dict[str, str]:
    """The tiny YAML subset the skills actually use: ``key: value`` between ``---`` lines."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillValidationError("FRONTMATTER_MISSING", f"{source}: 缺少 frontmatter")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        fields[key.strip()] = value.strip()
    raise SkillValidationError("FRONTMATTER_MISSING", f"{source}: frontmatter 没有结束标记")


def validate_bundle(bundle: SourceBundle, *, slug: str) -> ValidatedSkill:
    """Everything a skill must be before it is allowed to run.

    The slug in the request has to match the ``name`` in the frontmatter. Without that check
    a publish could file one skill's content under another skill's name, and the assignment
    the user approved would point at something they never saw.
    """
    if not SLUG_PATTERN.match(slug) or len(slug) > SLUG_MAX_LENGTH:
        raise SkillValidationError(
            "SLUG_INVALID", f"slug {slug!r} 不符合 {SLUG_PATTERN.pattern} 或超过 {SLUG_MAX_LENGTH} 字符"
        )

    if len(bundle.files) > MAX_FILES:
        raise SkillValidationError("TOO_MANY_FILES", f"{len(bundle.files)} 个文件，超过上限")
    expanded = sum(len(payload) for payload in bundle.files.values())
    if expanded > MAX_EXPANDED_BYTES:
        raise SkillValidationError("ARCHIVE_TOO_LARGE", f"文件合计 {expanded} 字节，超过上限")

    for name in bundle.files:
        normalised = posixpath.normpath(name)
        if name.startswith("/") or normalised.startswith("../") or normalised == "..":
            raise SkillValidationError("PATH_TRAVERSAL", f"文件路径越界：{name!r}")

    document = bundle.files.get(SKILL_DOCUMENT)
    if document is None:
        raise SkillValidationError("SKILL_MISSING", f"必须包含 {SKILL_DOCUMENT}")
    try:
        text = document.decode("utf-8")
    except UnicodeDecodeError as failure:
        raise SkillValidationError("SKILL_INVALID", f"{SKILL_DOCUMENT} 不是 UTF-8：{failure}") from failure

    frontmatter = parse_frontmatter(text, source=SKILL_DOCUMENT)
    name = (frontmatter.get("name") or "").strip()
    description = (frontmatter.get("description") or "").strip()
    if not name:
        raise SkillValidationError("FRONTMATTER_MISSING", "frontmatter 缺少 name")
    if not description:
        raise SkillValidationError("FRONTMATTER_MISSING", "frontmatter 缺少 description")
    if name != slug:
        raise SkillValidationError(
            "SLUG_MISMATCH",
            f"frontmatter 的 name {name!r} 与请求的 slug {slug!r} 不一致；"
            "不允许把一份技能内容发布到另一个名字下",
        )

    scripts_entry = _declared_entry(text)
    if scripts_entry and scripts_entry not in bundle.files:
        raise SkillValidationError(
            "ENTRY_MISSING", f"声明了脚本入口 {scripts_entry!r}，但文件不存在"
        )
    if scripts_entry:
        example = bundle.files.get(EXAMPLE_INPUT)
        if example is None:
            raise SkillValidationError("EXAMPLE_MISSING", f"脚本技能必须提供 {EXAMPLE_INPUT}")
        try:
            json.loads(example.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as failure:
            raise SkillValidationError("EXAMPLE_INVALID", "示例必须是合法 UTF-8 JSON") from failure

    return ValidatedSkill(
        slug=slug,
        description=description,
        files=dict(bundle.files),
        scripts_entry=scripts_entry,
        bundle=bundle,
    )


#: ``scripts_entry: scripts/x.py`` in the frontmatter, or a ``scripts/...`` reference in the
#: body. Both are accepted because the shipped skills use the second form.
_ENTRY_PATTERN = re.compile(r"scripts/([A-Za-z0-9_.\-/]+\.py)")


def _declared_entry(text: str) -> str | None:
    frontmatter = parse_frontmatter(text, source=SKILL_DOCUMENT)
    declared = (frontmatter.get("scripts_entry") or "").strip()
    if declared:
        return declared
    match = _ENTRY_PATTERN.search(text)
    return f"scripts/{match.group(1)}" if match else None


# --------------------------------------------------------------------------- #
# smoke
# --------------------------------------------------------------------------- #


def write_staging(backend: Any, directory: str, files: Mapping[str, bytes]) -> None:
    """Put a validated skill into staging so the sandbox can run it.

    Staging only. The executable directory is written by the assign step, after the version
    has been persisted and read back.
    """
    _run(backend, f"rm -rf {shlex.quote(directory)}; mkdir -p {shlex.quote(directory)}")
    uploads = [(f"{directory.rstrip('/')}/{name}", payload) for name, payload in files.items()]
    responses = backend.upload_files(uploads)
    failures = [getattr(item, "error", None) for item in responses if getattr(item, "error", None)]
    if failures:
        raise SkillValidationError("STAGING_FAILED", f"写入 staging 失败：{failures[:3]}")


def run_smoke(backend: Any, *, staging: str, entry: str, slug: str) -> dict[str, Any]:
    """Run the declared entry point twice: once as intended, once with a broken input.

    Both directions matter. A script that fails on the example is broken; a script that
    *succeeds* on a malformed input is worse, because it will happily produce a report from
    nonsense and nothing downstream can tell.
    """
    output_root = f"{staging}/.smoke-output"
    bad_input = f"{staging}/.smoke-bad-input.json"
    example = f"{staging}/{EXAMPLE_INPUT}"

    _run(
        backend,
        "set -e\n"
        f"test -f {shlex.quote(example)}\n"
        f"rm -rf {shlex.quote(output_root)}; mkdir -p {shlex.quote(output_root)}\n"
        "printf '%s' '{\"lines\": [{\"part_id\": ' > "
        f"{shlex.quote(bad_input)}\n",
    )

    record: dict[str, Any] = {"slug": slug, "entry": entry, "attempts": []}
    for label, input_file in (("example", example), ("broken", bad_input)):
        output_dir = f"{output_root}/{label}"
        _run(backend, f"mkdir -p {shlex.quote(output_dir)}")
        command = (
            f"cd {shlex.quote(staging)} && python3 {shlex.quote(entry)} "
            f"{INPUT_FLAG} {shlex.quote(input_file)} {OUTPUT_DIR_FLAG} {shlex.quote(output_dir)}"
        )
        result = _run(backend, command)
        record["attempts"].append(
            {
                "input": label,
                "input_path": input_file,
                "output_dir": output_dir,
                "exit_code": result.exit_code,
                "stdout": result.output[-2000:],
                "expected": "0" if label == "example" else "non-zero",
            }
        )
        record["attempts"][-1]["outputs"] = _collect_outputs(backend, output_dir)
        if label == "example":
            # Capture before the malformed-input execution can modify any file.
            record["outputs"] = record["attempts"][-1]["outputs"]
            record["observed_total"] = _observed_total(backend, output_dir)

    example_attempt, broken_attempt = record["attempts"]
    problems = []
    if example_attempt["exit_code"] != 0:
        problems.append("EXAMPLE_FAILED")
    if broken_attempt["exit_code"] in (None, 0):
        problems.append("BROKEN_INPUT_ACCEPTED")
    if not any(item["size"] > 0 for item in record["outputs"].values()):
        problems.append("EXAMPLE_OUTPUT_MISSING")
    record["validation_level"] = "installation"
    if slug == "reorder-cost-summary":
        from agent.skills.procurement_validation import validate_procurement

        business = validate_procurement(backend, staging=staging, entry=entry)
        record["business_validation"] = business
        record["validation_level"] = "procurement-contract"
        if not business["passed"]:
            problems.append("PROCUREMENT_CONTRACT_FAILED")
    record["problems"] = problems
    record["passed"] = not problems
    return record


def _collect_outputs(backend: Any, output_dir: str) -> dict[str, dict[str, Any]]:
    return {name: {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
            for name, content in _read_output_files(backend, output_dir).items()}


def _read_output_files(backend: Any, output_dir: str) -> dict[str, bytes]:
    listing = _run(backend, f"find {shlex.quote(output_dir)} -type f -print")
    if listing.exit_code != 0:
        raise SkillValidationError("OUTPUT_READ_FAILED", "无法列出校验产物")
    names = [line.strip() for line in listing.output.splitlines() if line.strip()]
    if not names:
        return {}
    responses = backend.download_files(names)
    outputs: dict[str, bytes] = {}
    for path, response in zip(names, responses, strict=True):
        content = getattr(response, "content", None)
        if content is None or getattr(response, "error", None):
            raise SkillValidationError("OUTPUT_READ_FAILED", f"无法读取产物 {path}")
        relative = posixpath.relpath(path, output_dir)
        if relative.startswith("../"):
            raise SkillValidationError("OUTPUT_READ_FAILED", "产物不在输出目录中")
        outputs[relative] = content
    return outputs


def _observed_total(backend: Any, output_dir: str) -> str | None:
    """The ``total_amount`` from any produced JSON, so the caller can check the number.

    Read generically rather than by fetching ``summary.json`` by name: the point is to record
    what the skill actually computed, and a skill that names its output differently is still
    reporting a total.
    """
    listing = _run(backend, f"find {shlex.quote(output_dir)} -maxdepth 1 -name '*.json' -print || true")
    paths = [line.strip() for line in listing.output.splitlines() if line.strip()]
    for path in paths:
        response = backend.download_files([path])[0]
        content = getattr(response, "content", None)
        if content is None:
            continue
        try:
            document = json.loads(content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(document, dict) and document.get("total_amount") is not None:
            return str(document["total_amount"])
    return None


# --------------------------------------------------------------------------- #
# publishing
# --------------------------------------------------------------------------- #


class SkillPublisher:
    """Validation, persistence and assignment for one process."""

    def __init__(
        self,
        *,
        backend_provider: Any,
        store: SkillStore,
        scopes: Sequence[str] = KNOWN_SCOPES,
        allowed_source_hosts: Sequence[str] = (),
        staging_root: str = DEFAULT_STAGING_ROOT,
    ) -> None:
        self._backend_provider = backend_provider
        self._store = store
        self._scopes = tuple(scopes)
        self._allowed_source_hosts = tuple(allowed_source_hosts)
        self._staging_root = staging_root

    @property
    def store(self) -> SkillStore:
        return self._store

    @property
    def scopes(self) -> tuple[str, ...]:
        return self._scopes

    def check_scope(self, scope: str | None) -> str:
        """Resolve a requested scope, or refuse.

        A missing scope is refused rather than widened. "Where should this be available" is a
        question only the user can answer, and an empty value silently meaning *every agent*
        would publish to more places than were asked for.
        """
        # Whitespace is not a scope either. Stripping first means " " takes the same path as
        # "" instead of falling through to the unknown-scope branch and reporting the wrong
        # problem — the caller would be told the value is unrecognised when it was never
        # really supplied.
        scope = (scope or "").strip()
        if not scope:
            raise SkillValidationError(
                "SCOPE_REQUIRED",
                "没有指定 target_scope。请询问用户这个技能应该给哪个 Agent 用，"
                f"可选：{'、'.join(self._scopes)}；不要替用户决定，也不要默认对所有 Agent 发布。",
            )
        if scope not in self._scopes:
            raise SkillValidationError(
                "SCOPE_UNKNOWN",
                f"未知的 target_scope {scope!r}；可选：{'、'.join(self._scopes)}",
            )
        return scope

    def collect(self, source_type: str, source: str) -> SourceBundle:
        if source_type == "generated":
            return collect_generated(self._backend_provider(), source)
        if source_type == "package":
            return collect_package(
                self._backend_provider(),
                source,
                allowed_hosts=self._allowed_source_hosts,
                staging_root=self._staging_root,
            )
        raise SkillValidationError(
            "SOURCE_TYPE_UNKNOWN", f"source_type 只能是 generated 或 package，收到 {source_type!r}"
        )

    def prepare(self, *, source_type: str, source: str, slug: str) -> PreparedSkill:
        """Collect, validate and smoke-test. Raises on any failure; writes no version."""
        bundle = self.collect(source_type, source)
        skill = validate_bundle(bundle, slug=slug)

        staging = f"{self._staging_root.rstrip('/')}/{slug}/{uuid4().hex}"
        write_staging(self._backend_provider(), staging, skill.files)

        entry = skill.scripts_entry
        if entry is None:
            # No script means nothing to execute; the smoke step is vacuous rather than
            # failed, and the record says so instead of implying a test ran.
            return PreparedSkill(
                skill=skill,
                smoke=[{"slug": slug, "entry": None, "validation_level": "structural",
                        "behavior_verified": False, "note": "指导性技能未执行行为评测"}],
                staging_directory=staging,
            )

        record = run_smoke(self._backend_provider(), staging=staging, entry=entry, slug=slug)
        if not record["passed"]:
            raise SmokeFailed(_smoke_message(record), [record])
        return PreparedSkill(skill=skill, smoke=[record], staging_directory=staging)

    def complete(self, request: PreparedSkill, *, owner_user_id: str, scope: str) -> PublishResult:
        """Persist, verify by reading back, then move the pointer."""
        skill = request.skill
        digest = skill.content_sha256
        current = self._store.current_assignment(owner_user_id, scope, skill.slug)
        expected_revision = current.revision if current else None

        existing = self._store.find_by_content(owner_user_id, scope, skill.slug, digest)
        if existing is not None:
            # A concurrent loser may have persisted identical content in another version.
            # Replays keep the current version when its content is already the requested one.
            current_version = (self._store.get_version(owner_user_id, scope, skill.slug, current.version)
                               if current else None)
            if current_version is not None and current_version.content_sha256 == digest:
                existing = current_version
            if current is not None and current.version == existing.version:
                # Same owner, same scope, same content, already current: return the original.
                return PublishResult(
                    status="assigned",
                    slug=skill.slug,
                    scope=scope,
                    version=existing.version,
                    content_sha256=digest,
                    reused=True,
                    smoke=request.smoke,
                    reason="内容与当前版本一致，返回原版本",
                )
            version = existing
        else:
            version = self._persist(skill, owner_user_id=owner_user_id, scope=scope)

        pointer = self._assign(
            owner_user_id=owner_user_id, scope=scope, slug=skill.slug, version=version.version,
            expected_revision=expected_revision,
        )
        if pointer is None:
            raise SkillValidationError(
                "ASSIGNMENT_CONFLICT",
                f"{skill.slug} 的当前指针在发布过程中被别人改动，已放弃这次分配；"
                "版本已写入但未分配，可以重试。",
            )

        self._store.store_pointer_mirror(
            owner_user_id, scope, skill.slug, pointer.version, pointer.revision
        )
        self._install_execution_copy(owner_user_id, scope, skill, version.version, pointer.revision)

        return PublishResult(
            status="assigned",
            slug=skill.slug,
            scope=scope,
            version=version.version,
            content_sha256=digest,
            smoke=request.smoke,
        )

    # --------------------------------------------------------------- internals

    def _persist(self, skill: ValidatedSkill, *, owner_user_id: str, scope: str) -> SkillVersion:
        """Write the version, then read it back and re-hash every file.

        The read-back is the whole point of the step: ``put`` returning without raising only
        says the write was accepted. Until the bytes come back with the digests the manifest
        claims, the version is a half-write, and a half-write must never become assignable.
        """
        version_number, reservation_id = self._store.reserve_version(owner_user_id, scope, skill.slug)

        manifest = build_manifest(skill, version=version_number, scope=scope, owner_user_id=owner_user_id)

        from agent.skills.store import serialise_manifest

        version = SkillVersion(
            owner_user_id=owner_user_id,
            scope=scope,
            slug=skill.slug,
            version=version_number,
            content_sha256=skill.content_sha256,
            # Hashed from the bytes that are actually written, via the same serialiser.
            manifest_sha256=hashlib.sha256(serialise_manifest(manifest)).hexdigest(),
            source_type=skill.bundle.source_type,
            source=skill.bundle.source,
            source_sha256=skill.bundle.source_sha256,
            file_count=len(skill.files),
            total_size=sum(len(payload) for payload in skill.files.values()),
            scripts_entry=skill.scripts_entry,
            created_at=datetime.now(UTC).isoformat(),
        )

        try:
            self._store.write_version_files(version, skill.files, manifest)
            read_back = self._store.read_version_files(version)
            problems = _read_back_problems(skill.files, manifest, read_back)
            if problems:
                raise SkillValidationError(
                    "PERSISTENCE_INCOMPLETE",
                    "写入后读回校验失败，该版本不会被分配：" + "；".join(problems[:5]),
                )
            self._store.record_version(version, reservation_id=reservation_id)
        except Exception as failure:
            self._store.fail_reservation(owner_user_id, scope, skill.slug, version_number,
                                         reservation_id, type(failure).__name__)
            raise
        return version

    def _assign(self, *, owner_user_id: str, scope: str, slug: str, version: str,
                expected_revision: int | None) -> Any:
        return self._store.assign(owner_user_id, scope, slug, version,
                                  expected_revision=expected_revision)

    def _install_execution_copy(
        self,
        owner_user_id: str,
        scope: str,
        skill: ValidatedSkill,
        version: str,
        revision: int,
    ) -> None:
        """Copy the assigned version where agents read it, and mark the skills revision.

        The marker is what makes the change visible without a full rescan: the restore
        middleware compares it against the revision it last applied, so an assignment is
        picked up on the next call rather than at the next restart.
        """
        from agent.middlewares.user_skills_restore import (
            USER_SKILLS_REVISION_MARKER,
            USER_SKILLS_ROOT,
            SkillAssignment,
            atomic_replace,
        )

        assignment = SkillAssignment(
            owner_user_id=owner_user_id,
            scope=scope,
            slug=skill.slug,
            version=version,
        )
        backend = self._backend_provider()
        atomic_replace(backend, assignment.sandbox_directory, skill.files, assignment)
        backend.upload_files(
            [
                (
                    USER_SKILLS_REVISION_MARKER,
                    f"{revision}\n".encode(),
                )
            ]
        )
        LOGGER.info(
            "installed %s v%s for owner=%s scope=%s at %s",
            skill.slug,
            version,
            owner_user_id,
            scope,
            f"{USER_SKILLS_ROOT}/{scope}/{skill.slug}",
        )


def build_manifest(
    skill: ValidatedSkill, *, version: str, scope: str, owner_user_id: str
) -> dict[str, Any]:
    """The version manifest, in the shape ``verify_manifest`` reads.

    ``files`` is what the restore path checks; the rest is provenance, recorded so "where did
    this come from" is answerable later without re-downloading anything.
    """
    return {
        "schema_version": 1,
        "slug": skill.slug,
        "version": version,
        "scope": scope,
        "created_by": owner_user_id,
        "created_at": datetime.now(UTC).isoformat(),
        "source": skill.bundle.source_type,
        "source_uri": skill.bundle.source,
        "source_sha256": skill.bundle.source_sha256,
        "scripts_entry": skill.scripts_entry,
        "files": [
            {
                "path": path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
            for path, payload in sorted(skill.files.items())
        ],
        "total_size": sum(len(payload) for payload in skill.files.values()),
    }


def _read_back_problems(
    expected: Mapping[str, bytes], manifest: Mapping[str, Any], read_back: Mapping[str, bytes]
) -> list[str]:
    problems: list[str] = []
    for path, payload in sorted(expected.items()):
        stored = read_back.get(path)
        if stored is None:
            problems.append(f"{path} 读回缺失")
            continue
        if hashlib.sha256(stored).hexdigest() != hashlib.sha256(payload).hexdigest():
            problems.append(f"{path} 读回内容与写入不一致")
    if "manifest.json" not in read_back:
        problems.append("manifest.json 读回缺失")
    else:
        from agent.skills.store import serialise_manifest

        if read_back["manifest.json"] != serialise_manifest(manifest):
            problems.append("manifest.json 读回内容与写入不一致")
    declared = {entry["path"] for entry in manifest.get("files", [])}
    extra = sorted(set(read_back) - declared - {"manifest.json"})
    if extra:
        problems.append(f"多出未声明的文件：{extra[:3]}")
    return problems


def _smoke_message(record: Mapping[str, Any]) -> str:
    parts = []
    for attempt in record.get("attempts", []):
        parts.append(
            f"{attempt['input']} 输入退出码 {attempt['exit_code']}（期望 {attempt['expected']}）："
            f"{(attempt.get('stdout') or '').strip()[-300:]}"
        )
    parts.extend(str(problem) for problem in record.get("problems", []))
    for case in (record.get("business_validation") or {}).get("cases", []):
        if not case.get("passed"):
            parts.append(f"业务案例 {case['id']}：{', '.join(case.get('problems', []))}")
    return "冒烟测试未通过。" + "；".join(parts)


__all__ = [
    "DEFAULT_STAGING_ROOT",
    "EXAMPLE_INPUT",
    "INPUT_FLAG",
    "MAX_ARCHIVE_BYTES",
    "MAX_EXPANDED_BYTES",
    "MAX_FILES",
    "OUTPUT_DIR_FLAG",
    "SKILL_DOCUMENT",
    "SLUG_PATTERN",
    "PreparedSkill",
    "PublishResult",
    "SkillPublisher",
    "SkillValidationError",
    "SmokeFailed",
    "SourceBundle",
    "ValidatedSkill",
    "build_manifest",
    "collect_generated",
    "collect_package",
    "parse_frontmatter",
    "run_smoke",
    "validate_bundle",
    "write_staging",
]
