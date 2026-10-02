"""Retention is proved by real records, including an interrupted session."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import mongo_service  # noqa: E402
from live.stack import running_stack  # noqa: E402


class InterruptedSession(RuntimeError):
    """A failure after the application has written real state."""


@pytest.mark.integration
def test_persistent_session_keeps_mongo_and_erp_after_failure_and_restart(tmp_path):
    label = f"retention-{uuid.uuid4().hex}"
    settings = mongo_service.unique_settings(label)
    record = {"_id": "retention-proof", "owner_user_id": "demo-a", "body": "保留原文"}
    body = json.dumps({
        "supplier_id": "S001", "currency": "CNY",
        "lines": [{"part_id": "P001", "quantity": 3, "unit_price": "25.50"}],
        "note": "restart-proof",
    }).encode("utf-8")
    headers = {"X-Operation-Id": "retention-create", "Content-Type": "application/json"}
    try:
        with pytest.raises(InterruptedSession):
            with running_stack(run_dir=tmp_path, database_name=label, preserve_data=True) as stack:
                stack.database["retention_proof"].insert_one(record.copy())
                with stack.erp.client() as client:
                    created = client.post("/api/erp/v1/orders", content=body, headers=headers)
                    assert created.status_code == 201, created.text
                    order = created.json()["data"]
                    assert order["total_amount"] == "76.50"
                raise InterruptedSession("retain writes on exceptional teardown")

        # Check after teardown, before a second startup can hide a deletion by reseeding.
        with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000) as client:
            assert client[settings.database]["retention_proof"].find_one({"_id": record["_id"]}) == record

        with running_stack(run_dir=tmp_path, database_name=label, preserve_data=True) as stack:
            assert stack.database["retention_proof"].find_one({"_id": record["_id"]}) == record
            with stack.erp.client() as client:
                found = client.get("/api/erp/v1/orders", params={"order_id": order["order_id"]})
                assert found.status_code == 200
                assert found.json()["data"]["items"] == [order]
                replay = client.post("/api/erp/v1/orders", content=body, headers=headers)
                assert replay.status_code == 201, replay.text
                assert replay.headers["Idempotent-Replayed"] == "true"
                assert replay.json()["data"] == order

        with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000) as client:
            assert client[settings.database]["retention_proof"].find_one({"_id": record["_id"]}) == record
    finally:
        # This test owns a UUID database, never the developer's dev database.
        mongo_service.drop_test_database(settings)


@pytest.mark.integration
@pytest.mark.parametrize("interrupted", [False, True])
def test_named_test_sessions_still_clean_before_and_after_use(tmp_path, interrupted):
    label = f"ephemeral-{uuid.uuid4().hex}"
    settings = mongo_service.unique_settings(label)
    mongo_service.require_reachable(settings)
    try:
        with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000) as client:
            client[settings.database]["retention_proof"].insert_one({"_id": "stale"})
        expectation = pytest.raises(InterruptedSession) if interrupted else contextlib.nullcontext()
        with expectation:
            with running_stack(run_dir=tmp_path, database_name=label) as stack:
                assert stack.database["retention_proof"].count_documents({}) == 0
                stack.database["retention_proof"].insert_one({"_id": "fresh"})
                if interrupted:
                    raise InterruptedSession("test teardown must clean even on failure")
        with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000) as client:
            assert settings.database not in client.list_database_names()
    finally:
        mongo_service.drop_test_database(settings)


@pytest.mark.parametrize("coordinates", [{"database_name": "named"}, {"run_dir": Path("unused")}])
def test_retention_requires_stable_database_and_directory(coordinates):
    with pytest.raises(ValueError, match="stable run_dir and database_name"):
        with running_stack(preserve_data=True, **coordinates):
            pytest.fail("invalid retention coordinates were accepted")


def test_dev_launcher_explicitly_selects_retention(monkeypatch, tmp_path):
    """Unit check of the launcher boundary; retention itself uses real services above."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "dev.py"
    spec = importlib.util.spec_from_file_location("retention_dev", path)
    dev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dev)
    session = tmp_path / "session.json"
    stop = tmp_path / "stop"
    stop.touch()
    monkeypatch.setattr(dev, "DEV_ROOT", tmp_path)
    monkeypatch.setattr(dev, "SESSION", session)
    monkeypatch.setattr(dev, "STOP_FILE", stop)

    @contextlib.contextmanager
    def capture_stack(**kwargs):
        assert kwargs == {"run_dir": tmp_path / "run", "database_name": "isolated", "preserve_data": True}
        service = SimpleNamespace(base_url="http://unit-boundary")
        yield SimpleNamespace(base_url=service.base_url, settings=SimpleNamespace(database="isolated"),
                              erp=service, gateway=service, site=service)

    monkeypatch.setattr("live.stack.running_stack", capture_stack)
    assert dev.command_serve(argparse.Namespace(database="isolated")) == 0
    assert json.loads(session.read_text(encoding="utf-8"))["database"] == "isolated"
