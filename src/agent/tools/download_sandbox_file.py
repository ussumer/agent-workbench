"""Move a file the agent produced out of the sandbox and make it downloadable.

The report and its CSV are written by a script *inside* the sandbox. Until something copies
them out, they are only reachable from a container that will eventually be recycled, and a
link to them would be a link to nothing. This tool is that copy step: it reads the bytes,
registers them as an artifact owned by the caller, and returns the id the user can download
from.

Three things are deliberate:

**A path boundary.** Only files under the workspace can be exported. Without it, an agent
talked into "summarising the credentials file" would have a working exfiltration path with a
friendly download link at the end. The path is normalised before the check, so
``/workspace/../etc/passwd`` resolves to ``/etc/passwd`` and is refused rather than accepted
on a prefix match.

**No scope, no artifact.** The owner and thread come from the run's trusted config, never
from the tool arguments. A tool call cannot name the owner, so it cannot file a document
under somebody else's account.

**A failure is reported, never papered over.** A missing file, a permission refusal and an
out-of-bounds path each come back as a named code. Nothing is registered unless the bytes
were actually read, so a returned ``download_url`` always points at a file that exists.
"""

from __future__ import annotations

import json
import logging
import posixpath
from collections.abc import Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

from agent.artifacts.service import SCOPE_OWNER_KEY, SCOPE_THREAD_KEY, resolve_scope

LOGGER = logging.getLogger("rush_harness.tools.download_sandbox_file")

#: Roots a file may be exported from. The sandbox workspace is where products are written;
#: everything else — system paths, other mounts — is out of bounds by default.
ALLOWED_ROOTS: tuple[str, ...] = ("/workspace",)

#: What the user (or the UI) fetches. Built from the id, so it cannot point at a path.
DOWNLOAD_URL_TEMPLATE = "/api/artifacts/{artifact_id}/content"

DOWNLOAD_SANDBOX_FILE_DESCRIPTION = (
    "把一个沙箱内的文件转存为可下载产件，返回 artifact_id 与下载地址。"
    "只能导出 /workspace 下的文件（报告、CSV、图表等产件都写在那里）。"
    "文件不存在、越界或没有读权限时返回明确的错误码，不会返回一个指向空文件的链接。"
    "参数：path（沙箱内绝对路径）、可选 name（展示用文件名，省略时用原文件名）。"
)


def _ok(data: Mapping[str, Any]) -> str:
    return json.dumps(
        {"ok": True, "data": dict(data), "error": None, "request_id": None},
        ensure_ascii=False,
    )


def _fail(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: Any = None,
) -> str:
    return json.dumps(
        {
            "ok": False,
            "data": None,
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details,
            },
            "request_id": None,
        },
        ensure_ascii=False,
    )


def check_path(path: str) -> str | None:
    """Return an error code when the path may not be exported, else ``None``.

    Normalisation happens *before* the boundary check. ``posixpath.normpath`` collapses
    ``..`` segments, so a path that climbs out of the workspace is compared as the place it
    actually lands rather than as the string it was written as.
    """
    if not path or not isinstance(path, str):
        return "invalid_path"
    if "\x00" in path:
        return "invalid_path"
    if not path.startswith("/"):
        # A relative path would resolve against an unknown working directory.
        return "invalid_path"

    normalised = posixpath.normpath(path)
    if normalised == "/" or normalised.startswith("/.."):
        return "invalid_path"
    for root in ALLOWED_ROOTS:
        if normalised == root or normalised.startswith(root.rstrip("/") + "/"):
            return None
    return "path_not_allowed"


def export_file(
    *,
    backend: Any,
    artifacts: Any,
    owner_user_id: str,
    thread_id: str,
    path: str,
    name: str | None = None,
) -> dict[str, Any]:
    """Read one sandbox file and register it. Raises nothing; returns an envelope body."""
    responses = backend.download_files([path])
    if not responses:
        return {
            "ok": False,
            "code": "file_not_found",
            "message": f"沙箱没有返回 {path} 的读取结果",
        }

    response = responses[0]
    error = getattr(response, "error", None)
    content = getattr(response, "content", None)
    if error or content is None:
        code = str(error or "file_not_found")
        return {
            "ok": False,
            "code": code,
            "message": (
                f"读取沙箱文件 {path} 失败（{code}）。"
                "请确认脚本真的成功写入了这个路径，不要在文件缺失时编造产件。"
            ),
        }

    filename = name or posixpath.basename(path) or "artifact"
    record = artifacts.register(
        owner_user_id=owner_user_id,
        thread_id=thread_id,
        name=filename,
        content=content,
        source=f"sandbox:{path}",
    )
    return {
        "ok": True,
        "artifact": {
            "artifact_id": record.artifact_id,
            "name": record.name,
            "mime": record.mime,
            "size": record.size,
            "sha256": record.sha256,
            "download_url": DOWNLOAD_URL_TEMPLATE.format(artifact_id=record.artifact_id),
            "source": record.source,
        },
    }


def build_download_sandbox_file_tool(*, backend: Any, artifacts: Any) -> StructuredTool:
    """The tool. The backend and the artifact service are captured, not passed by the model."""

    # The annotation must be exactly ``RunnableConfig`` — measured, not assumed: with
    # ``RunnableConfig | None`` the framework treats ``config`` as an ordinary parameter, so it
    # appears in the tool schema and the model is asked to supply the owner, which is the one
    # thing it must never be able to name. Declared this way it is injected and stays out of
    # the schema.
    def download_sandbox_file(
        path: str, name: str | None = None, config: RunnableConfig = None
    ) -> str:  # type: ignore[assignment]
        scope = resolve_scope(config)
        if scope is None:
            return _fail(
                "SCOPE_MISSING",
                f"这次运行没有携带 {SCOPE_OWNER_KEY!r}/{SCOPE_THREAD_KEY!r}，"
                "无法确定产件归属，已拒绝导出",
            )
        owner_user_id, thread_id = scope

        refusal = check_path(path)
        if refusal == "invalid_path":
            return _fail(
                "invalid_path",
                f"{path!r} 不是可用于导出的绝对路径",
                details={"allowed_roots": list(ALLOWED_ROOTS)},
            )
        if refusal == "path_not_allowed":
            # The refusal is deliberately about the boundary, not about the file: saying
            # "no such file" here would invite a retry with a different path.
            return _fail(
                "path_not_allowed",
                f"只能导出 {list(ALLOWED_ROOTS)} 下的文件，{path!r} 不在其中",
                details={"allowed_roots": list(ALLOWED_ROOTS)},
            )

        try:
            result = export_file(
                backend=backend,
                artifacts=artifacts,
                owner_user_id=owner_user_id,
                thread_id=thread_id,
                path=path,
                name=name,
            )
        except ValueError as failure:
            # Raised by the artifact service (empty payload, over the size ceiling).
            LOGGER.warning("artifact refused for %s: %s", path, failure)
            return _fail("artifact_refused", str(failure))

        if not result["ok"]:
            LOGGER.info("export of %s failed: %s", path, result["code"])
            return _fail(result["code"], result["message"])

        LOGGER.info(
            "exported %s as %s for owner=%s", path, result["artifact"]["artifact_id"], owner_user_id
        )
        return _ok(result["artifact"])

    return StructuredTool.from_function(
        func=download_sandbox_file,
        name="download_sandbox_file",
        description=DOWNLOAD_SANDBOX_FILE_DESCRIPTION,
    )


__all__ = [
    "ALLOWED_ROOTS",
    "DOWNLOAD_SANDBOX_FILE_DESCRIPTION",
    "DOWNLOAD_URL_TEMPLATE",
    "build_download_sandbox_file_tool",
    "check_path",
    "export_file",
]
