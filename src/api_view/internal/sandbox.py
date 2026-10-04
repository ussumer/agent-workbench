"""The main process's sandbox surface, for the background analysis service.

The Agent Protocol service is a **separate process**, so it cannot share this project's
Python objects — and the contract is explicit that it must not try: "主进程是唯一沙箱分配与
执行租约持有者，异步进程不直接认领/重建容器". This endpoint is the seam. The background graph
asks here, and this process answers with the same lease the foreground conversation uses.

Three properties are what make it a seam rather than a hole:

**A whitelist, not a proxy.** Three operations exist — write, execute, download. There is no
"call any manager method" form, and no argument that names a method. A passthrough would be a
remote code execution surface wearing an internal token.

**The token is checked first, before anything is parsed.** ``/internal/approvals/verify``
established the pattern; this follows it, including ``compare_digest``.

**Paths are bounded to the workspace.** An operation that could read ``/etc/passwd`` out of a
container and return it over HTTP would be an exfiltration service. The same boundary the
export tool uses applies here, normalised before the prefix test.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import logging
import posixpath
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

LOGGER = logging.getLogger("rush_harness.internal.sandbox")

#: Roots an operation may touch. Everything the background analyst needs — the report
#: directory and the scratch space — is under here.
ALLOWED_ROOTS: tuple[str, ...] = ("/workspace",)

#: The operations the background service is allowed to ask for.
OPERATIONS: frozenset[str] = frozenset({"write_files", "execute", "download_files"})

#: Bytes returned by ``download_files`` in one response. A report and its CSV are small; a
#: larger payload is a sign the caller is trying to move a dataset through this channel.
MAX_TRANSFER_BYTES = 4 * 1024 * 1024

DEFAULT_EXECUTE_TIMEOUT = 300.0
MAX_EXECUTE_TIMEOUT = 900.0


class FilePayload(BaseModel):
    path: str
    content_base64: str = ""


class OperationRequest(BaseModel):
    owner_user_id: str
    thread_id: str = ""
    operation_id: str = ""
    operation: str
    #: Operation-specific arguments. Validated per operation below; never forwarded wholesale.
    files: list[FilePayload] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    command: str = ""
    timeout: float = DEFAULT_EXECUTE_TIMEOUT


def check_path(path: str) -> str | None:
    """Return an error code when the path is out of bounds, else ``None``.

    Normalisation happens before the boundary test: ``/workspace/../etc/passwd`` has to be
    judged as where it lands, not as the string it was written as.
    """
    if not path or not isinstance(path, str) or "\x00" in path:
        return "invalid_path"
    if not path.startswith("/"):
        return "invalid_path"
    normalised = posixpath.normpath(path)
    if normalised == "/" or normalised.startswith("/.."):
        return "invalid_path"
    for root in ALLOWED_ROOTS:
        if normalised == root or normalised.startswith(root.rstrip("/") + "/"):
            return None
    return "path_not_allowed"


def _decode(content_base64: str) -> bytes | None:
    try:
        return base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError):
        return None


def build_internal_sandbox_router(*, context: Any, service_token: str) -> APIRouter:
    router = APIRouter(tags=["internal"])

    def _authorise(token: str | None) -> None:
        if not service_token:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "no internal service token is configured; the sandbox surface is closed",
            )
        if not token or not hmac.compare_digest(token, service_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid internal service token")

    @router.post("/internal/sandbox/operations")
    async def operate(
        body: OperationRequest,
        x_internal_service_token: str | None = Header(default=None),
    ) -> JSONResponse:
        _authorise(x_internal_service_token)

        if body.operation not in OPERATIONS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"unknown operation {body.operation!r}; allowed: {sorted(OPERATIONS)}",
            )
        if not body.owner_user_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "owner_user_id is required")

        manager = getattr(context, "sandboxes", None)
        if manager is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "no sandbox manager is configured in this process",
            )

        try:
            # The same lease the foreground conversation uses. The background service never
            # creates a container of its own, so a user cannot end up with two.
            proxy = manager.get_or_create(body.owner_user_id)
        except Exception as failure:  # noqa: BLE001 - reported, not crashed
            LOGGER.warning("no sandbox lease for %s: %s", body.owner_user_id, failure)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, f"sandbox unavailable: {failure}"
            ) from failure

        import asyncio

        if body.operation == "write_files":
            return await asyncio.to_thread(_write, proxy, body)
        if body.operation == "execute":
            return await asyncio.to_thread(_execute, proxy, body)
        return await asyncio.to_thread(_download, proxy, body)

    return router


def _write(proxy: Any, body: OperationRequest) -> JSONResponse:
    if not body.files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "write_files needs at least one file")

    uploads: list[tuple[str, bytes]] = []
    for entry in body.files:
        refusal = check_path(entry.path)
        if refusal:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{refusal}: {entry.path!r} 不在 {list(ALLOWED_ROOTS)} 下",
            )
        payload = _decode(entry.content_base64)
        if payload is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"{entry.path!r}: content_base64 is not valid base64"
            )
        uploads.append((entry.path, payload))

    responses = proxy.upload_files(uploads)
    written = []
    for entry, response in zip(body.files, responses, strict=True):
        error = getattr(response, "error", None)
        written.append({"path": entry.path, "ok": error is None, "error": error})
    return JSONResponse(
        {"data": {"operation": "write_files", "written": written}, "request_id": body.operation_id}
    )


def _execute(proxy: Any, body: OperationRequest) -> JSONResponse:
    if not body.command.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "execute needs a command")
    timeout = max(1.0, min(MAX_EXECUTE_TIMEOUT, float(body.timeout or DEFAULT_EXECUTE_TIMEOUT)))
    result = proxy.execute(body.command, timeout=timeout)
    return JSONResponse(
        {
            "data": {
                "operation": "execute",
                "exit_code": int(getattr(result, "exit_code", -1)),
                # Truncated deliberately: the caller asked for a command result, not a file
                # transfer, and an unbounded response is how a chatty command becomes an OOM.
                "output": str(getattr(result, "output", ""))[-20000:],
            },
            "request_id": body.operation_id,
        }
    )


def _download(proxy: Any, body: OperationRequest) -> JSONResponse:
    if not body.paths:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "download_files needs at least one path")

    # Bounds are checked for the whole request *before* the sandbox is asked anything, and a
    # refusal is a 400 rather than a per-file outcome. The difference matters: reporting
    # "file_not_found" for an out-of-bounds path would let the caller tell "outside the
    # workspace" apart from "outside the workspace and absent", and would make a boundary
    # violation look like an ordinary missing file.
    for path in body.paths:
        refusal = check_path(path)
        if refusal:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{refusal}: {path!r} 不在 {list(ALLOWED_ROOTS)} 下",
            )

    total = 0
    files: list[dict[str, Any]] = []
    responses = proxy.download_files(list(body.paths))
    for path, response in zip(body.paths, responses, strict=True):
        error = getattr(response, "error", None)
        content = getattr(response, "content", None)
        if content is not None:
            total += len(content)
            if total > MAX_TRANSFER_BYTES:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    f"response would exceed {MAX_TRANSFER_BYTES} bytes",
                )
        files.append(
            {
                "path": path,
                "ok": error is None and content is not None,
                "error": error,
                "content_base64": (
                    base64.b64encode(content).decode("ascii") if content is not None else None
                ),
            }
        )
    return JSONResponse(
        {"data": {"operation": "download_files", "files": files}, "request_id": body.operation_id}
    )


__all__ = [
    "ALLOWED_ROOTS",
    "DEFAULT_EXECUTE_TIMEOUT",
    "MAX_EXECUTE_TIMEOUT",
    "MAX_TRANSFER_BYTES",
    "OPERATIONS",
    "OperationRequest",
    "build_internal_sandbox_router",
    "check_path",
]
