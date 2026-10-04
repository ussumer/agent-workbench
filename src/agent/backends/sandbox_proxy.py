"""A stable, per-user handle over a replaceable sandbox backend.

The graph, the middleware and the tools all hold *this* object. When the container
behind it dies, the pool swaps in a new backend with :meth:`SandboxBackendProxy.replace_backend`
and every existing reference keeps working — the Python object identity never
changes, so nothing has to be re-wired mid-run.

Two properties make the swap safe:

* **Explicit delegation for the whole protocol.** All twenty methods (ten sync plus
  their async counterparts) are declared here and forwarded. There is deliberately
  no ``__getattr__`` passthrough: a method the sandbox does not support must fail
  loudly rather than be resolved by an attribute lookup that happens to work.
* **In-flight calls fail instead of silently continuing.** While the backend is
  being replaced, calls raise :class:`SandboxReplacedError`. Nothing is replayed
  automatically: a shell command may have had side effects, so re-running it is the
  caller's decision, not ours.

A per-owner execution lock serialises shell commands, because two agents sharing
one container must not interleave stateful commands. Reads and writes stay
parallel.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Sequence
from typing import Any

import httpx
from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from opensandbox.exceptions import SandboxApiException, SandboxException

from agent.backends.custom_opensandbox import SandboxExecutionError, collect_sandbox_failures
from agent.middlewares.sandbox_breaker import SandboxBreaker

LOGGER = logging.getLogger("rush_harness.sandbox.proxy")


class SandboxCircuitOpenError(RuntimeError):
    """Cooldown/probe refusal; never evidence that a container needs rebuilding."""

    def __init__(self, owner: str) -> None:
        super().__init__(f"SANDBOX_CIRCUIT_OPEN: 用户 {owner} 的沙箱调用已暂停，请等待冷却后探测。")
        self.code = "SANDBOX_CIRCUIT_OPEN"


class SandboxReplacedError(RuntimeError):
    """A call raced with a backend replacement and must be retried by the caller."""

    def __init__(self, owner_user_id: str, generation: int) -> None:
        super().__init__(
            f"sandbox for {owner_user_id} (generation {generation}) is being replaced; "
            "the command was not replayed because it may have had side effects"
        )
        self.owner_user_id = owner_user_id
        self.generation = generation


class SandboxBackendProxy(SandboxBackendProtocol):
    """Stable handle whose underlying backend can be swapped without re-wiring."""

    def __init__(
        self,
        backend: Any,
        *,
        owner_user_id: str,
        generation: int = 1,
        execution_lock: threading.Lock | None = None,
        breaker: SandboxBreaker | None = None,
    ) -> None:
        self._backend = backend
        self.breaker = breaker or SandboxBreaker()
        self._owner_user_id = owner_user_id
        self._generation = generation
        # Shared by every proxy of the same owner, so two threads cannot interleave
        # stateful shell commands inside one container.
        self._execution_lock = execution_lock or threading.Lock()
        self._state_lock = threading.Lock()
        self._replacing = False
        self._computation_quarantined = False
        self._replacements = 0

    # ------------------------------------------------------------- handle state

    @property
    def owner_user_id(self) -> str:
        return self._owner_user_id

    @property
    def generation(self) -> int:
        """Increments on every successful replacement; observable by callers."""
        return self._generation

    @property
    def replacement_count(self) -> int:
        return self._replacements

    @property
    def id(self) -> str:
        """Sandbox id of the *current* generation."""
        with self._state_lock:
            return str(self._backend.id)

    @property
    def backend(self) -> Any:
        with self._state_lock:
            return self._backend

    def is_replacing(self) -> bool:
        return self._replacing

    def begin_replacement(self) -> None:
        """Block new calls while a new generation is being prepared."""
        with self._state_lock:
            self._replacing = True

    def abort_replacement(self) -> None:
        """Give up on a replacement, leaving the current (broken) generation in place.

        Without this a failed recovery would keep the proxy in the "being replaced"
        state forever, turning every later call into a confusing retry verdict.
        """
        with self._state_lock:
            self._replacing = self._computation_quarantined
        LOGGER.warning("proxy owner=%s replacement aborted", self._owner_user_id)

    def replace_backend(self, backend: Any) -> int:
        """Publish a fully-initialised backend. Returns the new generation."""
        with self._state_lock:
            previous = self._backend
            self._backend = backend
            self._generation += 1
            self._replacements += 1
            self._replacing = False
            self._computation_quarantined = False
            generation = self._generation
        LOGGER.info(
            "proxy owner=%s generation=%s replaced old=%s new=%s",
            self._owner_user_id,
            generation,
            getattr(previous, "id", "<unknown>"),
            getattr(backend, "id", "<unknown>"),
        )
        return generation

    def _current(self) -> Any:
        with self._state_lock:
            if self._replacing:
                raise SandboxReplacedError(self._owner_user_id, self._generation)
            return self._backend

    # ------------------------------------------------------------------ execute

    def _invoke(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        epoch = self.breaker.admit()
        if epoch is None:
            raise SandboxCircuitOpenError(self._owner_user_id)
        with collect_sandbox_failures() as failures:
            try:
                result = getattr(self._current(), operation)(*args, **kwargs)
            except Exception as failure:
                if failures:
                    self.breaker.record_failure(failures[0], expected_opened_count=epoch)
                elif isinstance(failure, (SandboxExecutionError, httpx.HTTPError, OSError)):
                    self.breaker.record_failure("TRANSPORT_ERROR", expected_opened_count=epoch)
                elif isinstance(failure, SandboxException) and (
                    failure.error.code in {"TIMEOUT", "CONNECTION", "UNHEALTHY", "READY_TIMEOUT"}
                    or isinstance(failure, SandboxApiException) and (
                        failure.status_code is not None and failure.status_code >= 500
                    )
                ):
                    self.breaker.record_failure("TRANSPORT_ERROR", expected_opened_count=epoch)
                else:
                    self.breaker.record_ignored(expected_opened_count=epoch)
                raise
            if failures:
                self.breaker.record_failure(failures[0], expected_opened_count=epoch)
            elif isinstance(result, ExecuteResponse) and result.exit_code in (124, 125):
                self.breaker.record_failure("EXEC_TIMEOUT" if result.exit_code == 124 else "CONTAINER_GONE", expected_opened_count=epoch)
            else:
                values = result if isinstance(result, list) else [result]
                if any(getattr(value, "error", None) for value in values):
                    self.breaker.record_ignored(expected_opened_count=epoch)
                else:
                    self.breaker.record_success(expected_opened_count=epoch)
            return result

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        with self._execution_lock:
            return self._invoke("execute", command, timeout=timeout)

    def computation_run(self, command: str, *, timeout: float, cancel_path: str,
                        should_cancel, on_started, expected_identity: tuple[str, int]) -> dict:
        with self._execution_lock:
            if should_cancel():
                raise TimeoutError("COMPUTATION_CANCELLED_BEFORE_START")
            if (self.id, self.generation) != expected_identity:
                raise SandboxReplacedError(self.owner_user_id, self.generation)
            return self._invoke("computation_run", command, timeout=timeout,
                                cancel_path=cancel_path, should_cancel=should_cancel,
                                on_started=on_started)

    def computation_cancel(self, cancel_path: str, *, expected_identity: tuple[str, int]) -> None:
        # Control plane must bypass the execution queue and an open breaker.
        with self._state_lock:
            if (str(self._backend.id), self._generation) != expected_identity:
                raise SandboxReplacedError(self.owner_user_id, self.generation)
            backend = self._backend
        responses = backend.upload_files([(cancel_path, b"cancel")])
        if any(response.error for response in responses):
            raise SandboxExecutionError("cancellation marker could not be delivered")

    def computation_wait(self, execution_id: str, *, expected_identity: tuple[str, int]) -> dict:
        with self._state_lock:
            if (str(self._backend.id), self._generation) != expected_identity:
                raise SandboxReplacedError(self.owner_user_id, self.generation)
            backend = self._backend
        return backend.computation_wait(execution_id)

    def quarantine_computation(self, *, expected_identity: tuple[str, int]) -> None:
        with self._state_lock:
            if (str(self._backend.id), self._generation) == expected_identity:
                self._replacing = True
                self._computation_quarantined = True

    def computation_publish(self, action, *, expected_identity: tuple[str, int]):
        # Hold the generation fence through publication; replacement must not
        # race between the identity check and the authoritative Mongo CAS.
        with self._state_lock:
            if self._replacing or (str(self._backend.id), self._generation) != expected_identity:
                raise SandboxReplacedError(self.owner_user_id, self.generation)
            return action()

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        # Delegated through the sync entry point so the per-owner execution lock is
        # held by the worker thread that actually runs the command.
        return await asyncio.to_thread(self.execute, command, timeout=timeout)

    def kernel_create_context(self):
        with self._execution_lock:
            return self._invoke("kernel_create_context")

    def kernel_get_context(self, context_id: str):
        return self._invoke("kernel_get_context", context_id)

    def kernel_run(self, code: str, context, handlers=None, *, should_cancel=None,
                   expected_identity: tuple[str, int] | None = None):
        with self._execution_lock:
            if should_cancel is not None and should_cancel():
                raise TimeoutError("KERNEL_CANCELLED_BEFORE_START")
            if expected_identity is not None and (self.id, self.generation) != expected_identity:
                raise SandboxReplacedError(self.owner_user_id, self.generation)
            return self._invoke("kernel_run", code, context, handlers, should_cancel=should_cancel)

    def kernel_interrupt(self, execution_id: str, *, expected_sandbox_id: str) -> None:
        if self.id != expected_sandbox_id:
            raise SandboxReplacedError(self.owner_user_id, self.generation)
        # Control traffic must still stop code while an execution holds the queue
        # or the breaker is open. It is not evidence that the data plane is healthy.
        self._current().kernel_interrupt(execution_id)

    def kernel_delete_context(self, context_id: str) -> None:
        with self._execution_lock:
            return self._invoke("kernel_delete_context", context_id)

    # --------------------------------------------------------------------- ls

    def ls(self, path: str) -> LsResult:
        return self._invoke("ls", path)

    async def als(self, path: str) -> LsResult:
        return await asyncio.to_thread(self.ls, path)

    # ------------------------------------------------------------------- read

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._invoke("read", file_path, offset, limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await asyncio.to_thread(self.read, file_path, offset, limit)

    # ------------------------------------------------------------------ write

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._invoke("write", file_path, content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await asyncio.to_thread(self.write, file_path, content)

    # ------------------------------------------------------------------- edit

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002 - protocol signature
    ) -> EditResult:
        return self._invoke("edit", file_path, old_string, new_string, replace_all)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002 - protocol signature
    ) -> EditResult:
        return await asyncio.to_thread(self.edit, file_path, old_string, new_string, replace_all)

    # ------------------------------------------------------------------- glob

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._invoke("glob", pattern, path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await asyncio.to_thread(self.glob, pattern, path)

    # ------------------------------------------------------------------- grep

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return self._invoke("grep", pattern, path, glob, max_count=max_count)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return await asyncio.to_thread(self.grep, pattern, path, glob, max_count=max_count)

    # ----------------------------------------------------------------- delete

    def delete(self, file_path: str) -> DeleteResult:
        return self._invoke("delete", file_path)

    async def adelete(self, file_path: str) -> DeleteResult:
        return await asyncio.to_thread(self.delete, file_path)

    # ---------------------------------------------------------------- transfer

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._invoke("upload_files", files)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await asyncio.to_thread(self.upload_files, files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._invoke("download_files", paths)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await asyncio.to_thread(self.download_files, paths)

    # ----------------------------------------------------- backend-specific extra

    def stat(self, paths: Sequence[str]) -> dict[str, Any]:
        return self._invoke("stat", paths)

    def workspace_report(self) -> Any:
        backend = self._current()
        return getattr(backend, "workspace_report", None)
