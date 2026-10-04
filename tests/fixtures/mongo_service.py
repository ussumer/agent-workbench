"""MongoDB helpers for integration and acceptance tests.

Two rules the plan is explicit about:

* Tests never touch ``rush_harness_demo``. Every run derives its own
  ``rush_harness_test_<suffix>`` database and drops only that one.
* An unreachable MongoDB is a failure to report, not something to paper over with
  an in-memory store.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from pymongo import MongoClient

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api_view.web_config import (  # noqa: E402
    DEFAULT_TEST_DATABASE,
    PersistenceSettings,
    MongoResources,
)


class MongoUnavailable(RuntimeError):
    """MongoDB is not reachable, so the test cannot make its claim."""


def unique_settings(suffix: str | None = None) -> PersistenceSettings:
    """Settings pointing at a private test database for this run."""
    label = suffix or uuid.uuid4().hex[:12]
    return PersistenceSettings.from_env().for_test_database(label)


def require_reachable(settings: PersistenceSettings) -> None:
    """Fail loudly when MongoDB is missing instead of skipping the test."""
    try:
        with MongoClient(
            settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True
        ) as client:
            client.admin.command("ping")
    except Exception as failure:  # noqa: BLE001 - re-raised as a typed error
        raise MongoUnavailable(
            f"MongoDB is not reachable at {settings.mongo_uri}: {failure}. "
            "Start it with: docker compose -f infra/compose.yml up -d mongo"
        ) from failure


def start_resources(settings: PersistenceSettings) -> MongoResources:
    """Start Mongo-backed resources for a test database, verifying reachability first."""
    require_reachable(settings)
    return MongoResources(settings).start()


def drop_test_database(settings: PersistenceSettings) -> None:
    """Drop only this run's test database; refuse to touch anything else."""
    if not settings.database.startswith(DEFAULT_TEST_DATABASE):
        raise RuntimeError(
            f"refusing to drop {settings.database!r}: not a test database"
        )
    with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000) as client:
        client.drop_database(settings.database)


def python_env() -> dict[str, str]:
    """Environment for a child Python process: the project sources on PYTHONPATH."""
    import os

    return {
        **os.environ,
        "PYTHONPATH": f"{SRC_DIR}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "PYTHONUNBUFFERED": "1",
    }
