"""Wiring for the assistant's persistence.

This module is the only place that decides which store backs which concern, so a
route can never be re-pointed by accident:

* ``checkpointer()`` → the official ``MongoDBSaver`` (graph state, pending interrupts)
* ``store()`` → the official MongoDB-backed ``MongoDBStore`` (preferences, published skills)
* ``repository()`` → this project's application collections

The graph itself is assembled in T11; nothing here fakes an agent in the meantime.
"""

from __future__ import annotations

from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.store.mongodb import MongoDBStore

from agent.persistence.indexes import describe_indexes
from agent.persistence.repository import ApplicationRepository
from agent.persistence.scoped_store import UserScopedStore
from api_view.web_config import MongoResources, PersistenceSettings


def build_persistence(settings: PersistenceSettings | None = None) -> MongoResources:
    """Start MongoDB resources for one process.

    Raises :class:`~api_view.web_config.PersistenceUnavailable` when MongoDB cannot
    be reached, so callers report blocked instead of degrading to memory.
    """
    return MongoResources(settings or PersistenceSettings.from_env()).start()


def checkpointer(resources: MongoResources) -> MongoDBSaver:
    return resources.checkpointer


def store(resources: MongoResources) -> MongoDBStore:
    return resources.store


def repository(resources: MongoResources) -> ApplicationRepository:
    return ApplicationRepository(resources.database)


def scoped_store(resources: MongoResources, user_id: str) -> UserScopedStore:
    """A Store view restricted to ``user_id``'s namespaces and key prefixes."""
    return UserScopedStore(resources.store, user_id)


def index_report(resources: MongoResources) -> dict[str, list[str]]:
    """Current index names per application collection, for evidence."""
    return describe_indexes(resources.database)
