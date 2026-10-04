#!/usr/bin/env python3
"""Prepare the demo's data, and say whether it is actually there.

The Java ERP seeds itself from ``fixtures/seed`` when it starts, so "seeding" here is not a
second data path — it is the step that makes the first one **verifiable**: it reads the ERP back
and compares what it finds with the numbers the demo cases promise (``tests/live/scenarios.py``,
which in turn derives its totals from ``fixtures/expected-v1.json``). A seeding command that
printed "已 seed" and stopped would be indistinguishable from one that did nothing.

It uses the running session when there is one, and otherwise starts the ERP by itself — so a
fresh checkout can be seeded before the rest of the stack is up.

Usage::

    python scripts/seed_demo.py
    python scripts/seed_demo.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(REPO_ROOT / "src"), str(REPO_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SESSION = REPO_ROOT / "artifacts" / "dev" / "session.json"


def _session_erp() -> str | None:
    """The ERP address of a running session, if one is running."""
    if not SESSION.is_file():
        return None
    try:
        session = json.loads(SESSION.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return str((session.get("services") or {}).get("erp") or "") or None


def _read_erp(base_url: str, token: str) -> dict[str, Any]:
    """Read the parts and orders back from the ERP."""
    import httpx

    headers = {"X-Service-Token": token, "X-Actor-Id": "demo-a"}
    with httpx.Client(
        base_url=base_url, headers=headers, timeout=httpx.Timeout(30.0, connect=10.0)
    ) as client:
        parts = client.get("/api/erp/v1/parts")
        orders = client.get("/api/erp/v1/orders")
    found: dict[str, Any] = {"parts": [], "orders": []}
    if parts.status_code < 300:
        found["parts"] = (parts.json().get("data") or {}).get("items") or []
    else:
        found["error"] = f"GET /parts -> HTTP {parts.status_code}"
    if orders.status_code < 300:
        found["orders"] = (orders.json().get("data") or {}).get("items") or []
    else:
        found.setdefault("error", f"GET /orders -> HTTP {orders.status_code}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from live import scenarios

    expected = dict(scenarios.by_id("D01").numbers)
    base_url = _session_erp()
    started: Any = None

    if base_url is None:
        # No session: start the ERP alone, seeded the way the stack would seed it.
        from fixtures import erp_service, loader

        with erp_service.running_erp(
            REPO_ROOT / "artifacts" / "dev" / "seed", seed_path=loader.SEED_PATH
        ) as erp:
            started = erp
            report = {
                "mode": "standalone ERP",
                "erp": erp.base_url,
                **_read_erp(erp.base_url, erp.token),
            }
    else:
        from api_view.web_config import PersistenceSettings

        report = {
            "mode": "running session",
            "erp": base_url,
            **_read_erp(base_url, PersistenceSettings.from_env().service_token),
        }

    problems: list[str] = []
    if report.get("error"):
        problems.append(str(report["error"]))
    stock = {str(item.get("part_id")): item.get("on_hand") for item in report["parts"]}
    for part_id, wanted in expected.items():
        actual = stock.get(part_id)
        if actual is None:
            problems.append(f"{part_id} 不在 ERP 的物料表里")
        elif str(actual) != str(wanted):
            problems.append(f"{part_id} 库存 {actual}，用例预期 {wanted}")

    report["expected_stock"] = expected
    report["problems"] = problems

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"数据来源：{report['mode']}（{report['erp']}）")
        print(f"  物料 {len(report['parts'])} 项，订单 {len(report['orders'])} 张")
        for part_id in sorted(expected):
            print(f"  {part_id} 库存 {stock.get(part_id, '缺失')}（用例预期 {expected[part_id]}）")
        for problem in problems:
            print(f"  ! {problem}")

    if started is not None:
        started.stop()
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
