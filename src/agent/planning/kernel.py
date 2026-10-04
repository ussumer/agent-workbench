"""Mongo-registered Python contexts on the existing owner-scoped OpenSandbox.

No source code is executed on the host. Official SDK contexts hold live objects;
Mongo JSON checkpoints recover selected data without replaying Actor code.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from opensandbox.exceptions import SandboxApiException
from opensandbox.models.execd_sync import ExecutionHandlersSync
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.persistence.indexes import (
    COLLECTION_KERNEL_EXECUTIONS,
    COLLECTION_KERNEL_OUTPUTS,
    COLLECTION_KERNEL_SESSIONS,
    ensure_application_indexes,
)
from agent.planning.kernel_protocol import (
    MAX_PREVIEW_BYTES,
    KernelError,
    decode_snapshot,
    restore_source,
    snapshot_source,
    validate_code,
    validate_names,
    validate_session,
)


def _now() -> datetime:
    return datetime.now(UTC)


class KernelService:
    def __init__(self, database: Database, backend_provider: Callable[[str], SandboxBackendProxy]):
        self.database = database
        self.backend_provider = backend_provider
        self.sessions = database[COLLECTION_KERNEL_SESSIONS]
        self.executions = database[COLLECTION_KERNEL_EXECUTIONS]
        self.outputs = database[COLLECTION_KERNEL_OUTPUTS]
        ensure_application_indexes(database)

    def _scope(self, owner: str, thread: str, session: str) -> dict:
        validate_session(session)
        if not isinstance(owner, str) or not owner or not isinstance(thread, str) or not thread:
            raise KernelError("KERNEL_SCOPE_REQUIRED", "trusted owner and thread are required")
        if self.database.threads.find_one({"owner_user_id": owner, "thread_id": thread}) is None:
            raise KernelError("KERNEL_THREAD_NOT_FOUND", "thread is not owned by this user")
        return {"owner_user_id": owner, "thread_id": thread, "session": session}

    def _lease(self, scope: dict, timeout: int) -> dict:
        digest = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        try:
            self.sessions.update_one({"_id": digest}, {"$setOnInsert": {
                **scope, "state": "ready", "context_id": None, "checkpoint": None,
                "checkpoint_names": [], "lease_token": None, "created_at": _now(),
            }}, upsert=True)
        except DuplicateKeyError:
            pass  # concurrent creator; unique key is authoritative
        token, now = uuid.uuid4().hex, _now()
        row = self.sessions.find_one_and_update({
            "_id": digest, **scope,
            "$or": [{"lease_token": None}, {"lease_until": {"$lt": now}}],
        }, {"$set": {"lease_token": token, "lease_until": now + timedelta(seconds=timeout + 30),
                     "state": "busy", "updated_at": now}}, return_document=ReturnDocument.BEFORE)
        if row is None:
            raise KernelError("KERNEL_BUSY", "session already has an active operation")
        # Expiry lets another process recover data, never attach to an uncertain
        # old execution or replay its code. Final commits are fenced by token.
        if row.get("lease_token") is not None:
            row["state"] = "needs_rebuild"
        return {**row, "lease_token": token}

    def _context(self, row: dict, proxy: SandboxBackendProxy) -> tuple[Any, str, list[str]]:
        identity = (proxy.id, proxy.generation)
        connected = False
        if row.get("context_id") and row.get("state") == "ready" and identity == (
            row.get("sandbox_id"), row.get("generation")
        ):
            try:
                context = proxy.kernel_get_context(row["context_id"])
                if context.id != row["context_id"] or context.language != "python":
                    raise KernelError("KERNEL_CONTEXT_MISMATCH", "context identity does not match registration")
                connected = True
            except SandboxApiException as failure:
                if failure.status_code != 404:
                    raise  # transport/auth/server errors are not context loss
        if connected:
            return context, "reconnected", []
        if row.get("context_id") and row.get("sandbox_id") == proxy.id:
            try:
                proxy.kernel_delete_context(row["context_id"])
            except SandboxApiException as failure:
                if failure.status_code != 404:
                    raise
        context = proxy.kernel_create_context()
        if not context.id or context.language != "python":
            raise KernelError("KERNEL_CONTEXT_MISSING", "SDK returned no context identity")
        checkpoint = row.get("checkpoint")
        warnings: list[str] = []
        if checkpoint is not None:
            snapshot = decode_snapshot(checkpoint.encode(), tuple(row["checkpoint_names"]))
            result = proxy.kernel_run(restore_source(snapshot), context,
                                      expected_identity=identity)
            if result.error is not None or result.complete is None:
                raise KernelError("KERNEL_RESTORE_FAILED", "JSON restore did not complete")
            warnings.extend(snapshot["unsupported"])
            recovery = "restored_json"
        else:
            recovery = "created_empty"
            if row.get("context_id"):
                warnings.append("all_uncheckpointed_state")
        return context, recovery, warnings

    def execute(self, owner: str, thread: str, code: str, *, session: str = "analysis",
                timeout: int = 60, checkpoint_names: tuple[str, ...] | None = None,
                cancel_signal: threading.Event | None = None, operation_id: str | None = None) -> dict:
        validate_code(code, timeout)
        if checkpoint_names is not None:
            checkpoint_names = validate_names(checkpoint_names)
        scope = self._scope(owner, thread, session)
        row = self._lease(scope, timeout)
        token = row["lease_token"]
        opid = operation_id or uuid.uuid4().hex
        selector = {"_id": row["_id"], **scope, "lease_token": token}
        operation_scope = {**scope, "operation_id": opid}
        preview = {"stdout": "", "stderr": "", "result": ""}
        output_bytes, sequence = 0, 0
        callback_failures: list[str] = []
        proxy = None
        state = "needs_rebuild"
        timer = None
        stopped = threading.Event()
        try:
            self.executions.insert_one({**operation_scope, "status": "running", "code": code,
                                        "cancel_requested": False, "created_at": _now()})
        except Exception:
            # No remote code has started. Preserve the previous context state
            # and release the lease even when durable registration fails.
            self.sessions.update_one(selector, {"$set": {
                "lease_token": None, "state": row["state"], "updated_at": _now(),
            }})
            raise

        def cancelled() -> bool:
            if cancel_signal is not None and cancel_signal.is_set():
                self.executions.update_one({**operation_scope, "status": "running"}, {"$set": {
                    "cancel_requested": True, "cancel_reason": "caller_cancelled",
                }})
            doc = self.executions.find_one(operation_scope)
            return doc is None or doc.get("cancel_requested", False)

        def output(kind: str, item: Any) -> None:
            nonlocal sequence, output_bytes
            text = item.text or ""
            # Preserve structured SDK events too; streamed text is only preview.
            try:
                encoded = text.encode()
                output_bytes += len(encoded)
                for offset in range(0, len(encoded), MAX_PREVIEW_BYTES):
                    self.outputs.insert_one({**operation_scope, "seq": sequence, "kind": kind,
                                             "content": encoded[offset:offset + MAX_PREVIEW_BYTES]})
                    sequence += 1
                event_bytes = item.model_dump_json().encode()
                for offset in range(0, len(event_bytes), MAX_PREVIEW_BYTES):
                    self.outputs.insert_one({**operation_scope, "seq": sequence, "kind": "event",
                                             "content": event_bytes[offset:offset + MAX_PREVIEW_BYTES]})
                    sequence += 1
                remaining = MAX_PREVIEW_BYTES - sum(len(v.encode()) for v in preview.values())
                if remaining > 0:
                    preview[kind] += encoded[:remaining].decode("utf-8", "ignore")
            except Exception as failure:
                callback_failures.append(type(failure).__name__)

        def initialized(event: Any) -> None:
            try:
                op = self.executions.find_one_and_update(operation_scope, {"$set": {
                    "execution_id": event.id,
                }}, return_document=ReturnDocument.AFTER)
                if op is None:
                    raise KernelError("KERNEL_RECORD_MISSING", "execution record vanished")
                if op.get("cancel_requested") or cancelled():
                    assert proxy is not None
                    proxy.kernel_interrupt(event.id, expected_sandbox_id=op["sandbox_id"])
            except Exception as failure:
                callback_failures.append(type(failure).__name__)

        def monitor_cancellation() -> None:
            # execd emits on_init before Jupyter has necessarily started code.
            # An interrupt acknowledged in that window can arrive too early.
            # Keep interrupting this registered operation until its SDK stream
            # returns; never release its session or start a successor meanwhile.
            while not stopped.wait(0.2):
                try:
                    op = self.executions.find_one({**operation_scope, "status": "running"})
                    if op is None:
                        return
                    if op.get("cancel_requested") or cancel_signal is not None and cancel_signal.is_set():
                        self.cancel(owner, thread, opid, reason=op.get("cancel_reason", "caller_cancelled"))
                except Exception:
                    # cancel() persists interrupt failures. Leave the operation
                    # running rather than claim remote termination.
                    continue

        try:
            timer = threading.Timer(timeout, lambda: self.cancel(owner, thread, opid, reason="timeout"))
            timer.daemon = True
            timer.start()
            threading.Thread(target=monitor_cancellation, daemon=True).start()
            if cancelled():
                raise KernelError("KERNEL_CANCELLED", "cancelled before sandbox allocation")
            proxy = self.backend_provider(owner)
            if proxy.owner_user_id != owner:
                raise KernelError("KERNEL_OWNER_MISMATCH", "backend belongs to a different user")
            if cancelled():
                raise KernelError("KERNEL_CANCELLED", "cancelled before context allocation")
            identity = (proxy.id, proxy.generation)
            context, recovery, rebuild = self._context(row, proxy)
            if (proxy.id, proxy.generation) != identity or proxy.is_replacing():
                raise KernelError("KERNEL_GENERATION_CHANGED", "context initialization raced with recovery")
            names = checkpoint_names if checkpoint_names is not None else tuple(row["checkpoint_names"])
            result_update = self.sessions.update_one(selector, {"$set": {
                "sandbox_id": identity[0], "generation": identity[1], "context_id": context.id,
            }})
            if result_update.matched_count != 1:
                raise KernelError("KERNEL_LEASE_LOST", "session operation was superseded")
            self.executions.update_one(operation_scope, {"$set": {
                "sandbox_id": identity[0], "generation": identity[1], "context_id": context.id,
            }})
            result = proxy.kernel_run(code, context, ExecutionHandlersSync(
                on_init=initialized,
                on_stdout=lambda item: output("stdout", item),
                on_stderr=lambda item: output("stderr", item),
                on_result=lambda item: output("result", item),
                skip_accumulation=True,
            ), should_cancel=cancelled, expected_identity=identity)
            if cancelled():
                raise KernelError("KERNEL_CANCELLED", "operation was cancelled; partial state is discarded")
            if callback_failures:
                raise KernelError("KERNEL_EVIDENCE_FAILED", "SDK callback persistence failed")
            if (proxy.id, proxy.generation) != identity or proxy.is_replacing():
                raise KernelError("KERNEL_GENERATION_CHANGED", "late result from replaced sandbox")
            if result.error is not None:
                self.outputs.insert_one({**operation_scope, "seq": sequence, "kind": "error",
                                         "content": result.error.model_dump_json().encode()})
                raise KernelError("KERNEL_CODE_ERROR", f"{result.error.name}: {result.error.value}")
            if result.complete is None:
                raise KernelError("KERNEL_INCOMPLETE", "SDK stream has no completion event")
            new_checkpoint = row.get("checkpoint")
            if names or checkpoint_names is not None:
                # Names are explicit; never send the complete working table to the
                # next model call. Only the reconstruction metadata is returned.
                path = "/workspace/scratch/kernel-checkpoint-" + opid + ".json"
                saved = proxy.kernel_run(snapshot_source(names, path), context,
                                         ExecutionHandlersSync(on_init=initialized),
                                         should_cancel=cancelled, expected_identity=identity)
                if saved.error is not None or saved.complete is None:
                    raise KernelError("KERNEL_CHECKPOINT_FAILED", "checkpoint export did not complete")
                downloaded = proxy.download_files([path])[0]
                if downloaded.error is not None or downloaded.content is None:
                    raise KernelError("KERNEL_CHECKPOINT_FAILED", "checkpoint file is unavailable")
                snapshot = decode_snapshot(downloaded.content, names)
                new_checkpoint = json.dumps(snapshot, ensure_ascii=True, allow_nan=False)
                rebuild = sorted(snapshot["unsupported"])
            if (proxy.id, proxy.generation) != identity or proxy.is_replacing():
                raise KernelError("KERNEL_GENERATION_CHANGED", "checkpoint raced with sandbox recovery")
            # Claim commit only while cancel is false. Never publish completed
            # before the checkpoint is durable; Mongo may be standalone, so
            # do not depend on cross-collection transactions.
            finished = self.executions.update_one({**operation_scope, "status": "running",
                                                   "cancel_requested": False}, {"$set": {
                "status": "committing", "preview": preview,
                "output_bytes": output_bytes, "recovery": recovery,
            }})
            if finished.matched_count != 1:
                raise KernelError("KERNEL_CANCELLED", "late success rejected")
            persisted = self.sessions.update_one(selector, {"$set": {
                "checkpoint": new_checkpoint, "checkpoint_names": list(names),
                "rebuild_required": rebuild, "last_operation_id": opid,
                "state": "ready", "lease_token": None, "updated_at": _now(),
            }})
            if persisted.matched_count != 1:
                raise KernelError("KERNEL_LEASE_LOST", "checkpoint commit was superseded")
            self.executions.update_one({**operation_scope, "status": "committing"}, {"$set": {
                "status": "completed", "finished_at": _now(),
            }})
            state = "ready"
            return {"operation_id": opid, "status": "completed", **preview,
                    "truncated": output_bytes > sum(len(v.encode()) for v in preview.values()),
                    "recovery": recovery, "rebuild_required": rebuild, "context_id": context.id}
        except Exception as failure:
            was_cancelled = cancelled()
            status = "cancelled" if was_cancelled else "failed"
            code_name = "KERNEL_CANCELLED" if was_cancelled else getattr(failure, "code", "KERNEL_RUNTIME_ERROR")
            self.executions.update_one(operation_scope, {"$set": {
                "status": status, "error_code": code_name, "error": str(failure)[:8000],
                "preview": preview, "finished_at": _now(),
            }})
            # Code failures also recover from the last successful checkpoint:
            # a failed snippet may already have mutated live objects.
            return {"operation_id": opid, "status": status, "error_code": code_name,
                    "message": str(failure)[:8000], **preview}
        finally:
            stopped.set()
            if timer is not None:
                timer.cancel()
            self.sessions.update_one(selector, {"$set": {
                "lease_token": None, "state": state, "updated_at": _now(),
            }})

    def checkpoint(self, owner: str, thread: str, names: tuple[str, ...], *, session="analysis") -> dict:
        return self.execute(owner, thread, "pass", session=session, checkpoint_names=names)

    def cancel(self, owner: str, thread: str, operation_id: str, *, reason="user") -> bool:
        self._scope(owner, thread, "analysis")  # thread ownership, not default context
        selector = {"owner_user_id": owner, "thread_id": thread, "operation_id": operation_id}
        operation = self.executions.find_one_and_update({**selector, "status": "running"}, {"$set": {
            "cancel_requested": True, "cancel_reason": reason,
        }}, return_document=ReturnDocument.AFTER)
        if operation is None:
            return False
        if operation.get("execution_id"):
            proxy = self.backend_provider(owner)
            try:
                proxy.kernel_interrupt(operation["execution_id"],
                                       expected_sandbox_id=operation["sandbox_id"])
            except Exception as failure:
                # Failure remains visible; cancellation is not proof of remote stop.
                self.executions.update_one(selector, {"$set": {
                    "interrupt_error": type(failure).__name__,
                }})
                raise
        return True

    def status(self, owner: str, thread: str, *, session="analysis") -> dict:
        scope = self._scope(owner, thread, session)
        row = self.sessions.find_one(scope)
        if row is None:
            return {"state": "absent"}
        return {key: row.get(key) for key in (
            "state", "context_id", "sandbox_id", "generation", "checkpoint_names",
            "rebuild_required", "last_operation_id",
        )}

    def read_output(self, owner: str, thread: str, operation_id: str, *, kind="stdout", offset=0) -> dict:
        self._scope(owner, thread, "analysis")
        if kind not in {"stdout", "stderr", "result", "error", "event"} or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise KernelError("INVALID_OUTPUT_QUERY", "invalid output kind or byte offset")
        query = {"owner_user_id": owner, "thread_id": thread, "operation_id": operation_id}
        if self.executions.find_one(query) is None:
            raise KernelError("KERNEL_OPERATION_NOT_FOUND", "operation is not in this thread")
        content, skipped, remaining = bytearray(), 0, MAX_PREVIEW_BYTES
        for chunk in self.outputs.find({**query, "kind": kind}).sort("seq", 1):
            raw = bytes(chunk["content"])
            start = max(0, offset - skipped)
            skipped += len(raw)
            if start < len(raw):
                selected = raw[start:start + remaining]
                content.extend(selected)
                remaining -= len(selected)
                if remaining == 0:
                    break
        return {"content": bytes(content).decode("utf-8", "replace"), "next_offset": offset + len(content)}

    def close(self, owner: str, thread: str, *, session="analysis") -> None:
        scope = self._scope(owner, thread, session)
        row = self._lease(scope, 60)
        try:
            proxy = self.backend_provider(owner)
            if row.get("context_id") and proxy.id == row.get("sandbox_id"):
                try:
                    proxy.kernel_delete_context(row["context_id"])
                except SandboxApiException as failure:
                    if failure.status_code != 404:
                        raise
            self.sessions.update_one({**scope, "lease_token": row["lease_token"]},
                                     {"$set": {"context_id": None, "state": "ready"}})
        finally:
            self.sessions.update_one({**scope, "lease_token": row["lease_token"]},
                                     {"$set": {"lease_token": None}})
