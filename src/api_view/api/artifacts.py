"""Artifact metadata and download endpoints.

Three rules, all of them about not lying to the client:

**Another user's artifact is a 404.** The lookup is owner-scoped from the start, so a miss
and a foreign id are the same answer. The ids are random too, which removes the invitation
without replacing the check.

**A row without bytes never yields a link.** The registry and the blob store are separate
collections, so "the entry exists but the file is gone" is a state that really happens. The
metadata route reports it as ``download_ready: false``; the content route answers 410 rather
than 200-with-zero-bytes, because a zero-byte download looks like success to every client
that only checks the status code.

**The digest is advertised, so the download can be verified.** The bytes were hashed when
they were stored, and that same value is returned in ``sha256`` and again in the
``X-Content-Sha256`` header. A client that checks it is checking what the agent produced,
not what the disk happened to hand back.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from api_view.api.deps import WebContext, require_owner

LOGGER = logging.getLogger("rush_harness.api.artifacts")

#: Header the download carries, same name the fixture site uses for its archives.
DIGEST_HEADER = "X-Content-Sha256"

MAX_PAGE_SIZE = 100


def build_artifacts_router(context: WebContext) -> APIRouter:
    router = APIRouter(tags=["artifacts"])

    @router.get("/artifacts")
    async def list_artifacts(
        request: Request, thread_id: str | None = None, page_size: int = 50
    ) -> JSONResponse:
        """The caller's artifacts, newest last, optionally narrowed to one thread."""
        owner = require_owner(request)
        limit = max(1, min(MAX_PAGE_SIZE, page_size))

        if thread_id is None:
            # Without a thread there is nothing to scope by, and listing across every thread
            # is not something this demo needs; refuse rather than return a partial answer
            # that looks complete.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "thread_id is required; artifacts belong to a conversation",
            )

        records = context.artifacts.list_for_thread(owner, thread_id)
        items = []
        for record in records[:limit]:
            described = context.artifacts.describe(owner, record.artifact_id)
            present = described[1] if described else False
            items.append(record.public(download_ready=present))

        return JSONResponse(
            {"data": {"items": items, "total": len(records)}, "request_id": ""}
        )

    @router.get("/artifacts/{artifact_id}")
    async def artifact_metadata(artifact_id: str, request: Request) -> JSONResponse:
        owner = require_owner(request)
        described = context.artifacts.describe(owner, artifact_id)
        if described is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such artifact")
        record, download_ready = described
        if not download_ready:
            LOGGER.warning("artifact %s has no content to serve", artifact_id)
        return JSONResponse(
            {"data": record.public(download_ready=download_ready), "request_id": ""}
        )

    @router.get("/artifacts/{artifact_id}/content")
    async def artifact_content(artifact_id: str, request: Request) -> Response:
        owner = require_owner(request)
        described = context.artifacts.describe(owner, artifact_id)
        if described is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such artifact")

        record, download_ready = described
        if not download_ready:
            raise HTTPException(
                status.HTTP_410_GONE,
                "artifact content is no longer available; it was registered but the "
                "stored bytes are missing",
            )

        opened = context.artifacts.open(owner, artifact_id)
        if opened is None:
            # The blob vanished between the two reads.
            raise HTTPException(status.HTTP_410_GONE, "artifact content is no longer available")
        record, content = opened

        filename = record.name or "artifact"
        return Response(
            content=content,
            media_type=record.mime,
            headers={
                # ``filename*`` carries the real name; the plain ``filename`` is an ASCII
                # fallback because a raw UTF-8 name in a header is not portable.
                "Content-Disposition": (
                    f'attachment; filename="{_ascii_fallback(filename)}"; '
                    f"filename*=UTF-8''{quote(filename)}"
                ),
                "Content-Length": str(len(content)),
                DIGEST_HEADER: record.sha256,
                "Cache-Control": "private, max-age=0, must-revalidate",
            },
        )

    return router


def _ascii_fallback(filename: str) -> str:
    """A header-safe ASCII stand-in for a possibly non-ASCII name."""
    cleaned = "".join(
        character if (character.isascii() and character.isalnum()) or character in "._-"
        else "_"
        for character in filename
    )
    return cleaned or "artifact"


__all__ = [
    "DIGEST_HEADER",
    "MAX_PAGE_SIZE",
    "build_artifacts_router",
]
