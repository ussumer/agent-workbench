"""DeepAgents sandbox backend backed by a real OpenSandbox container.

``BaseSandbox`` already implements the composed protocol methods (``ls``, ``read``,
``write``, ``edit``, ``glob``, ``grep``) on top of four primitives and their async
counterparts. This module supplies those primitives explicitly and natively:

* :meth:`OpenSandboxBackend.execute` / ``aexecute`` → ``CommandsSync.run``
* :meth:`upload_files` / ``download_files`` (+ async) → the VFS service
* :attr:`id`

Nothing here is resolved through ``__getattr__``: every entry point the gateway
calls is declared on the class, so a removed or renamed SDK method fails loudly at
import/call time instead of silently degrading.

Failure policy: timeouts and I/O errors become *structured* results (a non-zero
exit code, or a per-path error) and are logged. They are never converted into an
empty success — a caller must be able to tell "the command failed" from "the
command succeeded and printed nothing".
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta

from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    INVALID_PATH,
    IS_DIRECTORY,
    PERMISSION_DENIED,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from opensandbox.models.execd import RunCommandOpts
from opensandbox.models.filesystem import WriteEntry
from opensandbox.sync import SandboxSync

from agent.backends.sandbox_setup import SandboxRuntimeConfig, WorkspaceReport

LOGGER = logging.getLogger("rush_harness.sandbox")

_CALL_FAILURES: ContextVar[list[str] | None] = ContextVar("sandbox_call_failures", default=None)


@contextmanager
def collect_sandbox_failures() -> Iterator[list[str]]:
    """Invocation-local transport evidence, including composed filesystem operations."""
    failures: list[str] = []
    token = _CALL_FAILURES.set(failures)
    try:
        yield failures
    finally:
        _CALL_FAILURES.reset(token)


def _report_failure(code: str) -> None:
    failures = _CALL_FAILURES.get()
    if failures is not None:
        failures.append(code)


def _report_file_failure(failure: BaseException) -> None:
    status = _status_code(failure)
    business_error = status in (400, 401, 403, 404, 409, 422)
    if status is None:
        business_error = _error_code(failure) in (FILE_NOT_FOUND, PERMISSION_DENIED, INVALID_PATH, IS_DIRECTORY)
    if not business_error:
        # Evidence comes from a failed SDK operation, before mapping into file errors.
        _report_failure("TRANSPORT_ERROR")

#: Exit code for a command that the backend aborted because it exceeded its budget.
TIMEOUT_EXIT_CODE = 124

#: Exit code for a command terminated for some other reason (for example OOM).
ABORTED_EXIT_CODE = 125

#: A killed command is attributed to a timeout when it consumed at least this much
#: of the budget it was given. The SDK reports both timeouts and external kills as
#: ``exit_code=-1``, so elapsed time is what separates them.
_TIMEOUT_ATTRIBUTION_RATIO = 0.8


class SandboxExecutionError(RuntimeError):
    """The sandbox could not run the command at all (transport or daemon failure)."""


def _stdout_text(execution: object) -> str:
    logs = getattr(execution, "logs", None)
    chunks = getattr(logs, "stdout", None) or []
    return "\n".join(str(getattr(chunk, "text", "")) for chunk in chunks)


def _stderr_text(execution: object) -> str:
    logs = getattr(execution, "logs", None)
    chunks = getattr(logs, "stderr", None) or []
    return "\n".join(str(getattr(chunk, "text", "")) for chunk in chunks)


def _combined_output(execution: object) -> str:
    """stdout and stderr in one string, which is what the protocol expects."""
    stdout = _stdout_text(execution)
    stderr = _stderr_text(execution)
    if stdout and stderr:
        return f"{stdout}\n{stderr}"
    return stdout or stderr


def _error_text(execution: object) -> str:
    """Render the SDK's structured execution error, if any.

    Without this a terminated command surfaces as an empty output with a negative
    exit code, which a caller cannot distinguish from "the command printed nothing".
    """
    error = getattr(execution, "error", None)
    if error is None:
        return ""
    name = getattr(error, "name", "") or "CommandExecError"
    value = getattr(error, "value", "")
    traceback = getattr(error, "traceback", None) or []
    detail = "; ".join(str(item) for item in traceback) if traceback else str(value)
    return f"[{name}] exit={value} {detail}".strip()


def _was_terminated(execution: object) -> bool:
    """Whether the execution was killed rather than exiting on its own."""
    code = getattr(execution, "exit_code", None)
    if isinstance(code, int) and code < 0:
        return True
    text = f"{_error_text(execution)}".lower()
    return "signal: killed" in text or "killed" in text


def _validate_path(path: str) -> str | None:
    """Return an error code when a path is unusable, else ``None``.

    Only structurally invalid paths are rejected here. Containment is provided by
    the container itself — verified by the acceptance suite, which checks that host
    files and the Docker socket are unreachable — not by string filtering, which
    would give a false sense of safety.
    """
    if not path or not path.strip():
        return INVALID_PATH
    if "\x00" in path:
        return INVALID_PATH
    if not path.startswith("/"):
        return INVALID_PATH
    return None


class OpenSandboxBackend(BaseSandbox):
    """A DeepAgents backend whose files live inside one OpenSandbox container."""

    #: Large `execute` output is captured at source by the middleware.
    enable_capture_offload = True

    def __init__(
        self,
        sandbox: SandboxSync,
        *,
        config: SandboxRuntimeConfig,
        default_timeout: int | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._config = config
        self._default_timeout = default_timeout or config.default_exec_timeout
        self._code_service = None
        self._code_service_lock = threading.Lock()

    # ------------------------------------------------------------------ identity

    @property
    def id(self) -> str:
        """Stable sandbox identifier, reported back to the agent."""
        return str(self._sandbox.id)

    @property
    def sandbox(self) -> SandboxSync:
        """The underlying client, for workspace preparation and diagnostics."""
        return self._sandbox

    @property
    def config(self) -> SandboxRuntimeConfig:
        return self._config

    # Official persistent code service over this same container. The proxy owns
    # execution serialization; interruption deliberately uses a separate request.
    def _codes(self):
        from code_interpreter.sync.adapters.factory import AdapterFactorySync
        from opensandbox.constants import DEFAULT_EXECD_PORT
        from opensandbox.transport import unwrap_retry_transport

        with self._code_service_lock:
            if self._code_service is None:
                config = self._sandbox.connection_config
                config = config.model_copy(update={
                    "transport": unwrap_retry_transport(config.transport),
                })
                self._code_service = AdapterFactorySync(config).create_code_execution_service(
                    self._sandbox.get_endpoint(DEFAULT_EXECD_PORT))
            return self._code_service

    def kernel_create_context(self):
        return self._codes().create_context("python")

    def kernel_get_context(self, context_id: str):
        return self._codes().get_context(context_id)

    def kernel_run(self, code: str, context, handlers=None, *, should_cancel=None):
        if not context.id or context.language != "python":
            raise ValueError("explicit Python context identity is required")
        if should_cancel is not None and should_cancel():
            raise TimeoutError("KERNEL_CANCELLED_BEFORE_START")
        return self._codes().run(code, context=context, handlers=handlers)

    def kernel_interrupt(self, execution_id: str) -> None:
        self._codes().interrupt(execution_id)

    def kernel_delete_context(self, context_id: str) -> None:
        self._codes().delete_context(context_id)

    # ------------------------------------------------------------------ execute

    def computation_run(self, command: str, *, timeout: float, cancel_path: str,
                        should_cancel, on_started) -> dict:
        """Launch one supervised process with the official background Commands API.

        Cancellation stops the supervised children, not just our HTTP wait. A
        missing/uncertain terminal status is handled as quarantine by the caller.
        """
        started = time.monotonic()
        execution = self._sandbox.commands.run(command, opts=RunCommandOpts(
            background=True, timeout=timedelta(seconds=timeout + 15)))
        if not execution.id or execution.error is not None:
            raise SandboxExecutionError("computation supervisor did not start")
        on_started(execution.id)
        signalled = False
        while time.monotonic() - started < timeout + 12:
            if should_cancel() and not signalled:
                self._sandbox.files.write_files([WriteEntry(path=cancel_path, data=b"cancel")])
                signalled = True
            status = self._sandbox.commands.get_command_status(execution.id)
            if status.running is False:
                return {"execution_id": execution.id, "exit_code": status.exit_code}
            time.sleep(0.05)
        # Killing only the command is not proof its children ended. The caller
        # quarantines this container rather than accepting partial cleanup.
        self._sandbox.commands.interrupt(execution.id)
        raise SandboxExecutionError("supervisor termination was not confirmed")

    def computation_wait(self, execution_id: str, *, timeout: int = 20) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._sandbox.commands.get_command_status(execution_id)
            if status.running is False:
                return {"execution_id": execution_id, "exit_code": status.exit_code}
            time.sleep(0.05)
        raise SandboxExecutionError("supervisor did not terminate during recovery")

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Run a shell command inside the container."""
        effective = timeout or self._default_timeout
        LOGGER.info("sandbox=%s execute timeout=%ss command=%s", self.id, effective, command[:200])
        started = time.monotonic()
        try:
            execution = self._sandbox.commands.run(
                command,
                opts=RunCommandOpts(timeout=timedelta(seconds=effective)),
            )
        except TimeoutError as failure:
            _report_failure("EXEC_TIMEOUT")
            LOGGER.warning("sandbox=%s execute timed out after %ss", self.id, effective)
            return ExecuteResponse(
                output=f"command timed out after {effective}s: {failure}",
                exit_code=TIMEOUT_EXIT_CODE,
                truncated=False,
            )
        except Exception as failure:  # noqa: BLE001 - classified, then either mapped or re-raised
            status = _status_code(failure)
            if status in (408, 504):
                _report_failure("EXEC_TIMEOUT")
                LOGGER.warning("sandbox=%s execute reported timeout status=%s", self.id, status)
                return ExecuteResponse(
                    output=f"command timed out after {effective}s (upstream status {status})",
                    exit_code=TIMEOUT_EXIT_CODE,
                    truncated=False,
                )
            _report_failure("TRANSPORT_ERROR")
            raise SandboxExecutionError(
                f"sandbox {self.id} could not run the command: {type(failure).__name__}: {failure}"
            ) from failure

        elapsed = time.monotonic() - started
        output = _combined_output(execution)
        error_text = _error_text(execution)
        exit_code = getattr(execution, "exit_code", None)

        if _was_terminated(execution):
            # The SDK reports a signal-killed command as exit_code=-1 with an empty
            # output. A command that ran out its own budget was a timeout; anything
            # else was terminated for another reason (for example OOM) and keeps a
            # distinct code so the two are not conflated.
            timed_out = elapsed >= effective * _TIMEOUT_ATTRIBUTION_RATIO
            exit_code = TIMEOUT_EXIT_CODE if timed_out else ABORTED_EXIT_CODE
            _report_failure("EXEC_TIMEOUT" if timed_out else "CONTAINER_GONE")
            verdict = "timed out" if timed_out else "terminated"
            detail = f"command {verdict} after {elapsed:.1f}s (budget {effective}s)"
            output = f"{output}\n{detail}".strip() if output else detail

        if error_text and error_text not in output:
            output = f"{output}\n{error_text}".strip()

        response = ExecuteResponse(output=output, exit_code=exit_code, truncated=False)
        LOGGER.info(
            "sandbox=%s execute exit_code=%s elapsed=%.1fs", self.id, response.exit_code, elapsed
        )
        return response

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Async entry point, off-loading the blocking SDK call to a worker thread."""
        return await asyncio.to_thread(self.execute, command, timeout=timeout)

    # ----------------------------------------------------------- file transfer

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Write files into the sandbox, reporting per-path outcomes."""
        responses: list[FileUploadResponse] = []
        for path, content in files:
            invalid = _validate_path(path)
            if invalid is not None:
                responses.append(FileUploadResponse(path=path, error=invalid))
                continue
            try:
                self._sandbox.files.write_files([WriteEntry(path=path, data=content)])
            except Exception as failure:  # noqa: BLE001 - mapped to a per-path error
                _report_file_failure(failure)
                responses.append(FileUploadResponse(path=path, error=_error_code(failure)))
                LOGGER.warning("sandbox=%s upload failed path=%s err=%s", self.id, path, failure)
                continue
            responses.append(FileUploadResponse(path=path, error=None))
        return responses

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await asyncio.to_thread(self.upload_files, files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Read files out of the sandbox, reporting per-path outcomes."""
        responses: list[FileDownloadResponse] = []
        for path in paths:
            invalid = _validate_path(path)
            if invalid is not None:
                responses.append(FileDownloadResponse(path=path, content=None, error=invalid))
                continue
            try:
                content = self._sandbox.files.read_bytes(path)
            except Exception as failure:  # noqa: BLE001 - mapped to a per-path error
                _report_file_failure(failure)
                code = _error_code(failure)
                responses.append(FileDownloadResponse(path=path, content=None, error=code))
                LOGGER.warning(
                    "sandbox=%s download failed path=%s code=%s", self.id, path, code
                )
                continue
            responses.append(FileDownloadResponse(path=path, content=content, error=None))
        return responses

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await asyncio.to_thread(self.download_files, paths)

    # ------------------------------------------------------------------ deletion

    def delete(self, file_path: str):
        """Remove one file, returning a structured result."""
        from deepagents.backends.protocol import DeleteResult

        invalid = _validate_path(file_path)
        if invalid is not None:
            return DeleteResult(path=file_path, error=invalid)
        try:
            info = self._sandbox.files.get_file_info([file_path]).get(file_path)
        except Exception as failure:  # noqa: BLE001 - retain SDK exception and fault evidence
            _report_file_failure(failure)
            raise
        if info is None:
            return DeleteResult(path=file_path, error=FILE_NOT_FOUND)
        if getattr(info, "entry_type", None) == "directory":
            return DeleteResult(path=file_path, error=IS_DIRECTORY)
        try:
            self._sandbox.files.delete_files([file_path])
        except Exception as failure:  # noqa: BLE001
            _report_file_failure(failure)
            return DeleteResult(path=file_path, error=_error_code(failure))
        return DeleteResult(path=file_path, error=None)

    async def adelete(self, file_path: str):
        return await asyncio.to_thread(self.delete, file_path)

    # -------------------------------------------------------------- inspection

    def stat(self, paths: Iterable[str]) -> dict[str, object]:
        """Batch metadata lookup, used by tests and workspace checks."""
        try:
            return {path: entry for path, entry in self._sandbox.files.get_file_info(list(paths)).items()}
        except Exception as failure:  # noqa: BLE001 - retain SDK exception and fault evidence
            _report_file_failure(failure)
            raise

    def search(self, path: str, pattern: str) -> list[object]:
        """Glob-style search delegated to the sandbox's own filesystem service."""
        from opensandbox.models.filesystem import SearchEntry

        return list(self._sandbox.files.search(SearchEntry(path=path, pattern=pattern)))

    # ------------------------------------------------------------------ lifecycle

    def renew(self, seconds: int) -> None:
        """Extend the sandbox lifetime before a long step."""
        self._sandbox.renew(timeout=timedelta(seconds=seconds))  # type: ignore[call-arg]

    def close(self) -> None:
        """Destroy the sandbox and its container."""
        LOGGER.info("sandbox=%s closing", self.id if self._sandbox is not None else "<unknown>")
        self._sandbox.kill()

    def __enter__(self) -> OpenSandboxBackend:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _status_code(failure: BaseException) -> int | None:
    for attribute in ("status_code", "status", "code"):
        value = getattr(failure, attribute, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    response = getattr(failure, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def _error_code(failure: BaseException) -> str:
    """Map an SDK failure onto the protocol's error vocabulary.

    Unknown failures keep a readable message rather than being flattened into
    "file_not_found", which would misreport what happened.
    """
    status = _status_code(failure)
    if status == 404:
        return FILE_NOT_FOUND
    if status in (401, 403):
        return PERMISSION_DENIED
    if status == 400:
        return INVALID_PATH
    if status == 409:
        return IS_DIRECTORY
    text = f"{type(failure).__name__}: {failure}".lower()
    if "no such file" in text or "not found" in text:
        return FILE_NOT_FOUND
    if "is a directory" in text:
        return IS_DIRECTORY
    if "permission denied" in text:
        return PERMISSION_DENIED
    return f"{type(failure).__name__}: {failure}"


def create_backend(
    config: SandboxRuntimeConfig | None = None,
    *,
    env: dict[str, str] | None = None,
    metadata: dict[str, str] | None = None,
    prepare: bool = True,
) -> tuple[OpenSandboxBackend, WorkspaceReport | None]:
    """Create a sandbox and wrap it in the backend.

    Returns the backend plus the workspace report, so callers can log exactly what
    image and Python version the execution actually used.
    """
    from agent.backends.sandbox_setup import prepare_workspace

    resolved = config or SandboxRuntimeConfig.from_env(env)
    sandbox = SandboxSync.create(
        resolved.image,
        # The SDK defaults to `tail -f /dev/null`, overriding this image's
        # entrypoint and leaving execd's persistent context service without
        # Jupyter. Start the same pinned image's actual runtime instead.
        entrypoint=["/opt/code-interpreter/code-interpreter.sh"],
        timeout=resolved.create_timeout,
        ready_timeout=resolved.ready_timeout,
        env=resolved.extra_env or None,
        metadata=metadata,
        resource_requests=resolved.resource_requests(),
        connection_config=resolved.connection_config(),
    )
    backend = OpenSandboxBackend(sandbox, config=resolved)
    report = prepare_workspace(sandbox, resolved) if prepare else None
    return backend, report


def execute_sequence(backend: OpenSandboxBackend, commands: Sequence[str]) -> list[ExecuteResponse]:
    """Run several commands in order, stopping at the first non-zero exit."""
    results: list[ExecuteResponse] = []
    for command in commands:
        response = backend.execute(command)
        results.append(response)
        if response.exit_code not in (0, None):
            break
    return results
