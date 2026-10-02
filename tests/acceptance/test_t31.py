"""Real Mongo version reservations and controlled publication races; no memory store."""

import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acceptance.test_t17 import fixture_files, rename_skill, upload_tree  # noqa: E402
from agent.skills.pipeline import (  # noqa: E402
    PreparedSkill,
    SkillPublisher,
    SkillValidationError,
    SourceBundle,
    validate_bundle,
)
from agent.skills.store import SkillStore, content_digest  # noqa: E402
from fixtures import mongo_service, sandbox_service  # noqa: E402


@pytest.fixture(scope="module")
def resources():
    settings = mongo_service.unique_settings(f"t31-{uuid.uuid4().hex[:10]}")
    resources = mongo_service.start_resources(settings)
    try:
        yield resources
    finally:
        resources.close()
        mongo_service.drop_test_database(settings)


@pytest.fixture(scope="module")
def sandbox():
    with sandbox_service.running_backend() as (backend, _):
        yield backend


@pytest.fixture()
def publisher(resources, sandbox):
    return SkillPublisher(backend_provider=lambda: sandbox,
                          store=SkillStore(resources.store, resources.database))


def skill(slug, suffix=""):
    files = rename_skill(fixture_files(), slug)
    files["SKILL.md"] += suffix.encode()
    bundle = SourceBundle("generated", "/workspace/scratch/source", files, content_digest(files), "")
    return validate_bundle(bundle, slug=slug)


def test_concurrent_persistence_never_shares_or_overwrites_a_version(publisher, monkeypatch):
    slug = "t31-parallel-persist"
    candidates = [skill(slug, f"\nvariation {index}") for index in range(2)]
    barrier = threading.Barrier(2, timeout=30)
    original = publisher._store.write_version_files

    def overlapping_write(version, files, manifest):
        barrier.wait()
        return original(version, files, manifest)

    monkeypatch.setattr(publisher._store, "write_version_files", overlapping_write)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(publisher._persist, candidate, owner_user_id="demo-a", scope="main")
                   for candidate in candidates]
        versions = [future.result(timeout=60) for future in futures]
    assert len({version.version for version in versions}) == 2
    for candidate, version in zip(candidates, versions, strict=True):
        saved = publisher._store.read_version_files(version)
        assert all(saved[name] == content for name, content in candidate.files.items())


def test_partial_write_retry_allocates_a_new_version_and_preserves_the_orphan(publisher, resources, monkeypatch):
    candidate = skill("t31-half-write")
    original = resources.store.put
    writes = 0

    def fail_second(*args, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("injected Store write failure")
        return original(*args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(resources.store, "put", fail_second)
        with pytest.raises(RuntimeError, match="injected"):
            publisher._persist(candidate, owner_user_id="demo-a", scope="main")
    prefix = "users/demo-a/main/t31-half-write/1.0.0"
    orphan = publisher._store.file_bytes(prefix)
    assert "manifest.json" in orphan
    assert publisher._store.get_version("demo-a", "main", candidate.slug, "1.0.0") is None
    version = publisher._persist(candidate, owner_user_id="demo-a", scope="main")
    assert version.version != "1.0.0"
    assert publisher._store.file_bytes(prefix) == orphan
    assert publisher._store.get_version("demo-a", "main", candidate.slug, version.version) is not None


def test_reserved_version_is_not_visible_or_assignable(publisher):
    store = publisher._store
    number, _token = store.reserve_version("demo-a", "main", "t31-reserved")
    assert store.get_version("demo-a", "main", "t31-reserved", number) is None
    assert store.list_versions("demo-a", "main", "t31-reserved") == []
    assert store.assign("demo-a", "main", "t31-reserved", number, expected_revision=None) is None


def test_legacy_published_version_remains_readable_and_numbers_are_not_reused(publisher):
    candidate = skill("t31-legacy")
    old = publisher._persist(candidate, owner_user_id="demo-a", scope="main")
    publisher._store._versions.update_one(
        {"owner_user_id": "demo-a", "slug": candidate.slug, "version": old.version},
        {"$unset": {"publication_status": "", "reservation_id": ""}},
    )
    assert publisher._store.get_version("demo-a", "main", candidate.slug, old.version) is not None
    number, _ = publisher._store.reserve_version("demo-a", "main", candidate.slug)
    assert number != old.version


def test_cas_loser_does_not_rebase_and_replace_the_winner(publisher, monkeypatch):
    slug = "t31-pointer-race"
    candidates = [skill(slug, f"\ncandidate {index}") for index in range(2)]
    barrier = threading.Barrier(2, timeout=30)
    lock = threading.Lock()
    original = publisher._persist

    def persisted_before_assignment(candidate, **kwargs):
        # Serialize persistence to isolate the pointer race from the allocation race above.
        with lock:
            version = original(candidate, **kwargs)
        barrier.wait()
        return version

    monkeypatch.setattr(publisher, "_persist", persisted_before_assignment)

    def complete(candidate):
        try:
            return publisher.complete(PreparedSkill(candidate), owner_user_id="demo-a", scope="main")
        except SkillValidationError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(complete, candidates))
    winners = [result for result in results if not isinstance(result, SkillValidationError)]
    losers = [result for result in results if isinstance(result, SkillValidationError)]
    assert len(winners) == len(losers) == 1
    assert losers[0].code == "ASSIGNMENT_CONFLICT"
    pointer = publisher._store.current_assignment("demo-a", "main", slug)
    assert pointer.version == winners[0].version and pointer.revision == 1
    assert len(publisher._store.list_versions("demo-a", "main", slug)) == 2


def test_each_preparation_has_an_independent_staging_directory(publisher, sandbox):
    slug = "t31-instructions"
    root = f"/workspace/scratch/{slug}"
    upload_tree(sandbox, root, {"SKILL.md": f"---\nname: {slug}\ndescription: guide\n---\nGuide\n".encode()})
    first = publisher.prepare(source_type="generated", source=root, slug=slug)
    second = publisher.prepare(source_type="generated", source=root, slug=slug)
    assert first.staging_directory != second.staging_directory
    assert first.smoke[0]["validation_level"] == "structural"
    assert first.smoke[0]["behavior_verified"] is False


def test_pre_reservation_orphan_is_not_overwritten_or_permanently_blocks_retry(publisher, resources):
    candidate = skill("t31-old-orphan")
    key = f"users/demo-a/main/{candidate.slug}/1.0.0/manifest.json"
    resources.store.put(("skills",), key, b"legacy half-write")
    version = publisher._persist(candidate, owner_user_id="demo-a", scope="main")
    assert version.version != "1.0.0"
    assert resources.store.get(("skills",), key).value == b"legacy half-write"


def test_absent_version_cannot_be_assigned(publisher):
    assert publisher._store.assign("demo-a", "main", "t31-absent", "1.0.0", expected_revision=None) is None


def test_corrupted_manifest_cannot_become_a_published_version(publisher, resources, monkeypatch):
    candidate = skill("t31-manifest-corruption")
    original = resources.store.put

    def corrupt(namespace, key, value, *args, **kwargs):
        if key.endswith("/manifest.json"):
            value = b"corrupted manifest"
        return original(namespace, key, value, *args, **kwargs)

    monkeypatch.setattr(resources.store, "put", corrupt)
    with pytest.raises(SkillValidationError) as failure:
        publisher._persist(candidate, owner_user_id="demo-a", scope="main")
    assert failure.value.code == "PERSISTENCE_INCOMPLETE"
    assert publisher._store.list_versions("demo-a", "main", candidate.slug) == []
    assert publisher._store.assign("demo-a", "main", candidate.slug, "1.0.0", expected_revision=None) is None
