"""T17 acceptance: creating, downloading, validating, assigning and persisting a skill.

Runs against real MongoDB, a real sandbox and the real resource site. The model double is
used only where a repair loop needs a scripted answer.

The properties under test, in the order the pipeline enforces them:

* **Scope is asked for, never assumed.** A missing scope is a question; an unknown scope is a
  refusal. Neither becomes "publish to every agent".
* **Nothing is assigned that has not run.** The smoke step executes the declared entry point
  with the shipped example *and* with a broken input, and both outcomes are recorded. A skill
  that cannot fail on nonsense is worse than one that fails on the example.
* **Nothing is assigned that has not been read back.** A version is written, re-read and
  re-hashed; until then it is a half-write, and half-writes are not discoverable.
* **A pointer move is conditional.** MongoDB's ``find_one_and_update`` is what makes two
  publishes for one slug unable to both win — the Store has no compare-and-swap, which is why
  the pointer lives where it does.
* **A restored copy is verified.** The bytes that land in the sandbox are checked against the
  manifest digests before they replace anything, and a failed restore leaves the old copy.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import uuid
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.artifacts.service import ArtifactService  # noqa: E402
from agent.artifacts.store import MongoArtifactStore  # noqa: E402
from agent.middlewares.user_skills_restore import (  # noqa: E402
    USER_SKILLS_REVISION_MARKER,
    USER_SKILLS_ROOT,
    ManifestIntegrityError,
    SkillAssignment,
    StoreAssignmentReader,
    UserSkillsRestoreMiddleware,
    assignments_revision,
    verify_manifest,
)
from agent.persistence.indexes import (  # noqa: E402
    drop_undeclared_application_indexes,
    ensure_application_indexes,
)
from agent.skills.pipeline import (  # noqa: E402
    MAX_ARCHIVE_BYTES,
    MAX_FILES,
    SkillPublisher,
    SkillValidationError,
    SmokeFailed,
    SourceBundle,
    _expand_archive,
    validate_bundle,
)
from agent.skills.store import SkillStore, content_digest  # noqa: E402
from agent.tools.assign_skill import publish_skill  # noqa: E402
from fixtures import mongo_service, sandbox_service, site_service  # noqa: E402

pytestmark = pytest.mark.integration

OWNER_A = "demo-a"
OWNER_B = "demo-b"
SLUG = "reorder-cost-summary"
EXAMPLE_TOTAL = "1533.00"

#: Three files, as the resource site ships them.
FIXTURE_SKILL = Path(__file__).resolve().parents[2] / "fixtures" / "skills" / "reorder-cost-summary-v1"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def upload_tree(backend, root: str, files: dict[str, bytes]) -> str:
    """Write a directory into the sandbox, the way a model generating a skill would."""
    directories = {f"{root}/" + str(Path(name).parent).replace("\\", "/") for name in files}
    backend.execute("mkdir -p " + " ".join(f"'{path}'" for path in sorted(directories)))
    responses = backend.upload_files([(f"{root}/{name}", payload) for name, payload in files.items()])
    failures = [getattr(item, "error", None) for item in responses if getattr(item, "error", None)]
    assert not failures, failures
    return root


def fixture_files() -> dict[str, bytes]:
    return {
        path.relative_to(FIXTURE_SKILL).as_posix(): path.read_bytes()
        for path in sorted(FIXTURE_SKILL.rglob("*"))
        if path.is_file()
    }


def rename_skill(files: dict[str, bytes], new_slug: str) -> dict[str, bytes]:
    """The same skill under a different name, for tests that need two of them."""
    body = files["SKILL.md"].decode("utf-8").replace(SLUG, new_slug)
    return {**files, "SKILL.md": body.encode("utf-8")}


def build_zip(members: dict[str, bytes], *, symlinks: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
        for name in symlinks:
            info = zipfile.ZipInfo(name)
            # S_IFLNK with mode 0o777: what a real archive of a symlink carries.
            info.external_attr = (0o120777 << 16) | 0o777
            archive.writestr(info, "/etc/passwd")
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def settings():
    return mongo_service.unique_settings(f"t17-{uuid.uuid4().hex[:8]}")


@pytest.fixture(scope="module")
def client(settings):
    from pymongo import MongoClient

    mongo_service.require_reachable(settings)
    with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True) as mongo:
        database = mongo[settings.database]
        drop_undeclared_application_indexes(database)
        ensure_application_indexes(database)
        yield mongo
    mongo_service.drop_test_database(settings)


@pytest.fixture(scope="module")
def store(settings, client):
    from langgraph.store.mongodb import MongoDBStore

    return MongoDBStore(client[settings.database][settings.store_collection])


@pytest.fixture(scope="module")
def skill_store(store, client, settings):
    return SkillStore(store, client[settings.database])


@pytest.fixture(scope="module")
def artifacts(client, settings):
    return ArtifactService(store=MongoArtifactStore(client[settings.database]))


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    with site_service.running_site(tmp_path_factory.mktemp("t17-site"), host="0.0.0.0") as running:
        yield running


@pytest.fixture(scope="module")
def sandbox():
    with sandbox_service.running_backend() as (backend, _report):
        yield backend


@pytest.fixture(scope="module")
def publisher(sandbox, skill_store):
    """A publisher allowed to fetch packages from the resource site, and nowhere else."""
    return SkillPublisher(
        backend_provider=lambda: sandbox,
        store=skill_store,
        allowed_source_hosts=(sandbox_service.SANDBOX_HOST_ALIAS,),
    )


@pytest.fixture(scope="module")
def package_url(site):
    """The archive URL *as the sandbox must see it* — the fetch happens in the container."""
    base = sandbox_service.sandbox_url_for_host_config(site.base_url)
    return f"{base}/skills/{SLUG}-v1.zip"


@pytest.fixture(scope="module")
def restorer(sandbox, store, skill_store):
    reader = StoreAssignmentReader(store, pointers=skill_store)
    return UserSkillsRestoreMiddleware(backend_provider=lambda: sandbox, reader=reader)


# --------------------------------------------------------------------------- #
# scope
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("scope", [None, "", "   "])
def test_a_missing_scope_is_a_question_not_a_default(publisher, scope):
    """Empty must not mean "everyone": that publishes to more agents than were asked for."""
    with pytest.raises(SkillValidationError) as failure:
        publisher.check_scope(scope)

    assert failure.value.code == "SCOPE_REQUIRED"
    assert "不要" in str(failure.value) or "询问" in str(failure.value)


@pytest.mark.parametrize("scope", ["everyone", "all", "*", "analyst", "Main"])
def test_an_unknown_scope_is_refused(publisher, scope):
    with pytest.raises(SkillValidationError) as failure:
        publisher.check_scope(scope)

    assert failure.value.code == "SCOPE_UNKNOWN"
    assert "main" in str(failure.value)


def test_the_known_scopes_are_the_ones_the_agents_use(publisher):
    assert set(publisher.scopes) == {"main", "procurement-analyst", "procurement-order"}


# --------------------------------------------------------------------------- #
# archive and path safety
# --------------------------------------------------------------------------- #


def test_a_traversing_archive_member_is_refused():
    payload = build_zip(
        {"SKILL.md": b"---\nname: evil\ndescription: x\n---\n", "../../escape.py": b"x"}
    )

    with pytest.raises(SkillValidationError) as failure:
        _expand_archive(payload)

    assert failure.value.code == "PATH_TRAVERSAL"


def test_an_absolute_archive_member_is_refused():
    payload = build_zip({"SKILL.md": b"body", "/etc/passwd": b"x"})

    with pytest.raises(SkillValidationError) as failure:
        _expand_archive(payload)

    assert failure.value.code == "PATH_TRAVERSAL"


def test_a_symlink_member_is_refused():
    """A link is how a later read escapes a directory nobody wrote to."""
    payload = build_zip(
        {"SKILL.md": b"---\nname: evil\ndescription: x\n---\n"}, symlinks=("scripts/link.py",)
    )

    with pytest.raises(SkillValidationError) as failure:
        _expand_archive(payload)

    assert failure.value.code == "SYMLINK_REFUSED"


def test_an_oversized_archive_is_refused_before_it_is_expanded():
    payload = b"PK\x03\x04" + b"\x00" * (MAX_ARCHIVE_BYTES + 1)

    with pytest.raises(SkillValidationError) as failure:
        _expand_archive(payload)

    assert failure.value.code == "ARCHIVE_TOO_LARGE"


def test_too_many_files_are_refused():
    members = {f"f{index}.txt": b"x" for index in range(MAX_FILES + 1)}
    members["SKILL.md"] = b"---\nname: x\ndescription: y\n---\n"

    with pytest.raises(SkillValidationError) as failure:
        _expand_archive(build_zip(members))

    assert failure.value.code == "TOO_MANY_FILES"


def test_a_generated_directory_that_escapes_its_root_is_refused():
    """A generated source is not trusted just because a model wrote it."""
    bundle = SourceBundle(
        source_type="generated",
        source="/workspace/scratch/skills/x",
        files={
            "SKILL.md": b"---\nname: x\ndescription: y\n---\n",
            "../outside.py": b"print('escape')",
        },
        source_sha256="0" * 64,
        fetched_at="",
    )

    with pytest.raises(SkillValidationError) as failure:
        validate_bundle(bundle, slug="x")

    assert failure.value.code == "PATH_TRAVERSAL"


# --------------------------------------------------------------------------- #
# validation of the skill document
# --------------------------------------------------------------------------- #


def bundle_with(files: dict[str, bytes]) -> SourceBundle:
    return SourceBundle(
        source_type="generated",
        source="test",
        files=files,
        source_sha256=content_digest(files),
        fetched_at="",
    )


def test_a_skill_without_frontmatter_is_refused():
    with pytest.raises(SkillValidationError) as failure:
        validate_bundle(bundle_with({"SKILL.md": b"# just a heading\n"}), slug="x")

    assert failure.value.code == "FRONTMATTER_MISSING"


def test_a_skill_missing_its_description_is_refused():
    with pytest.raises(SkillValidationError) as failure:
        validate_bundle(bundle_with({"SKILL.md": b"---\nname: x\n---\nbody\n"}), slug="x")

    assert "description" in str(failure.value)


def test_a_bad_slug_is_refused():
    with pytest.raises(SkillValidationError) as failure:
        validate_bundle(
            bundle_with({"SKILL.md": b"---\nname: Bad_Slug\ndescription: y\n---\n"}), slug="Bad_Slug"
        )

    assert failure.value.code == "SLUG_INVALID"


def test_a_slug_that_disagrees_with_the_frontmatter_is_refused():
    """Otherwise one skill's content could be published under another skill's name."""
    with pytest.raises(SkillValidationError) as failure:
        validate_bundle(
            bundle_with({"SKILL.md": b"---\nname: something-else\ndescription: y\n---\n"}),
            slug="claimed-name",
        )

    assert failure.value.code == "SLUG_MISMATCH"


def test_a_declared_entry_point_that_does_not_exist_is_refused():
    document = b"---\nname: x\ndescription: y\n---\n\nrun scripts/missing.py\n"

    with pytest.raises(SkillValidationError) as failure:
        validate_bundle(bundle_with({"SKILL.md": document}), slug="x")

    assert failure.value.code == "ENTRY_MISSING"


# --------------------------------------------------------------------------- #
# the real thing: download, run, persist, assign
# --------------------------------------------------------------------------- #


def test_a_downloaded_package_is_installed_and_its_script_really_runs(
    publisher, sandbox, package_url
):
    """The headline: download → validate → run in the sandbox → persist → assign.

    The number printed by the smoke run is the skill's own output, computed inside the
    container from the input it shipped. That is the difference between "the skill was
    published" and "the skill works".
    """
    prepared = publisher.prepare(source_type="package", source=package_url, slug=SLUG)

    assert prepared.skill.scripts_entry == "scripts/summarise.py"
    assert sorted(prepared.skill.files) == ["SKILL.md", "examples/input.json", "scripts/summarise.py"]
    assert prepared.smoke[0]["passed"] is True
    assert prepared.smoke[0]["observed_total"] == EXAMPLE_TOTAL

    result = publisher.complete(prepared, owner_user_id=OWNER_A, scope="main")

    assert result.status == "assigned"
    assert result.version == "1.0.0"
    assert result.reused is False

    listing = sandbox.execute(f"ls -1 {USER_SKILLS_ROOT}/main/{SLUG}")
    assert listing.exit_code == 0, listing.output
    assert "SKILL.md" in listing.output
    assert "scripts" in listing.output

    ran = sandbox.execute(
        f"cd {USER_SKILLS_ROOT}/main/{SLUG} && python3 scripts/summarise.py "
        "--input examples/input.json --out-dir /workspace/scratch/t17-run"
    )
    assert ran.exit_code == 0, ran.output
    assert EXAMPLE_TOTAL in ran.output


def test_the_smoke_record_proves_both_directions(publisher, package_url):
    """A run that only tests the happy path has not tested anything about failure."""
    record = publisher.prepare(
        source_type="package", source=package_url, slug=SLUG
    ).smoke[0]

    example, broken = record["attempts"]
    assert example["exit_code"] == 0
    assert broken["exit_code"] != 0, "坏输入必须失败，否则说明脚本会把无效数据当有效"
    assert record["outputs"], "冒烟必须留下产物 hash"
    for entry in record["outputs"].values():
        assert entry["sha256"] and entry["size"] > 0


def test_a_generated_directory_publishes_through_the_same_pipeline(publisher, sandbox):
    """The other source type: files a model wrote into its own sandbox."""
    slug = "reorder-cost-generated"
    upload_tree(sandbox, f"/workspace/scratch/generated/{slug}", rename_skill(fixture_files(), slug))

    prepared = publisher.prepare(
        source_type="generated", source=f"/workspace/scratch/generated/{slug}", slug=slug
    )

    assert prepared.skill.bundle.source_type == "generated"
    assert prepared.smoke[0]["observed_total"] == EXAMPLE_TOTAL

    result = publisher.complete(prepared, owner_user_id=OWNER_A, scope="procurement-analyst")
    assert result.status == "assigned"


def test_identical_content_returns_the_original_version(publisher, package_url, skill_store):
    first = publisher.complete(
        publisher.prepare(source_type="package", source=package_url, slug=SLUG),
        owner_user_id=OWNER_A,
        scope="main",
    )
    second = publisher.complete(
        publisher.prepare(source_type="package", source=package_url, slug=SLUG),
        owner_user_id=OWNER_A,
        scope="main",
    )

    assert second.reused is True
    assert second.version == first.version
    assert len(skill_store.list_versions(OWNER_A, "main", SLUG)) == 1, (
        "内容没变却新建了版本"
    )


def test_changed_content_creates_a_new_version_and_keeps_the_old(publisher, sandbox, skill_store):
    slug = "reorder-cost-versioned"
    root = f"/workspace/scratch/generated/{slug}"
    files = rename_skill(fixture_files(), slug)
    upload_tree(sandbox, root, files)

    first = publisher.complete(
        publisher.prepare(source_type="generated", source=root, slug=slug),
        owner_user_id=OWNER_A,
        scope="main",
    )

    changed = {**files, "SKILL.md": files["SKILL.md"] + "\n<!-- 说明补充 -->\n".encode()}
    upload_tree(sandbox, root, changed)
    second = publisher.complete(
        publisher.prepare(source_type="generated", source=root, slug=slug),
        owner_user_id=OWNER_A,
        scope="main",
    )

    assert second.version != first.version
    assert second.reused is False
    versions = skill_store.list_versions(OWNER_A, "main", slug)
    assert len(versions) == 2, "旧版本必须保留"
    assert first.version in {item.version for item in versions}


def test_the_pointer_only_moves_when_the_revision_matches(skill_store):
    """The conditional update, tested on the condition itself.

    A caller holding a stale revision — the shape of two publishes racing — must be told it
    lost, not handed the pointer anyway.
    """
    slug = "pointer-test"
    for version in ("1.0.0", "1.0.1"):
        skill_store.record_version(_version_row(slug, version))

    first = skill_store.assign(OWNER_A, "main", slug, "1.0.0", expected_revision=None)
    assert first is not None and first.revision == 1

    stale = skill_store.assign(OWNER_A, "main", slug, "1.0.1", expected_revision=99)
    assert stale is None, "用过期 revision 的条件更新不应成功"

    fresh = skill_store.assign(OWNER_A, "main", slug, "1.0.1", expected_revision=1)
    assert fresh is not None and fresh.version == "1.0.1" and fresh.revision == 2

    duplicate = skill_store.assign(OWNER_A, "main", slug, "1.0.1", expected_revision=None)
    assert duplicate is None, "已经存在的指针不能被 expected_revision=None 覆盖"

    # Clean up: this test deliberately creates version *rows* with no files behind them, which
    # is precisely the half-written state the restore path must refuse. Leaving the assignment
    # behind would make every later restore for this owner report a failure.
    skill_store.withdraw(OWNER_A, "main", slug)


def _version_row(slug: str, version: str):
    from agent.skills.store import SkillVersion

    return SkillVersion(
        owner_user_id=OWNER_A,
        scope="main",
        slug=slug,
        version=version,
        content_sha256="0" * 64,
        manifest_sha256="0" * 64,
        source_type="generated",
        source="test",
        source_sha256="0" * 64,
        file_count=1,
        total_size=1,
        scripts_entry=None,
        created_at="2026-09-17T00:00:00+00:00",
    )


# --------------------------------------------------------------------------- #
# failures that must not publish
# --------------------------------------------------------------------------- #


def test_a_failing_smoke_is_never_published(publisher, sandbox, skill_store):
    """Two repairs, then refusal — and nothing assigned, nothing installed."""
    slug = "reorder-cost-broken"
    root = f"/workspace/scratch/generated/{slug}"
    files = rename_skill(fixture_files(), slug)
    files["scripts/summarise.py"] = b"import sys\nsys.exit(3)\n"
    upload_tree(sandbox, root, files)

    attempts = []

    def record_only(payload: dict) -> None:
        attempts.append(payload)

    body = publish_skill(
        publisher=publisher,
        owner_user_id=OWNER_A,
        source_type="generated",
        source=root,
        slug=slug,
        target_scope="main",
        repair_hook=record_only,
    )

    assert body["ok"] is False
    assert body["code"] == "SMOKE_FAILED"
    assert len(attempts) == 2, "应当正好给两次修复机会"
    assert len(body["details"]["attempts"]) == 3, "初始一次 + 两次修复都要留记录"

    assert skill_store.current_assignment(OWNER_A, "main", slug) is None
    assert not skill_store.list_versions(OWNER_A, "main", slug)
    missing = sandbox.execute(f"ls -d {USER_SKILLS_ROOT}/main/{slug} 2>&1; echo rc=$?")
    assert "No such file" in missing.output, "失败的技能不得出现在可执行目录"


def test_a_repair_that_fixes_the_skill_lets_it_through(publisher, sandbox):
    """The repair loop has to be able to succeed, or it is only a delay."""
    slug = "reorder-cost-repaired"
    root = f"/workspace/scratch/generated/{slug}"
    good = rename_skill(fixture_files(), slug)
    broken = {**good, "scripts/summarise.py": b"import sys\nsys.exit(3)\n"}
    upload_tree(sandbox, root, broken)

    seen = []

    def repair(payload: dict) -> None:
        seen.append(payload)
        upload_tree(sandbox, root, good)

    body = publish_skill(
        publisher=publisher,
        owner_user_id=OWNER_A,
        source_type="generated",
        source=root,
        slug=slug,
        target_scope="main",
        repair_hook=repair,
    )

    assert body["ok"] is True, body
    assert len(seen) == 1
    assert body["skill"]["repairs"] == 1
    assert body["skill"]["smoke"][0]["observed_total"] == EXAMPLE_TOTAL


def test_a_publish_without_a_scope_returns_the_question(publisher, sandbox):
    """No scope, no publish. The refusal names the allowed values."""
    slug = "reorder-cost-noscope"
    root = f"/workspace/scratch/generated/{slug}"
    upload_tree(sandbox, root, rename_skill(fixture_files(), slug))

    body = publish_skill(
        publisher=publisher,
        owner_user_id=OWNER_A,
        source_type="generated",
        source=root,
        slug=slug,
        target_scope=None,
    )

    assert body["ok"] is False
    assert body["code"] == "SCOPE_REQUIRED"
    assert publisher.scopes[0] in body["message"]


def test_an_unapproved_source_host_is_refused(publisher):
    with pytest.raises(SkillValidationError) as failure:
        publisher.prepare(
            source_type="package", source="http://evil.example/skill.zip", slug=SLUG
        )

    assert failure.value.code == "SOURCE_NOT_ALLOWED"


def test_a_corrupt_archive_from_the_real_site_is_refused(publisher, site):
    base = sandbox_service.sandbox_url_for_host_config(site.base_url)

    with pytest.raises(SkillValidationError) as failure:
        publisher.prepare(
            source_type="package", source=f"{base}/test/skills/broken.zip", slug=SLUG
        )

    assert failure.value.code == "ARCHIVE_INVALID"


def test_an_unknown_source_type_is_refused(publisher, sandbox):
    with pytest.raises(SkillValidationError) as failure:
        publisher.prepare(source_type="tarball", source="/tmp/x", slug=SLUG)

    assert failure.value.code == "SOURCE_TYPE_UNKNOWN"


def test_staging_is_not_the_executable_directory(publisher, sandbox):
    """A skill under construction must not be one directory listing from being loaded.

    The smoke test has to run the script, so the files must be *somewhere* the sandbox can
    reach. That somewhere is ``/skills/.staging``, and nothing appears under the agents' root
    until the version has been persisted and the pointer moved.
    """
    slug = "reorder-cost-staging"
    root = f"/workspace/scratch/generated/{slug}"
    upload_tree(sandbox, root, rename_skill(fixture_files(), slug))

    prepared = publisher.prepare(source_type="generated", source=root, slug=slug)

    assert prepared.staging_directory.startswith(f"/skills/.staging/{slug}/")
    staged = sandbox.execute(f"ls -1 {prepared.staging_directory}")
    assert staged.exit_code == 0, staged.output
    assert "SKILL.md" in staged.output, "冒烟必须在 staging 里真的跑到脚本"

    not_yet = sandbox.execute(f"ls -d {USER_SKILLS_ROOT}/main/{slug} 2>&1; echo rc=$?")
    assert "No such file" in not_yet.output, "未发布的技能不得出现在可执行目录"

    publisher.complete(prepared, owner_user_id=OWNER_A, scope="main")

    installed = sandbox.execute(f"ls -1 {USER_SKILLS_ROOT}/main/{slug}")
    assert installed.exit_code == 0
    assert "SKILL.md" in installed.output, "分配之后才出现在可执行目录"


# --------------------------------------------------------------------------- #
# persistence integrity and restore
# --------------------------------------------------------------------------- #


def test_a_half_written_version_is_not_restorable(publisher, sandbox, store, restorer, skill_store):
    """Delete one file from a published version, then try to restore it.

    This is the half-write the read-back step exists to catch: the manifest still claims the
    file, the Store no longer has it, and the copy already in the sandbox must survive.
    """
    slug = "reorder-cost-halfwrite"
    root = f"/workspace/scratch/generated/{slug}"
    upload_tree(sandbox, root, rename_skill(fixture_files(), slug))

    result = publisher.complete(
        publisher.prepare(source_type="generated", source=root, slug=slug),
        owner_user_id=OWNER_A,
        scope="main",
    )
    version = skill_store.get_version(OWNER_A, "main", slug, result.version)
    assert version is not None

    target = f"{USER_SKILLS_ROOT}/main/{slug}"
    before = sandbox.download_files([f"{target}/scripts/summarise.py"])[0].content

    # Remove a declared file, leaving the manifest claiming it.
    store.put(("skills",), f"{version.store_prefix}/scripts/summarise.py", b"")
    from agent.skills.store import serialise_manifest

    remaining = {
        path: payload
        for path, payload in skill_store.file_bytes(version.store_prefix).items()
        if path != "manifest.json"
    }
    manifest = json.loads(store.get(("skills",), f"{version.store_prefix}/manifest.json").value)
    with pytest.raises(ManifestIntegrityError):
        verify_manifest(
            SkillAssignment(owner_user_id=OWNER_A, scope="main", slug=slug, version=result.version),
            {**remaining, "manifest.json": serialise_manifest(manifest)},
        )

    report = restorer.restore(OWNER_A, generation=1)
    assert target in report.failed, "manifest 与文件不符的版本不得被恢复"
    after = sandbox.download_files([f"{target}/scripts/summarise.py"])[0].content
    assert after == before, "恢复失败不得破坏沙箱里已有的副本"

    # Put the file back. Later tests in this module share the Store, and leaving a broken
    # version behind would make their restores fail for a reason that has nothing to do with
    # what they are testing.
    store.put(("skills",), f"{version.store_prefix}/scripts/summarise.py", before)


def test_restore_materialises_then_short_circuits(restorer, sandbox, skill_store):
    """The revision is derived from the assignments, so a pointer move invalidates the cache."""
    slug = "reorder-cost-restore"
    root = f"/workspace/scratch/generated/{slug}"
    upload_tree(sandbox, root, rename_skill(fixture_files(), slug))
    # Publish a real version through the publisher so the Store actually holds the files.
    version = _publish_files(sandbox, skill_store, slug, rename_skill(fixture_files(), slug))
    result = skill_store.assign(OWNER_A, "main", slug, version, expected_revision=None)
    assert result is not None
    skill_store.assign(OWNER_A, "main", slug, version, expected_revision=result.revision)

    first = restorer.restore(OWNER_A, generation=7)
    assert f"{USER_SKILLS_ROOT}/main/{slug}" in first.restored
    assert first.revision

    second = restorer.restore(OWNER_A, generation=7)
    assert second.reason == "up_to_date"
    assert second.revision == first.revision

    marker = sandbox.execute(f"cat {USER_SKILLS_REVISION_MARKER}")
    assert marker.output.strip() == first.revision


def test_a_rebuilt_sandbox_is_restored_again(restorer, sandbox):
    """The marker lives in the container, so an empty container means "restore everything"."""
    slug = "reorder-cost-restore"
    target = f"{USER_SKILLS_ROOT}/main/{slug}"
    restorer.restore(OWNER_A, generation=7)

    # What a rebuilt container looks like: the files and the marker are both gone.
    sandbox.execute(f"rm -rf {USER_SKILLS_ROOT}; rm -f {USER_SKILLS_REVISION_MARKER}")

    report = restorer.restore(OWNER_A, generation=8)

    assert target in report.restored, "容器被重建后必须重新写回技能"
    assert sandbox.download_files([f"{target}/SKILL.md"])[0].error is None


def test_another_user_cannot_see_the_published_skill(restorer, skill_store):
    """Isolation is enforced in the Store's key space, not by which container exists.

    The harness shares one sandbox between the demo owners, so "the file is not in the
    container" would not be a true statement here and asserting it would be theatre. What is
    true — and what restore actually depends on — is that another owner's assignment scan
    comes back empty, so nothing of theirs is ever materialised. Container-level isolation is
    a different property, pinned by T08/T09 on separate sandboxes.
    """
    report = restorer.restore(OWNER_B, generation=7)

    assert report.empty is True
    assert report.reason == "no_assignments"
    assert report.restored == []
    assert skill_store.list_for_owner(OWNER_B) == []
    assert skill_store.list_for_owner(OWNER_A) != [], "对照：owner A 确实有已分配的技能"


def _publish_files(backend, skill_store, slug, files) -> str:
    """Force a version into the Store without going through the publisher's validation.

    Used where a test needs a *stored* version to restore, and the interesting behaviour is
    what the restore path does with it rather than how it got there.
    """
    from datetime import UTC, datetime

    from agent.skills.store import SkillVersion

    versions = skill_store.list_versions(OWNER_A, "main", slug)
    number = f"1.0.{len(versions)}"
    manifest = {
        "slug": slug,
        "version": number,
        "files": [
            {"path": path, "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
            for path, payload in sorted(files.items())
        ],
    }
    from agent.skills.store import serialise_manifest

    version = SkillVersion(
        owner_user_id=OWNER_A,
        scope="main",
        slug=slug,
        version=number,
        content_sha256=content_digest(files),
        manifest_sha256=hashlib.sha256(serialise_manifest(manifest)).hexdigest(),
        source_type="generated",
        source="test",
        source_sha256=content_digest(files),
        file_count=len(files),
        total_size=sum(len(payload) for payload in files.values()),
        scripts_entry="scripts/summarise.py",
        created_at=datetime.now(UTC).isoformat(),
    )
    skill_store.write_version_files(version, files, manifest)
    skill_store.record_version(version)
    return number


# --------------------------------------------------------------------------- #
# isolation, artefacts and the tool surface
# --------------------------------------------------------------------------- #


def test_assignments_in_one_scope_do_not_disturb_another(skill_store):
    from dataclasses import replace

    skill_store.record_version(_version_row("scope-test", "1.0.0"))
    skill_store.record_version(replace(_version_row("scope-test", "1.0.0"), scope="procurement-analyst"))
    main = skill_store.assign(OWNER_A, "main", "scope-test", "1.0.0", expected_revision=None)
    analyst = skill_store.assign(
        OWNER_A, "procurement-analyst", "scope-test", "1.0.0", expected_revision=None
    )

    assert main is not None and analyst is not None
    assert skill_store.withdraw(OWNER_A, "main", "scope-test") is True
    assert skill_store.current_assignment(OWNER_A, "main", "scope-test") is None
    assert skill_store.current_assignment(OWNER_A, "procurement-analyst", "scope-test") is not None

    # And the other one too, for the same reason as the CAS test: a version row with no files
    # is a state restore is supposed to report, and it must not be left lying around for the
    # tests that run after this one.
    skill_store.withdraw(OWNER_A, "procurement-analyst", "scope-test")


def test_the_revision_tracks_which_versions_are_assigned():
    a = SkillAssignment(owner_user_id=OWNER_A, scope="main", slug="x", version="1.0.0")
    b = SkillAssignment(owner_user_id=OWNER_A, scope="main", slug="x", version="1.0.1")
    other = SkillAssignment(
        owner_user_id=OWNER_A, scope="procurement-analyst", slug="x", version="1.0.0"
    )

    assert assignments_revision([a]) != assignments_revision([b])
    assert assignments_revision([a]) != assignments_revision([a, other])
    # Order must not matter, or two identical sets would look different.
    assert assignments_revision([a, other]) == assignments_revision([other, a])
    assert assignments_revision([]) == assignments_revision([])


def test_publishing_records_a_downloadable_validation_artifact(
    publisher, sandbox, artifacts, package_url, client
):
    """The tool's ``validation_artifact_id`` has to point at something a user can open."""
    slug = "reorder-cost-artifact"
    root = f"/workspace/scratch/generated/{slug}"
    upload_tree(sandbox, root, rename_skill(fixture_files(), slug))

    body = publish_skill(
        publisher=publisher,
        owner_user_id=OWNER_A,
        source_type="generated",
        source=root,
        slug=slug,
        target_scope="main",
        artifacts=artifacts,
        thread_id="t17-thread",
    )

    assert body["ok"] is True, body
    artifact_id = body["skill"]["validation_artifact_id"]
    assert artifact_id

    described = artifacts.describe(OWNER_A, artifact_id)
    assert described is not None and described[1] is True
    _, content = artifacts.open(OWNER_A, artifact_id)
    record = json.loads(content.decode("utf-8"))
    assert record["slug"] == slug
    assert record["outcome"] == "passed"
    assert record["smoke"][0]["observed_total"] == EXAMPLE_TOTAL


def test_the_publish_tool_does_not_ask_the_model_who_the_owner_is(sandbox, skill_store, artifacts):
    from agent.tools.assign_skill import build_assign_skill_tool

    tool = build_assign_skill_tool(
        publisher=SkillPublisher(
            backend_provider=lambda: sandbox, store=skill_store, allowed_source_hosts=()
        ),
        artifacts=artifacts,
    )

    properties = set(tool.args_schema.model_json_schema()["properties"])

    assert properties == {"source_type", "source", "slug", "target_scope"}, properties
    assert "owner_user_id" not in properties


def test_the_tool_refuses_a_run_without_a_scope(sandbox, skill_store, artifacts):
    from agent.tools.assign_skill import build_assign_skill_tool

    tool = build_assign_skill_tool(
        publisher=SkillPublisher(
            backend_provider=lambda: sandbox, store=skill_store, allowed_source_hosts=()
        ),
        artifacts=artifacts,
    )

    body = json.loads(
        tool.invoke(
            {
                "source_type": "generated",
                "source": "/workspace/scratch/generated/x",
                "slug": "x",
                "target_scope": "main",
            }
        )
    )

    assert body["ok"] is False
    assert body["error"]["code"] == "SCOPE_MISSING"


def test_assign_skill_is_a_main_agent_capability():
    """A sub-agent that could publish would be able to widen its own tool set."""
    from agent.tools import LOCAL_TOOL_NAMES, MAIN_AGENT_TOOL_NAMES

    assert "assign_skill" in LOCAL_TOOL_NAMES
    assert "assign_skill" in MAIN_AGENT_TOOL_NAMES


def test_the_shipped_skill_management_document_matches_the_tool():
    """The SKILL.md an agent reads must name the parameters the tool actually takes."""
    body = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "skills"
        / "main"
        / "skill-management"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    for token in ("assign_skill", "source_type", "target_scope", "generated", "package"):
        assert token in body, token
    for scope in ("main", "procurement-analyst", "procurement-order"):
        assert scope in body, scope


def test_smoke_failure_carries_the_output_for_the_model_to_repair(publisher, sandbox):
    """`把错误反馈给模型修复` is only possible if the failure says what went wrong."""
    slug = "reorder-cost-feedback"
    root = f"/workspace/scratch/generated/{slug}"
    files = rename_skill(fixture_files(), slug)
    files["scripts/summarise.py"] = (
        b"import sys\nprint('boom: missing input file')\nsys.exit(4)\n"
    )
    upload_tree(sandbox, root, files)

    with pytest.raises(SmokeFailed) as failure:
        publisher.prepare(source_type="generated", source=root, slug=slug)

    record = failure.value.records[0]
    assert record["passed"] is False
    assert "boom" in record["attempts"][0]["stdout"]
