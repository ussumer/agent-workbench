"""Versioned Mongo data and independent Python processes in OpenSandbox."""

from __future__ import annotations

import hashlib
import json
import shlex
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from agent.persistence.indexes import (
    COLLECTION_KERNEL_EXECUTIONS,
    COLLECTION_KERNEL_OUTPUTS,
    COLLECTION_KERNEL_SESSIONS,
    ensure_application_indexes,
)
from agent.planning.computation_protocol import (
    MAX_PREVIEW_BYTES,
    ComputationError,
    decode_staged,
    encode_values,
    validate_code,
    validate_names,
    validate_operation,
    validate_session,
)


class ComputationService:
    def __init__(self, database: Any, backend_provider: Callable):
        self.database, self.backend_provider = database, backend_provider
        # Keep existing declared/indexed collections; schema 2 is a separate
        # data protocol, never attach to a legacy live-kernel session.
        self.sessions = database[COLLECTION_KERNEL_SESSIONS]
        self.executions = database[COLLECTION_KERNEL_EXECUTIONS]
        self.outputs = database[COLLECTION_KERNEL_OUTPUTS]
        ensure_application_indexes(database)

    def _scope(self, owner: str, thread: str, session: str) -> dict:
        validate_session(session)
        if not owner or not thread or self.database.threads.find_one(
            {"owner_user_id": owner, "thread_id": thread}) is None:
            raise ComputationError("COMPUTATION_SCOPE_REQUIRED", "trusted owned thread required")
        return {"owner_user_id": owner, "thread_id": thread, "session": session}

    def _session(self, scope: dict) -> dict:
        digest = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        try:
            self.sessions.update_one({"_id": digest}, {"$setOnInsert": {
                **scope, "schema_version": 2, "version": 0, "values_json": "{}",
                "active": None, "last_commit": None, "quarantined_sandbox_ids": [],
            }}, upsert=True)
        except DuplicateKeyError:
            pass
        row = self.sessions.find_one({"_id": digest, **scope})
        if row is None or row.get("schema_version") != 2:
            raise ComputationError("LEGACY_SESSION", "legacy kernel data needs explicit migration")
        return row

    def status(self, owner: str, thread: str, *, session: str = "analysis") -> dict:
        row = self._session(self._scope(owner, thread, session))
        values = json.loads(row["values_json"])
        return {"session": session, "version": row["version"], "active": row["active"],
                "last_commit": row["last_commit"], "data": {name: {
                    "bytes": len(json.dumps(value).encode()),
                    "sha256": hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(),
                } for name, value in values.items()}}

    def _quarantine(self, row: dict, proxy, identity: tuple[str, int]) -> None:
        # Persist before touching the process-local proxy. Future service
        # instances and other threads of this owner refuse the same container.
        self.sessions.update_one({"_id": row["_id"]},
                                 {"$addToSet": {"quarantined_sandbox_ids": identity[0]}})
        self.database.sandbox_registry.update_one({"sandbox_id": identity[0]},
            {"$set": {"computation_quarantined": True}})
        proxy.quarantine_computation(expected_identity=identity)

    def execute(self, owner: str, thread: str, code: str, *, read_names: list[str] | tuple[str, ...] = (),
                session: str = "analysis", timeout: int = 60, expected_version: int | None = None,
                operation_id: str | None = None, cancel_signal: threading.Event | None = None) -> dict:
        validate_code(code, timeout)
        names = validate_names(read_names)
        scope = self._scope(owner, thread, session)
        row = self._session(scope)
        if expected_version is not None and (type(expected_version) is not int or expected_version != row["version"]):
            raise ComputationError("VERSION_CONFLICT", "expected committed version is stale")
        values = json.loads(row["values_json"])
        if any(name not in values for name in names):
            raise ComputationError("STATE_NOT_FOUND", "requested data is not committed")
        opid = validate_operation(operation_id or uuid.uuid4().hex)
        operation_scope = {**scope, "operation_id": opid}
        # Operation reservation precedes acquiring the session: duplicate IDs
        # never replay code, including after a failed/ambiguous operation.
        try:
            self.executions.insert_one({**operation_scope, "status": "queued", "code": code,
                                        "base_version": row["version"], "read_names": list(names)})
        except DuplicateKeyError as failure:
            raise ComputationError("DUPLICATE_OPERATION", "operation IDs cannot be reused") from failure
        claimed = self.sessions.update_one({"_id": row["_id"], **scope, "version": row["version"],
                                           "active": None}, {"$set": {"active": opid, "cancelled": False}})
        if claimed.modified_count != 1:
            self.executions.update_one(operation_scope, {"$set": {"status": "refused"}})
            raise ComputationError("COMPUTATION_BUSY", "session version changed or already running")
        deadline = time.monotonic() + timeout
        proxy = None
        identity = None
        launched = False
        launch_attempted = False
        publishing = False
        result = {"operation_id": opid, "status": "failed", "base_version": row["version"],
                  "version": row["version"], "stdout": "", "stderr": ""}

        def cancelled() -> bool:
            if cancel_signal is not None and cancel_signal.is_set():
                self.sessions.update_one({"_id": row["_id"], "active": opid},
                                         {"$set": {"cancelled": True}})
            current = self.sessions.find_one({"_id": row["_id"], "active": opid})
            return current is None or current.get("cancelled", False) or time.monotonic() >= deadline

        def started(execution_id: str) -> None:
            nonlocal launched
            launched = True
            self.executions.update_one(operation_scope, {"$set": {
                "execution_id": execution_id, "status": "running"}})

        try:
            if cancelled():
                result["status"] = "cancelled"
                return result
            proxy = self.backend_provider(owner)
            identity = (proxy.id, proxy.generation)
            if self.sessions.find_one({"owner_user_id": owner,
                                       "quarantined_sandbox_ids": identity[0]}) is not None:
                raise ComputationError("ENVIRONMENT_QUARANTINED", "replace uncertain execution environment")
            root = "/workspace/.computations/" + opid
            self.executions.update_one(operation_scope, {"$set": {
                "sandbox_id": identity[0], "generation": identity[1], "root": root}})
            metadata = {"operation_id": opid, "base_version": row["version"],
                        "read_names": list(names), "timeout": max(0.01, deadline - time.monotonic())}
            files = [(root + "/supervisor.py", Path(__file__).with_name("computation_process.py").read_bytes()),
                     (root + "/code.py", code.encode()),
                     (root + "/metadata.json", json.dumps(metadata).encode())]
            files.extend((root + "/input-" + name + ".json", json.dumps(values[name], allow_nan=False).encode())
                         for name in names)
            uploads = proxy.upload_files(files)
            if len(uploads) != len(files) or any(item.error for item in uploads):
                raise ComputationError("UPLOAD_FAILED", "computation inputs were not staged")
            command = "python -u " + shlex.quote(root + "/supervisor.py") + " " + shlex.quote(root)
            launch_attempted = True
            terminal = proxy.computation_run(command, timeout=max(0.01, deadline - time.monotonic()),
                cancel_path=root + "/cancel", should_cancel=cancelled, on_started=started,
                expected_identity=identity)
            if (proxy.id, proxy.generation) != identity:
                raise ComputationError("ENVIRONMENT_REPLACED", "stale execution environment")
            report = json.loads(self._download(proxy, root + "/report.json"))
            if terminal["exit_code"] != 0 or report.get("operation_id") != opid or report.get("clean") is not True:
                raise ComputationError("TERMINATION_UNCONFIRMED", "process tree cleanup was not confirmed")
            result.update({"status": report["status"], "processes_stopped": True,
                           "worker_pid": report["worker_pid"], "terminated_pids": report["terminated_pids"]})
            for seq, kind in enumerate(("stdout", "stderr")):
                raw = self._download(proxy, root + "/" + kind)
                # Bounded at source by worker RLIMIT_FSIZE; persist complete output
                # in chunks below Mongo's document limit, preview separately.
                for offset in range(0, len(raw), 128 * 1024):
                    self.outputs.insert_one({**operation_scope, "seq": seq * 1000 + offset // (128 * 1024),
                                             "kind": kind, "offset": offset, "content": raw[offset:offset + 128 * 1024]})
                result[kind] = raw[:MAX_PREVIEW_BYTES].decode("utf-8", errors="replace")
            if report["status"] != "completed" or cancelled():
                if cancelled():
                    result["status"] = "timed_out" if time.monotonic() >= deadline else "cancelled"
                return result
            staged = decode_staged(self._download(proxy, root + "/staged.json"), opid, row["version"])
            merged = {**values, **staged}
            text = encode_values(merged)
            self.executions.update_one(operation_scope, {"$set": {"candidate_values_json": text}})

            def publish():
                nonlocal publishing
                if cancelled():
                    return None
                publishing = True
                return self.sessions.find_one_and_update({"_id": row["_id"], **scope,
                    "version": row["version"], "active": opid, "cancelled": False}, {"$set": {
                        "values_json": text, "version": row["version"] + 1, "last_commit": opid,
                        # Keep the active slot until terminal recording/reconciliation
                        # so a newer commit cannot overwrite last_commit prematurely.
                        }}, return_document=ReturnDocument.AFTER)

            published = proxy.computation_publish(publish, expected_identity=identity)
            if published is None:
                result["status"] = "stale"
            else:
                result.update({"status": "completed", "version": published["version"],
                               "saved_names": sorted(staged)})
            return result
        except Exception as failure:
            # A launched command without confirmed cleanup makes the entire
            # container unusable. JSON/commit errors after cleanup need no rebuild.
            uncertain_start = launch_attempted and not (isinstance(failure, TimeoutError) and not launched)
            if proxy is not None and identity is not None and uncertain_start and not result.get("processes_stopped"):
                self._quarantine(row, proxy, identity)
                result["environment_quarantined"] = True
            result.update({"status": "failed", "error": {"code": getattr(failure, "code", "COMPUTATION_FAILED"),
                                                         "message": str(failure)}})
            if publishing:
                result["status"] = "uncertain"
            if isinstance(failure, TimeoutError) and not launched:
                result["status"] = "timed_out" if time.monotonic() >= deadline else "cancelled"
            return result
        finally:
            try:
                # Revoke before resolving a lost publication acknowledgement.
                # Even a delayed Mongo request can no longer satisfy this fence.
                revoked = self.sessions.find_one_and_update({"_id": row["_id"], "active": opid},
                    {"$set": {"active": None, "cancelled": True}}, return_document=ReturnDocument.BEFORE)
                if publishing and result["status"] == "uncertain":
                    if revoked is not None and revoked["last_commit"] == opid:
                        result.update({"status": "completed", "version": revoked["version"],
                                       "publication_reconciled": True})
                    elif revoked is not None:
                        result["status"] = "failed"
                self.executions.update_one(operation_scope, {"$set": {"status": result["status"], "result": result}})
            except Exception as failure:
                # A database outage is neither a rollback nor a pass. An active
                # operation can be revoked/reconciled by explicit recovery later.
                result.update({"status": "uncertain", "error": {
                    "code": "PERSISTENCE_UNCERTAIN", "message": str(failure)}})

    @staticmethod
    def _download(proxy, path: str) -> bytes:
        items = proxy.download_files([path])
        if len(items) != 1 or items[0].error or items[0].content is None:
            raise ComputationError("RESULT_MISSING", "required computation file is missing")
        return items[0].content

    def cancel(self, owner: str, thread: str, operation_id: str) -> bool:
        validate_operation(operation_id)
        row = self.executions.find_one({"owner_user_id": owner, "thread_id": thread,
                                        "operation_id": operation_id})
        if row is None:
            raise ComputationError("OPERATION_NOT_FOUND", "operation is not owned by this thread")
        scope = self._scope(owner, thread, row["session"])
        changed = self.sessions.update_one({**scope, "active": operation_id,
            "last_commit": {"$ne": operation_id}}, {"$set": {"cancelled": True}})
        if changed.matched_count == 0:
            return False
        if row.get("root"):
            proxy = self.backend_provider(owner)
            identity = (row["sandbox_id"], row["generation"])
            try:
                proxy.computation_cancel(row["root"] + "/cancel", expected_identity=identity)
            except Exception:
                self._quarantine(self._session(scope), proxy, identity)
                raise
        return True

    def read_output(self, owner: str, thread: str, operation_id: str, *, kind: str = "stdout", offset: int = 0) -> dict:
        validate_operation(operation_id)
        if kind not in {"stdout", "stderr"} or type(offset) is not int or offset < 0:
            raise ComputationError("INVALID_OUTPUT_PAGE", "invalid output page")
        row = self.executions.find_one({"owner_user_id": owner, "thread_id": thread, "operation_id": operation_id})
        if row is None:
            raise ComputationError("OPERATION_NOT_FOUND", "operation is not owned by this thread")
        chunks = self.outputs.find({"owner_user_id": owner, "thread_id": thread,
                                   "operation_id": operation_id, "kind": kind}).sort("offset", 1)
        raw = b"".join(bytes(chunk["content"]) for chunk in chunks)
        page = raw[offset:offset + MAX_PREVIEW_BYTES]
        return {"text": page.decode("utf-8", errors="replace"), "next_offset": offset + len(page),
                "total_bytes": len(raw)}

    def recover(self, owner: str, thread: str, *, session: str = "analysis") -> dict:
        """Revoke an abandoned operation, verify cleanup, retain committed data.

        This is explicit recovery, not lease expiry or automatic code replay.
        It is safe against a live old API too: cancelling the authoritative
        active slot permanently prevents that worker's publication.
        """
        scope = self._scope(owner, thread, session)
        row = self._session(scope)
        opid = row["active"]
        if opid is None:
            return self.status(owner, thread, session=session)
        fenced = self.sessions.find_one_and_update({"_id": row["_id"], "active": opid},
            {"$set": {"cancelled": True}}, return_document=ReturnDocument.BEFORE)
        if fenced is None:
            return self.status(owner, thread, session=session)
        row = fenced
        if row["last_commit"] == opid:
            # The process tree was already confirmed before this atomic publish.
            # A crash after publication must not pretend that committed data rolled back.
            self.sessions.update_one({"_id": row["_id"], "active": opid, "last_commit": opid},
                                     {"$set": {"active": None}})
            result = {"operation_id": opid, "status": "completed", "version": row["version"],
                      "publication_reconciled": True}
            self.executions.update_one({**scope, "operation_id": opid}, {"$set": {
                "status": "completed", "recovery": result}})
            return result
        operation = self.executions.find_one({**scope, "operation_id": opid})
        proxy = None
        identity = None
        result = {"operation_id": opid, "status": "cancelled", "version": row["version"]}
        try:
            if operation and operation.get("sandbox_id"):
                proxy = self.backend_provider(owner)
                identity = (operation["sandbox_id"], operation["generation"])
                if proxy.id != identity[0]:
                    # Replacement is a safe place to load Mongo data; an unknown
                    # old container is still durably forbidden for future reuse.
                    self._quarantine(row, proxy, identity)
                    result["environment_quarantined"] = True
                else:
                    proxy.computation_cancel(operation["root"] + "/cancel", expected_identity=identity)
                    if not operation.get("execution_id"):
                        raise ComputationError("TERMINATION_UNCONFIRMED", "launch may have been interrupted before registration")
                    terminal = proxy.computation_wait(operation["execution_id"], expected_identity=identity)
                    report = json.loads(self._download(proxy, operation["root"] + "/report.json"))
                    if terminal["exit_code"] != 0 or report.get("clean") is not True or report.get("operation_id") != opid:
                        raise ComputationError("TERMINATION_UNCONFIRMED", "recovery cannot confirm stopped processes")
                    result.update({"processes_stopped": True, "worker_pid": report["worker_pid"],
                                   "terminated_pids": report["terminated_pids"]})
        except Exception as failure:
            if proxy is not None and identity is not None:
                self._quarantine(row, proxy, identity)
            result.update({"status": "failed", "environment_quarantined": True,
                           "error": {"code": "TERMINATION_UNCONFIRMED", "message": str(failure)}})
        finally:
            self.sessions.update_one({"_id": row["_id"], "active": opid, "cancelled": True},
                                     {"$set": {"active": None}})
            self.executions.update_one({**scope, "operation_id": opid}, {"$set": {"status": result["status"],
                                                                              "recovery": result}})
        return result
