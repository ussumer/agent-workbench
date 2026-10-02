"""Integration tests for the persistence plugins themselves.

These are lower level than T07's acceptance suite: they exercise the official
``MongoDBSaver`` and ``MongoDBStore`` APIs directly, so a framework upgrade that
changes one of these signatures fails here with a precise message instead of
deep inside the application. Included in the full regression (T22).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import mongo_service  # noqa: E402

SRC_DIR = mongo_service.SRC_DIR
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from langgraph.store.memory import InMemoryStore  # noqa: E402

from agent.persistence import memories_namespace  # noqa: E402

pytestmark = pytest.mark.integration

OWNER = "demo-a"


@pytest.fixture(scope="module")
def resources():
    settings = mongo_service.unique_settings(f"persist-{uuid.uuid4().hex[:8]}")
    mongo_service.require_reachable(settings)
    started = mongo_service.start_resources(settings)
    yield started
    started.close()
    mongo_service.drop_test_database(settings)


@pytest.fixture(autouse=True)
def clean_state(resources):
    for name in ("checkpoints", "checkpoint_writes", "persistent-store"):
        resources.database[name].delete_many({})
    yield


def test_the_store_is_mongodb_backed_not_in_memory(resources):
    """An in-memory store would silently break every restart-persistence claim."""
    assert not isinstance(resources.store, InMemoryStore)
    assert type(resources.store).__module__.startswith("langgraph.store.mongodb")
    assert type(resources.checkpointer).__module__.startswith("langgraph.checkpoint.mongodb")


def test_the_checkpointer_exposes_the_official_collections(resources):
    """The saver must use the collections we configured, not its own defaults."""
    names = set(resources.database.list_collection_names())
    assert resources.settings.checkpoint_collection in names
    assert resources.settings.checkpoint_writes_collection in names


def test_the_store_lists_namespaces_and_deletes_keys(resources):
    namespace = memories_namespace(OWNER)
    resources.store.put(namespace, "preferences.md", {"language": "zh-CN"})
    resources.store.put(namespace, "history.md", {"recent_queries": ["刹车片"]})

    namespaces = list(resources.store.list_namespaces())
    assert namespace in namespaces

    resources.store.delete(namespace, "history.md")
    assert resources.store.get(namespace, "history.md") is None
    assert resources.store.get(namespace, "preferences.md") is not None


def test_the_store_search_is_namespace_scoped(resources):
    owner_namespace = memories_namespace(OWNER)
    other_namespace = memories_namespace("demo-b")
    resources.store.put(owner_namespace, "preferences.md", {"language": "zh-CN"})
    resources.store.put(other_namespace, "preferences.md", {"language": "en-US"})

    found = resources.store.search(owner_namespace, limit=10)

    assert [item.key for item in found] == ["preferences.md"]
    assert found[0].value["language"] == "zh-CN"


def test_indexes_survive_a_reconnect(resources):
    """A second, independent connection must see the same indexes.

    The shared fixture is left running: the point is that indexes persist, not
    that tearing down one client affects another.
    """
    from agent.persistence import describe_indexes

    before = describe_indexes(resources.database)
    second = mongo_service.start_resources(resources.settings)
    try:
        after = describe_indexes(second.database)
        assert after == before
        assert any(name.startswith("uk_") for name in after["threads"])
    finally:
        second.close()
