#!/usr/bin/env python3
"""Sum reorder quantities and unit prices into a two-decimal cost summary.

This script only aggregates facts it is given. It never creates or modifies an
order: ordering stays behind the ERP's approval path.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

PART_ID_RE = re.compile(r"^P\d{3}$")
SUPPLIER_ID_RE = re.compile(r"^S\d{3}$")
# Two-decimal money string, 0.01..999999.99. Three decimals are an error, not
# something to round away.
MONEY_RE = re.compile(r"^(?:0\.(?:0[1-9]|[1-9][0-9])|[1-9][0-9]{0,5}\.[0-9]{2})$")
MIN_QUANTITY = 1
MAX_QUANTITY = 10_000


class InputError(ValueError):
    """Raised when the caller's input violates the documented contract."""


def _money(value: object, field: str) -> Decimal:
    if not isinstance(value, str) or not MONEY_RE.match(value):
        raise InputError(f"{field} must be a two-decimal money string, got {value!r}")
    try:
        return Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - regex already constrains this
        raise InputError(f"{field} is not a valid decimal: {value!r}") from exc


def _quantity(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputError(f"{field} must be an integer, got {value!r}")
    if not MIN_QUANTITY <= value <= MAX_QUANTITY:
        raise InputError(f"{field} must be within {MIN_QUANTITY}..{MAX_QUANTITY}, got {value}")
    return value


def summarise(payload: object) -> dict:
    """Validate the input document and return the summary structure."""
    if not isinstance(payload, dict):
        raise InputError("input must be a JSON object")
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        raise InputError("input must contain a non-empty 'lines' array")
    currency = payload.get("currency", "CNY")
    if currency != "CNY":
        raise InputError(f"unsupported currency {currency!r}; only CNY is available")

    total = Decimal("0.00")
    out_lines = []
    source_urls: list[str] = []
    seen_parts: set[str] = set()

    for index, raw in enumerate(lines):
        where = f"lines[{index}]"
        if not isinstance(raw, dict):
            raise InputError(f"{where} must be an object")
        part_id = raw.get("part_id")
        if not isinstance(part_id, str) or not PART_ID_RE.match(part_id):
            raise InputError(f"{where}.part_id must look like P001, got {part_id!r}")
        if part_id in seen_parts:
            raise InputError(f"{where}.part_id {part_id} appears more than once")
        seen_parts.add(part_id)

        supplier_id = raw.get("supplier_id")
        if not isinstance(supplier_id, str) or not SUPPLIER_ID_RE.match(supplier_id):
            raise InputError(f"{where}.supplier_id must look like S001, got {supplier_id!r}")

        line_currency = raw.get("currency", currency)
        if line_currency != currency:
            raise InputError(
                f"{where}.currency {line_currency!r} differs from {currency!r}; "
                "no exchange-rate service is available"
            )

        quantity = _quantity(raw.get("quantity"), f"{where}.quantity")
        unit_price = _money(raw.get("unit_price"), f"{where}.unit_price")
        amount = (unit_price * quantity).quantize(Decimal("0.01"))

        source_url = raw.get("source_url")
        if not isinstance(source_url, str) or not source_url:
            raise InputError(f"{where}.source_url is required so quotes stay traceable")
        if source_url not in source_urls:
            source_urls.append(source_url)

        out_lines.append(
            {
                "part_id": part_id,
                "supplier_id": supplier_id,
                "quantity": quantity,
                "unit_price": f"{unit_price:.2f}",
                "currency": currency,
                "amount": f"{amount:.2f}",
                "source_url": source_url,
            }
        )
        total += amount

    return {
        "currency": currency,
        "lines": out_lines,
        "total_amount": f"{total.quantize(Decimal('0.01')):.2f}",
        "source_urls": source_urls,
    }


def render_markdown(summary: dict) -> str:
    rows = [
        "| 物料 | 供应商 | 数量 | 单价 | 金额 | 来源 |",
        "|---|---|---|---|---|---|",
    ]
    for line in summary["lines"]:
        rows.append(
            "| {part_id} | {supplier_id} | {quantity} | {unit_price} | {amount} | {source_url} |".format(
                **line
            )
        )
    rows.append("")
    rows.append(f"合计：{summary['total_amount']} {summary['currency']}")
    rows.append("")
    rows.append("数据来源：" + "、".join(summary["source_urls"]))
    return "\n".join(rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="input JSON file")
    parser.add_argument("--out-dir", required=True, help="directory for summary.json and report.md")
    args = parser.parse_args(argv)

    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read input: {exc}", file=sys.stderr)
        return 2

    try:
        summary = summarise(payload)
    except InputError as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return 3

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_markdown(summary), encoding="utf-8")
    print(summary["total_amount"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
