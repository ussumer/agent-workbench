#!/usr/bin/env python3
"""Regenerate the published MCP tool-schema snapshot.

T04 asserts that the gateway's real ``tools/list`` result matches
``fixtures/mcp-tools.snapshot.json``. When a tool schema changes on purpose, run
this script so the drift is deliberate and reviewable::

    python scripts/mcp_snapshot.py

It starts a real ERP and gateway, so it needs a JDK and the project's Python
environment. It never touches the demo database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import httpx  # noqa: E402
from fixtures import erp_service, loader, mcp_service  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

SNAPSHOT_PATH = ROOT / "fixtures" / "mcp-tools.snapshot.json"
NOTE = (
    "MCP 工具名称与输入 schema 快照。T04 验收会与真实 tools/list 结果比对；"
    "任何漂移都必须是有意为之。重新生成：python scripts/mcp_snapshot.py"
)


async def _list_tools(url: str) -> dict:
    async with httpx.AsyncClient(
        headers={"x-actor-id": "snapshot"}, timeout=30.0, trust_env=False
    ) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                return {
                    tool.name: tool.inputSchema
                    for tool in sorted(listed.tools, key=lambda item: item.name)
                }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare with the checked-in snapshot instead of rewriting it",
    )
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as workspace:
        work = Path(workspace)
        with erp_service.running_erp(work / "erp", seed_path=loader.SEED_PATH) as erp:
            with mcp_service.running_gateway(work / "gateway", erp_base_url=erp.base_url) as gateway:
                tools = asyncio.run(_list_tools(gateway.mcp_url))

    payload = {"note": NOTE, "tool_count": len(tools), "tools": tools}

    if args.check:
        if not SNAPSHOT_PATH.is_file():
            print(f"snapshot missing: {SNAPSHOT_PATH}", file=sys.stderr)
            return 1
        current = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if current.get("tools") != tools:
            print("snapshot differs from the live tool surface", file=sys.stderr)
            return 1
        print(f"snapshot matches ({len(tools)} tools)")
        return 0

    SNAPSHOT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {SNAPSHOT_PATH.relative_to(ROOT)} with {len(tools)} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
