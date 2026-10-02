#!/usr/bin/env python3
"""Join inventory warnings with fetched supplier quotes and write a costed reorder report.

Runs *inside* the sandbox, so it uses the standard library only. All arithmetic is
:class:`decimal.Decimal`; a float never touches a price.

Why this is a script and not something the model does by reading a few JSON files:

* **The total has to be reproducible.** ``2553.00`` is a checked value. A number a model
  arrived at by reasoning is a number nobody can recompute.
* **A missing quote must stay missing.** If a part has no offer in any fetched page, the
  honest output is "no quote" and exclusion from the total. A model asked to "produce a
  report" tends to fill the gap with the catalogue price, which is exactly the failure the
  task forbids. Here the gap is a row in the warnings section and nothing else.

Every recommendation carries the page it came from and when that page was quoted, so a
reader can tell a fetched price from an assumed one.

Exit codes::

    0  success
    2  bad usage
    4  input file malformed          (a required field is missing or mistyped)
    5  no usable quotes at all       (every warned part lacks an offer)

Usage::

    python build_report.py --warnings /workspace/scratch/warnings.json \\
        --quotes /workspace/scratch/quotes-S001.json /workspace/scratch/quotes-S002.json \\
        --skip-supplier S003 \\
        --out-md /workspace/report/reorder-report.md \\
        --out-csv /workspace/report/reorder-lines.csv \\
        --out-chart-data /workspace/report/reorder-chart.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MALFORMED = 4
EXIT_NO_QUOTES = 5

CENT = Decimal("0.01")

#: Fields a quote file must carry. ``source_url`` and ``quoted_at`` are required rather than
#: optional because the whole point of the report is that prices are traceable.
QUOTE_REQUIRED = ("supplier_id", "source_url", "quotes")


class InputError(Exception):
    """An input file is present but does not have the shape this script relies on."""


def money(value: Decimal) -> str:
    """Two-decimal string. ``ROUND_HALF_UP`` matches the ERP's own rounding."""
    return f"{value.quantize(CENT, rounding=ROUND_HALF_UP):.2f}"


def as_decimal(raw: object, *, where: str) -> Decimal:
    if isinstance(raw, float):
        # A float here means somebody upstream lost precision; converting its repr is the
        # most faithful recovery, and the caller is told via the exception if it is unusable.
        raw = repr(raw)
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as failure:
        raise InputError(f"{where}: {raw!r} is not a decimal amount") from failure


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Source:
    """One fetched page, kept whole so the report can show where prices came from."""

    supplier_id: str
    source_url: str
    quoted_at: str
    render_style: str
    unsupported_parts: tuple[str, ...]
    #: ``(part_id, currency) -> unit price``
    prices: dict[tuple[str, str], Decimal]


@dataclass(frozen=True)
class Warning:
    part_id: str
    quantity: int
    on_hand: int | None = None
    warning_threshold: int | None = None
    target_stock: int | None = None


@dataclass
class Line:
    part_id: str
    quantity: int
    supplier_id: str
    unit_price: Decimal
    currency: str
    amount: Decimal
    source_url: str
    quoted_at: str


@dataclass
class Report:
    lines: list[Line] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0.00"))

    @property
    def currency(self) -> str:
        currencies = {line.currency for line in self.lines}
        return currencies.pop() if len(currencies) == 1 else "CNY"


def load_quote_file(path: Path) -> Source:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as failure:
        raise InputError(f"{path}: cannot be read as JSON ({failure})") from failure

    missing = [key for key in QUOTE_REQUIRED if not document.get(key)]
    if missing:
        raise InputError(
            f"{path}: missing {missing}; this is not a fetch_quotes.py output. "
            "Re-run the scraper rather than filling the fields in by hand."
        )

    supplier_id = str(document["supplier_id"])
    prices: dict[tuple[str, str], Decimal] = {}
    for offer in document["quotes"]:
        part_id = str(offer.get("part_id", ""))
        if not part_id:
            raise InputError(f"{path}: an offer has no part_id")
        currency = str(offer.get("currency") or "CNY")
        price = as_decimal(offer.get("unit_price"), where=f"{path}:{part_id}")
        if price <= 0:
            raise InputError(f"{path}:{part_id}: unit_price must be positive, got {price}")
        key = (part_id, currency)
        # One page quoting a part twice is a page-level problem, not something to average.
        if key in prices and prices[key] != price:
            raise InputError(
                f"{path}:{part_id}: quoted twice with different prices "
                f"({prices[key]} and {price}); refusing to pick one"
            )
        prices[key] = price

    return Source(
        supplier_id=supplier_id,
        source_url=str(document["source_url"]),
        quoted_at=str(document.get("quoted_at") or ""),
        render_style=str(document.get("render_style") or ""),
        unsupported_parts=tuple(str(item) for item in document.get("unsupported_parts") or ()),
        prices=prices,
    )


def load_warnings(path: Path) -> list[Warning]:
    """Read an ``inventory_warning`` payload, in either its enveloped or bare form."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as failure:
        raise InputError(f"{path}: cannot be read as JSON ({failure})") from failure

    data = document.get("data") if isinstance(document, dict) else None
    container = data if isinstance(data, dict) else document
    items = container.get("items") if isinstance(container, dict) else None
    if not isinstance(items, list):
        raise InputError(f"{path}: expected an object with an 'items' list")

    warnings: list[Warning] = []
    for index, item in enumerate(items):
        part_id = str(item.get("part_id") or "")
        if not part_id:
            raise InputError(f"{path}: item {index} has no part_id")

        quantity = item.get("suggested_quantity")
        if quantity is None:
            # The contract defines the suggestion, so it can be recovered rather than guessed
            # (docs/plan/contracts/erp.md: 建议补货 max(target_stock-on_hand, 0)).
            on_hand, target = item.get("on_hand"), item.get("target_stock")
            if isinstance(on_hand, int) and isinstance(target, int):
                quantity = max(target - on_hand, 0)
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            warnings.append(Warning(part_id=part_id, quantity=0))
            continue

        warnings.append(
            Warning(
                part_id=part_id,
                quantity=quantity,
                on_hand=item.get("on_hand"),
                warning_threshold=item.get("warning_threshold"),
                target_stock=item.get("target_stock"),
            )
        )
    return warnings


# --------------------------------------------------------------------------- #
# comparison
# --------------------------------------------------------------------------- #


def build_report(
    sources: list[Source], warnings: list[Warning], *, skip_suppliers: set[str]
) -> Report:
    """Pick, for each warned part, the cheapest offer across the fetched pages.

    Ordering rule from ``fixtures/expected-v1.json`` (``quote_comparison.rule``): compare
    within the same part and currency, take the lowest unit price, and break a tie by
    ``supplier_id`` ascending. The tie-break is spelled out because "cheapest" is ambiguous
    when two suppliers quote the same number, and an arbitrary pick would make the report
    unstable between runs.
    """
    report = Report(sources=list(sources))

    usable = [source for source in sources if source.supplier_id not in skip_suppliers]
    # Recorded for every requested exclusion, not only for ones that happened to be fetched.
    # "We left S003 out" is a fact about the comparison, and a reader who cannot see it
    # cannot tell whether the cheapest price they are looking at is the cheapest available.
    fetched_ids = {source.supplier_id for source in sources}
    for supplier_id in sorted(skip_suppliers):
        if supplier_id in fetched_ids:
            report.warnings.append(
                f"供应商 {supplier_id} 已按要求排除，其已抓取到的报价不计入比价"
            )
        else:
            report.warnings.append(f"供应商 {supplier_id} 已按要求排除，未参与比价")

    quote_years = {source.quoted_at for source in usable if source.quoted_at}
    if len(quote_years) > 1:
        report.warnings.append(
            "各来源报价时间不一致：" + "、".join(sorted(quote_years))
        )

    parts_with_offers: set[str] = set()
    ignored_parts: set[tuple[str, str]] = set()
    for source in usable:
        parts_with_offers.update(part_id for part_id, _currency in source.prices)

    for warning in warnings:
        if warning.quantity <= 0:
            report.warnings.append(
                f"{warning.part_id}：预警记录没有可用的建议补货量，已跳过"
            )
            continue

        candidates: list[tuple[Decimal, str, str]] = []
        for source in usable:
            for (part_id, currency), price in source.prices.items():
                if part_id != warning.part_id:
                    continue
                if (part_id, currency) in ignored_parts:
                    continue
                candidates.append((price, source.supplier_id, currency))
                break

        if not candidates:
            # The whole point of the warnings section: a gap stays a gap.
            report.warnings.append(
                f"{warning.part_id}：已抓取的页面中没有任何报价，**未计入合计**；"
                "请勿用目录价代替抓取价"
            )
            continue

        currencies = {currency for _price, _supplier, currency in candidates}
        if len(currencies) > 1:
            report.warnings.append(
                f"{warning.part_id}：报价存在多种币种 {sorted(currencies)}，"
                "无法直接比较，已跳过"
            )
            continue

        price, supplier_id, currency = min(candidates, key=lambda item: (item[0], item[1]))
        source = next(item for item in usable if item.supplier_id == supplier_id)
        report.lines.append(
            Line(
                part_id=warning.part_id,
                quantity=warning.quantity,
                supplier_id=supplier_id,
                unit_price=price,
                currency=currency,
                amount=(price * warning.quantity).quantize(CENT, rounding=ROUND_HALF_UP),
                source_url=source.source_url,
                quoted_at=source.quoted_at,
            )
        )

    for source in usable:
        for part_id in sorted(source.unsupported_parts):
            if part_id not in parts_with_offers:
                report.warnings.append(
                    f"{source.supplier_id}：不提供 {part_id}（来源 {source.source_url}）"
                )

    report.lines.sort(key=lambda line: line.part_id)
    return report


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def sources_table(report: Report) -> str:
    rows = ["| 供应商 | 渲染 | 报价时间 | 来源页面 |", "|---|---|---|---|"]
    for source in sorted(report.sources, key=lambda item: item.supplier_id):
        rows.append(
            f"| {source.supplier_id} | {source.render_style or '-'} | "
            f"{source.quoted_at or '未标注'} | {source.source_url} |"
        )
    return "\n".join(rows)


def lines_table(report: Report) -> str:
    rows = [
        "| 物料 | 数量 | 供应商 | 单价 | 金额 | 来源页面 | 报价时间 |",
        "|---|---|---|---|---|---|---|",
    ]
    for line in report.lines:
        rows.append(
            f"| {line.part_id} | {line.quantity} | {line.supplier_id} | "
            f"{money(line.unit_price)} | {money(line.amount)} | {line.source_url} | "
            f"{line.quoted_at or '未标注'} |"
        )
    return "\n".join(rows)


def derivation(report: Report) -> str:
    if not report.lines:
        return "无可用报价，未产生合计"
    parts = [f"({line.quantity} × {money(line.unit_price)})" for line in report.lines]
    amounts = " + ".join(money(line.amount) for line in report.lines)
    return f"{' + '.join(parts)} = {amounts}"


def warnings_block(report: Report) -> str:
    if not report.warnings:
        return "无"
    return "\n".join(f"- {item}" for item in report.warnings)


def render_markdown(report: Report, *, generated_at: str, template: str | None) -> str:
    values = {
        "generated_at": generated_at,
        "total_amount": money(report.total),
        "currency": report.currency,
        "line_count": str(len(report.lines)),
        "sources_table": sources_table(report),
        "lines_table": lines_table(report),
        "warnings": warnings_block(report),
        "derivation": derivation(report),
    }
    if template:
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{{" + key + "}}", value)
        # An unfilled placeholder would ship a report with a hole in it.
        leftover = [key for key in values if "{{" + key + "}}" in rendered]
        if leftover:
            report.warnings.append(f"模板未替换的占位符：{leftover}")
        return rendered

    return f"""# 补货比价报告

生成时间：{values["generated_at"]}
数据来源：**HTTP 抓取**（下方每个价格都带来源页面与报价时间，未使用目录价）

## 1. 抓取来源

{values["sources_table"]}

## 2. 补货建议（同物料同币种取最低单价）

{values["lines_table"]}

**合计：{values["total_amount"]} {values["currency"]}**

计算式：{values["derivation"]}

## 3. 警告与缺口

{values["warnings"]}

## 4. 口径

- 比价规则：同物料同币种取最低单价；相同价格以 `supplier_id` 升序确定推荐。
- 金额一律两位小数，十进制运算。
- 未取得报价的物料**不会**用目录价或估算值补上，只在第 3 节列出。
"""


def write_csv(path: Path, report: Report) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "part_id",
                "quantity",
                "supplier_id",
                "unit_price",
                "currency",
                "amount",
                "source_url",
                "quoted_at",
            ]
        )
        for line in report.lines:
            writer.writerow(
                [
                    line.part_id,
                    line.quantity,
                    line.supplier_id,
                    money(line.unit_price),
                    line.currency,
                    money(line.amount),
                    line.source_url,
                    line.quoted_at,
                ]
            )


def chart_rows(report: Report) -> list[dict[str, object]]:
    """Rows shaped for the agent's ``chart_generator`` (``{label, value}``)."""
    return [
        {"label": f"{line.part_id} {line.supplier_id}", "value": float(line.amount)}
        for line in report.lines
    ]


def main(argv: list[str] | None = None) -> int:
    # The summary on stdout is machine-read. A console that defaults to a legacy code page
    # would encode it in something the caller cannot parse, so say UTF-8 explicitly. Inside
    # the Linux sandbox this is a no-op.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Build the reorder comparison report.")
    parser.add_argument("--warnings", required=True, help="inventory_warning JSON payload")
    parser.add_argument("--quotes", required=True, nargs="+", help="fetch_quotes.py outputs")
    parser.add_argument("--skip-supplier", action="append", default=[])
    parser.add_argument("--template", default=None, help="Markdown template with {{placeholders}}")
    parser.add_argument("--out-md", required=True)
    parser.add_argument("--out-csv", default=None)
    parser.add_argument("--out-chart-data", default=None)
    args = parser.parse_args(argv)

    try:
        sources = [load_quote_file(Path(item)) for item in args.quotes]
        warnings = load_warnings(Path(args.warnings))
        template = (
            Path(args.template).read_text(encoding="utf-8") if args.template else None
        )
    except (InputError, OSError) as failure:
        print(str(failure), file=sys.stderr)
        return EXIT_MALFORMED

    report = build_report(sources, warnings, skip_suppliers=set(args.skip_supplier))
    if not report.lines:
        print(
            "no usable quotes: every warned part is missing from the fetched pages, so "
            "there is no report to write",
            file=sys.stderr,
        )
        return EXIT_NO_QUOTES

    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    markdown = render_markdown(report, generated_at=generated_at, template=template)

    md_path = Path(args.out_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown, encoding="utf-8")

    if args.out_csv:
        write_csv(Path(args.out_csv), report)
    if args.out_chart_data:
        chart_path = Path(args.out_chart_data)
        chart_path.parent.mkdir(parents=True, exist_ok=True)
        chart_path.write_text(
            json.dumps(chart_rows(report), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    summary = {
        "total_amount": money(report.total),
        "currency": report.currency,
        "line_count": len(report.lines),
        "derivation": derivation(report),
        "warnings": report.warnings,
        "lines": [
            {
                "part_id": line.part_id,
                "quantity": line.quantity,
                "supplier_id": line.supplier_id,
                "unit_price": money(line.unit_price),
                "amount": money(line.amount),
                "source_url": line.source_url,
                "quoted_at": line.quoted_at,
            }
            for line in report.lines
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
