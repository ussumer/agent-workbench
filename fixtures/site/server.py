#!/usr/bin/env python3
"""Demo quote site and skill download resource.

Run directly (no package needed, so nothing shadows the stdlib ``site`` module)::

    python fixtures/site/server.py

Binding is ``0.0.0.0:8088`` so an execution sandbox can reach the host through
its gateway address (for example ``http://host.docker.internal:8088`` on Docker
Desktop). T08 verifies container reachability from inside OpenSandbox; this
service only needs to listen on the host.

Everything served here is openly labelled demo data. The failure fixtures used by
tests — slow response, 500, a page without prices, a corrupt ZIP — only exist
when ``FIXTURES_TEST_MODE=1``; the normal site never fails at random.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Response  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

import pages  # noqa: E402
import skillpack  # noqa: E402

DEFAULT_PORT = 8088
TEST_MODE = os.environ.get("FIXTURES_TEST_MODE") == "1"

app = FastAPI(
    title="采购演示报价站与技能资源",
    description="课程 Demo 自建的演示数据服务，不是真实商户。",
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "fixtures-site",
        "demo_data": True,
        "test_mode": TEST_MODE,
    }


@app.get("/suppliers/{supplier_id}/quotes", response_class=HTMLResponse)
def supplier_quotes(supplier_id: str) -> HTMLResponse:
    if supplier_id not in pages.known_supplier_ids():
        raise HTTPException(status_code=404, detail=f"unknown supplier {supplier_id}")
    return HTMLResponse(pages.quotes_html(supplier_id))


@app.get("/skills/catalog.json")
def skills_catalog() -> JSONResponse:
    return JSONResponse(skillpack.catalog_payload())


@app.get("/skills/{archive_name}")
def skill_archive(archive_name: str) -> Response:
    if archive_name != skillpack.ARCHIVE_NAME:
        raise HTTPException(status_code=404, detail=f"unknown skill archive {archive_name}")
    payload = skillpack.build_skill_zip()
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{skillpack.ARCHIVE_NAME}"',
            "X-Content-Sha256": skillpack.sha256(payload),
        },
    )


if TEST_MODE:
    # Failure fixtures for tests. They are absent in the published site, so a real
    # scrape never meets an injected fault.

    @app.get("/test/slow")
    def slow(seconds: float = 5.0) -> dict:
        time.sleep(max(0.0, min(seconds, 30.0)))
        return {"status": "ok", "slept_seconds": seconds}

    @app.get("/test/error")
    def failing() -> Response:
        return Response(
            content='{"error":"injected failure for tests"}',
            status_code=500,
            media_type="application/json",
        )

    @app.get("/test/suppliers/{supplier_id}/quotes/no-prices", response_class=HTMLResponse)
    def quotes_without_prices(supplier_id: str) -> HTMLResponse:
        if supplier_id not in pages.known_supplier_ids():
            raise HTTPException(status_code=404, detail=f"unknown supplier {supplier_id}")
        return HTMLResponse(pages.quotes_html(supplier_id, with_prices=False))

    @app.get("/test/skills/broken.zip")
    def broken_archive() -> Response:
        return Response(
            content=b"PK\x03\x04 this is not a valid archive",
            media_type="application/zip",
        )


def main() -> int:
    import uvicorn

    host = os.environ.get("FIXTURES_HOST", "0.0.0.0")
    port = int(os.environ.get("FIXTURES_PORT", DEFAULT_PORT))
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
