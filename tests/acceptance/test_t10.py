"""T10 acceptance: three-way virtual filesystem and preset skill discovery.

Runs against a real sandbox and a real MongoDB-backed Store. The properties under test:

* each mount reaches a genuinely different backend, and a Store mount is invisible to the
  sandbox shell;
* a shared namespace with per-user key prefixes is enforced in code, not by prompt;
* preset skills sync incrementally, re-sync when the container generation changes, and are
  discovered through metadata before their bodies are read;
* user-published skills restore from the Store, an empty state is normal, and a version
  whose manifest does not add up is refused without deleting what is already on disk.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.main_agent import (  # noqa: E402
    MEMORIES_ROOT,
    PERSISTED_SKILLS_ROOT,
    OwnerScopedStoreBackend,
    build_virtual_backend,
    owner_key_prefix,
)
from agent.middlewares.skills_sync import (  # noqa: E402
    SKILL_FILENAME,
    SkillManifestError,
    SkillsSyncMiddleware,
    load_manifest,
    parse_frontmatter,
)
from agent.middlewares.user_skills_restore import (  # noqa: E402
    USER_SKILLS_ROOT,
    ManifestIntegrityError,
    SkillAssignment,
    StoreAssignmentReader,
    UserSkillsRestoreMiddleware,
    verify_manifest,
)
from fixtures import mongo_service, sandbox_service  # noqa: E402

pytestmark = pytest.mark.integration

OWNER_A = "demo-a"
OWNER_B = "demo-b"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def settings():
    return mongo_service.unique_settings("t10")


@pytest.fixture(scope="module")
def store(settings):
    """A real MongoDB-backed Store; InMemoryStore would not survive the restart cases."""
    from langgraph.store.mongodb import MongoDBStore
    from pymongo import MongoClient

    mongo_service.require_reachable(settings)
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    try:
        yield MongoDBStore(client[settings.database][settings.store_collection])
    finally:
        client.close()
        mongo_service.drop_test_database(settings)


@pytest.fixture(scope="module")
def sandbox():
    with sandbox_service.running_backend() as (backend, _):
        yield backend


@pytest.fixture(scope="module")
def virtual_a(sandbox, store):
    return build_virtual_backend(sandbox_backend=sandbox, store=store, owner_user_id=OWNER_A)


@pytest.fixture(scope="module")
def virtual_b(sandbox, store):
    return build_virtual_backend(sandbox_backend=sandbox, store=store, owner_user_id=OWNER_B)


# --------------------------------------------------------------------------- #
# three-way routing
# --------------------------------------------------------------------------- #


def test_each_mount_reaches_a_different_backend(virtual_a, sandbox):
    """The same virtual path shape lands on three distinct backends."""
    virtual_a.write(f"{MEMORIES_ROOT}/preferences.md", "language=zh-CN")
    virtual_a.write(f"{PERSISTED_SKILLS_ROOT}/notes.md", "published skill body")
    sandbox.write("/workspace/scratch/local.txt", "sandbox local")

    memories = virtual_a.read(f"{MEMORIES_ROOT}/preferences.md")
    persisted = virtual_a.read(f"{PERSISTED_SKILLS_ROOT}/notes.md")
    default = virtual_a.read("/workspace/scratch/local.txt")

    assert memories.error is None and "zh-CN" in memories.file_data["content"]
    assert persisted.error is None and "published skill body" in persisted.file_data["content"]
    assert default.error is None and "sandbox local" in default.file_data["content"]

    # The Store-backed paths must NOT exist as real files in the container.
    shell = sandbox.execute(
        f"ls -d {MEMORIES_ROOT} {PERSISTED_SKILLS_ROOT} 2>&1; echo rc=$?"
    )
    assert "No such file" in shell.output, shell.output


def test_store_mounts_are_invisible_to_the_sandbox_shell(virtual_a, sandbox):
    """A Store file is a virtual object, not a file in the container."""
    virtual_a.write(f"{MEMORIES_ROOT}/virtual-only.md", "not on disk")

    probe = sandbox.execute("cat /memories/virtual-only.md 2>&1; echo rc=$?")

    assert "No such file" in probe.output
    assert not virtual_a.read(f"{MEMORIES_ROOT}/virtual-only.md").error


def test_memories_are_isolated_by_namespace(virtual_a, virtual_b):
    """The memories namespace itself contains the user id."""
    virtual_a.write(f"{MEMORIES_ROOT}/preferences.md", "owner-a-only")

    mine = virtual_a.read(f"{MEMORIES_ROOT}/preferences.md")
    theirs = virtual_b.read(f"{MEMORIES_ROOT}/preferences.md")

    assert "owner-a-only" in mine.file_data["content"]
    assert theirs.error is not None
    assert theirs.file_data is None


def test_the_skills_mount_shows_only_the_owners_entries(virtual_a, virtual_b, store):
    """The namespace is shared, so isolation has to come from the code."""
    virtual_a.write(f"{PERSISTED_SKILLS_ROOT}/alpha/SKILL.md", "alpha body")
    virtual_b.write(f"{PERSISTED_SKILLS_ROOT}/beta/SKILL.md", "beta body")

    listing_a = virtual_a.ls(PERSISTED_SKILLS_ROOT)
    listing_b = virtual_b.ls(PERSISTED_SKILLS_ROOT)

    names_a = {Path(entry["path"]).name for entry in listing_a.entries}
    names_b = {Path(entry["path"]).name for entry in listing_b.entries}

    assert "alpha" in names_a
    assert "beta" not in names_a, "owner A 不应看到 owner B 的条目"
    assert "beta" in names_b
    assert "alpha" not in names_b

    # And the shared namespace really does hold both, so isolation is not an accident of
    # the data being absent.
    everything = store.search(("skills",), limit=100)
    keys = " ".join(str(item.key) for item in everything)
    assert f"{owner_key_prefix(OWNER_A)}/alpha" in keys
    assert f"{owner_key_prefix(OWNER_B)}/beta" in keys


def test_reading_another_owner_by_explicit_path_is_refused(virtual_b):
    """Naming someone else's prefix is a permission problem, not a missing file."""
    result = virtual_b.read(f"{PERSISTED_SKILLS_ROOT}/{owner_key_prefix(OWNER_A)}/alpha/SKILL.md")

    assert result.error == "permission_denied"
    assert result.file_data is None


def test_store_search_does_not_cross_users(virtual_a, virtual_b):
    """grep must be scoped too, otherwise it leaks another user's content."""
    virtual_a.write(f"{PERSISTED_SKILLS_ROOT}/alpha/SKILL.md", "unique-token-alpha-9931")

    found_by_b = virtual_b.grep("unique-token-alpha-9931", PERSISTED_SKILLS_ROOT)
    assert not found_by_b.matches, "owner B 的 search 不应命中 owner A 的内容"

    found_by_a = virtual_a.grep("unique-token-alpha-9931", PERSISTED_SKILLS_ROOT)
    assert found_by_a.matches, "owner A 应能搜到自己的内容"


def test_traversal_out_of_the_mount_is_rejected():
    """`..` must be collapsed before the scope decision, not after."""
    from agent.main_agent import OwnerScopeError

    scoped = OwnerScopedStoreBackend(object(), owner_user_id=OWNER_A)

    assert scoped.resolve("/persisted-skills/a/../../b") == f"/{owner_key_prefix(OWNER_A)}/b"
    assert scoped.resolve("/persisted-skills/") == f"/{owner_key_prefix(OWNER_A)}"
    with pytest.raises(OwnerScopeError):
        scoped.resolve(f"/persisted-skills/{owner_key_prefix(OWNER_B)}/x")
    with pytest.raises(OwnerScopeError):
        scoped.resolve("relative/path")


def test_a_relative_path_never_reaches_the_store_mount(virtual_a):
    """Routes match on the leading mount prefix, so a relative path falls through.

    It therefore lands on the default sandbox backend and fails there. The property that
    matters is that it does **not** resolve to Store content: a caller that forgets the
    leading slash must not be handed another mount's data.
    """
    virtual_a.write(f"{PERSISTED_SKILLS_ROOT}/notes.md", "store content")

    result = virtual_a.read("persisted-skills/notes.md")

    assert result.error is not None
    assert result.file_data is None, "相对路径不得读到 Store 内容"


# --------------------------------------------------------------------------- #
# preset skill manifest
# --------------------------------------------------------------------------- #


def test_the_preset_manifest_is_complete_and_unique():
    manifest = load_manifest()

    names = [skill.name for skill in manifest.skills]
    assert len(names) == len(set(names)), "技能名称必须唯一"
    assert {"skill-management", "procurement-analysis", "web-scraper"} <= set(names)

    for skill in manifest.skills:
        assert skill.description.strip(), f"{skill.name} 缺少 description"
        assert skill.document.endswith(SKILL_FILENAME)
        assert skill.files and skill.revision

    # A reference document next to the skills must not be mistaken for a skill.
    assert all(not skill.directory.endswith("chart_params.md") for skill in manifest.skills)


def test_the_manifest_refuses_incomplete_skill_sets(tmp_path):
    """A half-published skill set is worse than a loud failure."""
    good = (
        "---\nname: ok-skill\ndescription: fine\n---\n\n# body\n"
    )
    missing_description = "---\nname: no-desc\n---\n\n# body\n"
    bad_slug = "---\nname: Bad Slug\ndescription: nope\n---\n\n# body\n"

    for label, content in (
        ("missing_description", missing_description),
        ("bad_slug", bad_slug),
    ):
        root = tmp_path / label
        (root / "x").mkdir(parents=True)
        (root / "x" / SKILL_FILENAME).write_text(content, encoding="utf-8")
        with pytest.raises(SkillManifestError):
            load_manifest(root)

    # A directory with no SKILL.md at all yields no skills, which is also an error.
    empty_root = tmp_path / "empty"
    (empty_root / "docs").mkdir(parents=True)
    (empty_root / "docs" / "notes.md").write_text("# notes\n", encoding="utf-8")
    with pytest.raises(SkillManifestError):
        load_manifest(empty_root)

    # And the good shape still loads.
    ok_root = tmp_path / "ok"
    (ok_root / "ok-skill").mkdir(parents=True)
    (ok_root / "ok-skill" / SKILL_FILENAME).write_text(good, encoding="utf-8")
    assert load_manifest(ok_root).skills[0].name == "ok-skill"


def test_frontmatter_without_a_delimiter_is_rejected():
    with pytest.raises(SkillManifestError):
        parse_frontmatter("# just a heading\n", source="<inline>")


def test_progressive_disclosure_injects_only_metadata():
    """The first injection carries names, descriptions and paths — never bodies."""
    middleware = SkillsSyncMiddleware(backend_provider=lambda: None)
    directory = middleware.metadata_directory()

    assert directory, "至少应有一个预置技能"
    for entry in directory:
        assert set(entry) == {"name", "description", "path"}, entry
        assert entry["path"].startswith("/") and entry["path"].endswith(SKILL_FILENAME)

    body_markers = ("## 步骤", "## 失败条件")
    serialised = json.dumps(directory, ensure_ascii=False)
    for marker in body_markers:
        assert marker not in serialised, "首轮注入不得包含技能正文"


# --------------------------------------------------------------------------- #
# incremental sync
# --------------------------------------------------------------------------- #


def test_the_first_sync_uploads_every_preset_file(sandbox):
    middleware = SkillsSyncMiddleware(backend_provider=lambda: sandbox)

    report = middleware.sync(generation=1)

    assert report.reason == "first_sync"
    assert len(report.uploaded) == len(manifest_files_count())
    listing = sandbox.execute("find /skills -name SKILL.md | sort")
    for document in load_manifest().documents():
        assert f"/{document}" in listing.output


def manifest_files_count() -> set[str]:
    return {
        entry.path for skill in load_manifest().skills for entry in skill.files
    }


def test_an_unchanged_manifest_is_not_synced_again(sandbox):
    middleware = SkillsSyncMiddleware(backend_provider=lambda: sandbox)
    middleware.sync(generation=1)

    second = middleware.sync(generation=1)

    assert second.reason == "up_to_date"
    assert second.uploaded == []
    assert second.skipped, "未变化的文件应被记为跳过"


def test_a_changed_file_is_uploaded_incrementally(tmp_path, sandbox):
    """Only the file whose digest moved is re-uploaded."""
    source = tmp_path / "skills"
    shutil.copytree(Path(__file__).resolve().parents[2] / "src" / "skills", source)
    middleware = SkillsSyncMiddleware(backend_provider=lambda: sandbox, source_dir=source)
    middleware.sync(generation=1)

    target = source / "procurement" / "supplier-price-urls" / SKILL_FILENAME
    target.write_text(target.read_text(encoding="utf-8") + "\n<!-- edited -->\n", encoding="utf-8")

    report = middleware.sync(generation=1)

    assert report.reason == "revision_changed"
    assert report.uploaded == ["/skills/procurement/supplier-price-urls/SKILL.md"]
    assert report.skipped, "其余文件不应被重复上传"


def test_a_generation_change_forces_a_full_resync(sandbox):
    middleware = SkillsSyncMiddleware(backend_provider=lambda: sandbox)
    middleware.sync(generation=1)

    report = middleware.sync(generation=2)

    assert report.reason == "generation_changed"
    assert report.uploaded, "新 generation 必须重新同步，即使 revision 未变"


def test_a_wiped_skills_directory_is_repopulated(sandbox):
    """The sandbox's own marker is authoritative, not this process's memory."""
    middleware = SkillsSyncMiddleware(backend_provider=lambda: sandbox)
    middleware.sync(generation=1)
    sandbox.execute("rm -rf /skills")

    report = middleware.sync(generation=1)

    assert report.reason == "container_replaced"
    assert report.uploaded, "容器内副本被清空后必须重新上传"
    assert "SKILL.md" in sandbox.execute("find /skills -name SKILL.md | sort").output


# --------------------------------------------------------------------------- #
# user skills restore
# --------------------------------------------------------------------------- #


def publish(store, *, owner: str, scope: str, slug: str, version: str, body: bytes) -> None:
    """Write one published version plus its assignment pointer, manifest included."""
    prefix = f"{owner_key_prefix(owner)}/{scope}/{slug}/{version}"
    store.put(("skills",), f"{prefix}/SKILL.md", body)
    manifest = {
        "files": [
            {"path": "SKILL.md", "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
        ]
    }
    store.put(("skills",), f"{prefix}/manifest.json", json.dumps(manifest))
    store.put(("skills",), f"{owner_key_prefix(owner)}/assignments/{scope}/{slug}", {"version": version})


def test_restore_reports_an_empty_state_without_error(sandbox, store):
    restorer = UserSkillsRestoreMiddleware(
        backend_provider=lambda: sandbox, reader=StoreAssignmentReader(store)
    )

    report = restorer.restore("user-with-nothing")

    assert report.empty is True
    assert report.reason == "no_assignments"
    assert report.failed == []


def test_restore_materialises_a_published_version(sandbox, store):
    body = b"---\nname: restored-skill\ndescription: from the store\n---\n\nbody\n"
    publish(store, owner=OWNER_A, scope="main", slug="restored-skill", version="1.0.0", body=body)
    restorer = UserSkillsRestoreMiddleware(
        backend_provider=lambda: sandbox, reader=StoreAssignmentReader(store)
    )

    report = restorer.restore(OWNER_A)

    target = f"{USER_SKILLS_ROOT}/main/restored-skill"
    assert report.restored == [target]

    remote = sandbox.download_files([f"{target}/SKILL.md"])
    assert remote[0].error is None
    assert remote[0].content == body


def test_restore_is_idempotent(sandbox, store):
    """Each test publishes its own skill so the suite does not depend on run order."""
    body = b"---\nname: idempotent-skill\ndescription: same content twice\n---\n\nbody\n"
    publish(
        store, owner=OWNER_A, scope="main", slug="idempotent-skill", version="1.0.0", body=body
    )
    restorer = UserSkillsRestoreMiddleware(
        backend_provider=lambda: sandbox, reader=StoreAssignmentReader(store)
    )

    first = restorer.restore(OWNER_A)
    target = f"{USER_SKILLS_ROOT}/main/idempotent-skill"
    assert target in first.restored

    again = restorer.restore(OWNER_A)

    assert target not in again.restored, "内容一致时不应重复写入"
    assert target in again.skipped


def test_an_incomplete_manifest_is_refused_without_deleting_the_existing_copy(sandbox, store):
    """A tampered version must not be materialised, and must not destroy the good copy."""
    body = b"---\nname: tamper-target\ndescription: good copy first\n---\n\noriginal body\n"
    publish(
        store, owner=OWNER_A, scope="main", slug="tamper-target", version="1.0.0", body=body
    )
    restorer = UserSkillsRestoreMiddleware(
        backend_provider=lambda: sandbox, reader=StoreAssignmentReader(store)
    )
    assert restorer.restore(OWNER_A).restored

    target = f"{USER_SKILLS_ROOT}/main/tamper-target"
    before = sandbox.download_files([f"{target}/SKILL.md"])[0].content
    assert before == body

    # Corrupt the stored file so its digest no longer matches the manifest.
    prefix = f"{owner_key_prefix(OWNER_A)}/main/tamper-target/1.0.0"
    store.put(("skills",), f"{prefix}/SKILL.md", b"tampered-content")

    report = restorer.restore(OWNER_A)

    assert report.failed == [target]
    assert report.reason == "restore_failed"
    after = sandbox.download_files([f"{target}/SKILL.md"])[0].content
    assert after == before, "恢复失败不得删除已存在的版本"


def test_a_version_without_a_manifest_cannot_be_loaded():
    assignment = SkillAssignment(
        owner_user_id=OWNER_A, scope="main", slug="no-manifest", version="1.0.0"
    )

    with pytest.raises(ManifestIntegrityError):
        verify_manifest(assignment, {"SKILL.md": b"body without manifest"})


def test_a_manifest_path_cannot_escape_the_version_prefix():
    assignment = SkillAssignment(
        owner_user_id=OWNER_A, scope="main", slug="escaping", version="1.0.0"
    )
    payload = {
        "manifest.json": json.dumps(
            {"files": [{"path": "../secret", "sha256": "0" * 64}]}
        ).encode(),
        "SKILL.md": b"body",
    }

    with pytest.raises(ManifestIntegrityError):
        verify_manifest(assignment, payload)


def test_restore_only_reads_the_owners_prefix(sandbox, store):
    """Owner B's restore must not materialise owner A's skill."""
    other_body = b"---\nname: b-only\ndescription: b\n---\n\nb body\n"
    publish(store, owner=OWNER_B, scope="main", slug="b-only", version="1.0.0", body=other_body)
    restorer = UserSkillsRestoreMiddleware(
        backend_provider=lambda: sandbox, reader=StoreAssignmentReader(store)
    )

    report = restorer.restore(OWNER_B)

    assert report.restored == [f"{USER_SKILLS_ROOT}/main/b-only"]
    assert f"{USER_SKILLS_ROOT}/main/restored-skill" not in report.restored
