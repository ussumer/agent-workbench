"""Persistence configuration and the MongoDB client lifecycle.

Three storage responsibilities are kept apart on purpose
(docs/plan/contracts/storage-sandbox.md):

* **Graph state and pending interrupts** live in the official ``MongoDBSaver``
  collections. Only LangGraph reads and writes them.
* **Long-term files** (preferences, published skill packages) live in the
  official ``MongoDBStore``.
* **Application records** (thread ownership, display messages, runs, pending
  actions) live in this project's own collections.

A display message is not a checkpoint, and a checkpoint is not a display
message; nothing in this module lets one stand in for the other. The
``MongoDBStore`` here is the MongoDB-backed one, not ``InMemoryStore`` — an
in-memory store is only acceptable in explicitly unit-scoped tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.store.mongodb import MongoDBStore
from pymongo import MongoClient
from pymongo.database import Database

from agent.persistence.indexes import ensure_application_indexes

DEFAULT_URI = "mongodb://localhost:27017"
DEFAULT_DATABASE = "rush_harness_demo"
DEFAULT_TEST_DATABASE = "rush_harness_test"

SERVER_SELECTION_TIMEOUT_MS = 5000

#: Environment variable holding the token the gateway presents to the internal approval
#: verification endpoint. A separate secret from the user session and from the grant key:
#: the gateway must be able to call that endpoint, and a token that could also mint grants
#: would make the check pointless.
DEFAULT_INTERNAL_SERVICE_TOKEN_ENV = "INTERNAL_SERVICE_TOKEN"


def internal_service_token(env: Mapping[str, str] | None = None) -> str:
    """The internal service token, without ever inventing one.

    Returns an empty string when unset, which makes the endpoint refuse every caller rather
    than accept an unauthenticated one. Failing closed here is the difference between "the
    gateway cannot verify" and "anyone can".
    """
    from agent.env_utils import load_env

    resolved = dict(load_env() if env is None else env)
    return resolved.get(DEFAULT_INTERNAL_SERVICE_TOKEN_ENV, "").strip()


class PersistenceUnavailable(RuntimeError):
    """MongoDB could not be reached; callers report blocked rather than degrading."""


@dataclass(frozen=True)
class PersistenceSettings:
    """Where each class of state is kept."""

    mongo_uri: str = DEFAULT_URI
    database: str = DEFAULT_DATABASE
    checkpoint_collection: str = "checkpoints"
    checkpoint_writes_collection: str = "checkpoint_writes"
    store_collection: str = "persistent-store"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> PersistenceSettings:
        source = dict(os.environ if env is None else env)
        return cls(
            mongo_uri=source.get("MONGODB_URI", DEFAULT_URI),
            database=source.get("MONGODB_DB", DEFAULT_DATABASE),
            checkpoint_collection=source.get("MONGODB_CHECKPOINT_COLLECTION", "checkpoints"),
            checkpoint_writes_collection=source.get(
                "MONGODB_CHECKPOINT_WRITES_COLLECTION", "checkpoint_writes"
            ),
            store_collection=source.get("MONGODB_STORE_COLLECTION", "persistent-store"),
        )

    def for_test_database(self, suffix: str) -> PersistenceSettings:
        """A separate database for one integration run.

        Acceptance must never touch ``rush_harness_demo``; every run gets its own
        suffixed database instead.
        """
        clean = suffix.strip().replace("/", "-")
        if not clean:
            raise ValueError("test database suffix must not be empty")
        return PersistenceSettings(
            mongo_uri=self.mongo_uri,
            database=f"{DEFAULT_TEST_DATABASE}_{clean}",
            checkpoint_collection=self.checkpoint_collection,
            checkpoint_writes_collection=self.checkpoint_writes_collection,
            store_collection=self.store_collection,
        )

    @property
    def is_test_database(self) -> bool:
        return self.database.startswith(DEFAULT_TEST_DATABASE)


class MongoResources:
    """Owns the client, the checkpointer and the store for one process."""

    def __init__(self, settings: PersistenceSettings) -> None:
        self._settings = settings
        self._client: MongoClient | None = None
        self._checkpointer: MongoDBSaver | None = None
        self._store: MongoDBStore | None = None

    @property
    def settings(self) -> PersistenceSettings:
        return self._settings

    @property
    def is_started(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> MongoClient:
        if self._client is None:
            raise PersistenceUnavailable("Mongo client is not started")
        return self._client

    @property
    def database(self) -> Database:
        return self.client[self._settings.database]

    @property
    def checkpointer(self) -> MongoDBSaver:
        if self._checkpointer is None:
            raise PersistenceUnavailable("checkpointer is not started")
        return self._checkpointer

    @property
    def store(self) -> MongoDBStore:
        if self._store is None:
            raise PersistenceUnavailable("store is not started")
        return self._store

    def start(self) -> MongoResources:
        """Connect, verify the connection, and create application indexes.

        A failure here is raised, never swallowed: a service that cannot persist
        has nothing meaningful to serve.
        """
        client = MongoClient(
            self._settings.mongo_uri,
            serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
            tz_aware=True,
        )
        try:
            client.admin.command("ping")
        except Exception as failure:  # noqa: BLE001 - surfaced as a typed error
            client.close()
            raise PersistenceUnavailable(
                f"cannot reach MongoDB at {_redacted(self._settings.mongo_uri)}: {failure}"
            ) from failure

        self._client = client
        self._checkpointer = MongoDBSaver(
            client,
            db_name=self._settings.database,
            checkpoint_collection_name=self._settings.checkpoint_collection,
            writes_collection_name=self._settings.checkpoint_writes_collection,
        )
        self._store = MongoDBStore(self.database[self._settings.store_collection])
        ensure_application_indexes(self.database)
        return self

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._checkpointer = None
        self._store = None

    def __enter__(self) -> MongoResources:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _redacted(uri: str) -> str:
    """Strip credentials from a connection string before it reaches a log."""
    if "@" not in uri:
        return uri
    scheme, _, remainder = uri.partition("://")
    _, _, host = remainder.rpartition("@")
    return f"{scheme}://***@{host}"
