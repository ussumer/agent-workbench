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

LOGGER = logging.getLogger("rush_harness.sandbox.proxy")


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
    ) -> None:
        self._backend = backend
        self._owner_user_id = owner_user_id
        self._generation = generation
        # Shared by every proxy of the same owner, so two threads cannot interleave
        # stateful shell commands inside one container.
        self._execution_lock = execution_lock or threading.Lock()
        self._state_lock = threading.Lock()
        self._replacing = False
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
            self._replacing = False
        LOGGER.warning("proxy owner=%s replacement aborted", self._owner_user_id)

    def replace_backend(self, backend: Any) -> int:
        """Publish a fully-initialised backend. Returns the new generation."""
        with self._state_lock:
            previous = self._backend
            self._backend = backend
            self._generation += 1
            self._replacements += 1
            self._replacing = False
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

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        backend = self._current()
        with self._execution_lock:
            return backend.execute(command, timeout=timeout)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        # Delegated through the sync entry point so the per-owner execution lock is
        # held by the worker thread that actually runs the command.
        return await asyncio.to_thread(self.execute, command, timeout=timeout)

    # --------------------------------------------------------------------- ls

    def ls(self, path: str) -> LsResult:
        return self._current().ls(path)

    async def als(self, path: str) -> LsResult:
        return await asyncio.to_thread(self.ls, path)

    # ------------------------------------------------------------------- read

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._current().read(file_path, offset, limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await asyncio.to_thread(self.read, file_path, offset, limit)

    # ------------------------------------------------------------------ write

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._current().write(file_path, content)

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
        return self._current().edit(file_path, old_string, new_string, replace_all)

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
        return self._current().glob(pattern, path)

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
        return self._current().grep(pattern, path, glob, max_count=max_count)

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
        return self._current().delete(file_path)

    async def adelete(self, file_path: str) -> DeleteResult:
        return await asyncio.to_thread(self.delete, file_path)

    # ---------------------------------------------------------------- transfer

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._current().upload_files(files)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await asyncio.to_thread(self.upload_files, files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._current().download_files(paths)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await asyncio.to_thread(self.download_files, paths)

    # ----------------------------------------------------- backend-specific extra

    def stat(self, paths: Sequence[str]) -> dict[str, Any]:
        return self._current().stat(paths)

    def workspace_report(self) -> Any:
        backend = self._current()
        return getattr(backend, "workspace_report", None)
