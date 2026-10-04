"""Quote pages rendered from the shared seed.

Two deliberate differences keep the scraper honest:

* S001 renders an HTML **table**, S002 renders a **list**. A scraper that only
  matches one fixed DOM shape will fail on the other.
* Parts a supplier does not offer are listed explicitly in an
  ``#unsupported`` section, so "this supplier does not sell that part" is
  distinguishable from "the page could not be parsed".

Every price comes from ``fixtures/seed-v1.json``; nothing is hardcoded here.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SEED_PATH = Path(__file__).resolve().parents[1] / "seed-v1.json"

# Fixed demo timestamp: the page must not vary between requests, or acceptance
# could never pin the quoted time.
QUOTED_AT = "2026-09-16T00:00:00Z"

DEMO_NOTICE = "演示数据：本报价站为课程 Demo 自建，不代表任何真实商户，也不是实时价格。"

# Renderer per supplier, mirroring the two shapes the contract requires.
RENDER_STYLE = {"S001": "table", "S002": "list"}


@lru_cache(maxsize=1)
def load_seed() -> dict:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def supplier_name(supplier_id: str) -> str:
    for supplier in load_seed()["suppliers"]:
        if supplier["supplier_id"] == supplier_id:
            return supplier["name"]
    return supplier_id


def known_supplier_ids() -> set[str]:
    return {supplier["supplier_id"] for supplier in load_seed()["suppliers"]}


def _offers(supplier_id: str) -> list[dict]:
    seed = load_seed()
    skus = {part["part_id"]: part["sku"] for part in seed["parts"]}
    return [
        {
            "part_id": row["part_id"],
            "sku": skus.get(row["part_id"], ""),
            "currency": row["currency"],
            "unit_price": row["catalog_price"],
        }
        for row in seed["supplier_parts"]
        if row["supplier_id"] == supplier_id
    ]


def _unsupported(supplier_id: str) -> list[str]:
    seed = load_seed()
    offered = {
        row["part_id"] for row in seed["supplier_parts"] if row["supplier_id"] == supplier_id
    }
    return sorted(part["part_id"] for part in seed["parts"] if part["part_id"] not in offered)


def _table_rows(offers: list[dict], *, with_prices: bool) -> str:
    rows = []
    for offer in offers:
        price_cell = (
            f'<td class="unit-price">{offer["unit_price"]}</td>'
            if with_prices
            else '<td class="unit-price"></td>'
        )
        price_attribute = f' data-unit-price="{offer["unit_price"]}"' if with_prices else ""
        rows.append(
            f'      <tr data-part-id="{offer["part_id"]}" data-sku="{offer["sku"]}"'
            f' data-currency="{offer["currency"]}"{price_attribute}>'
            f'<td class="part-id">{offer["part_id"]}</td>'
            f'<td class="sku">{offer["sku"]}</td>'
            f'<td class="currency">{offer["currency"]}</td>'
            f"{price_cell}</tr>"
        )
    body = "\n".join(rows) if rows else "      <!-- 该供应商没有可用报价 -->"
    return (
        "    <table>\n"
        "      <thead><tr><th>物料</th><th>SKU</th><th>币种</th><th>单价</th></tr></thead>\n"
        f"      <tbody>\n{body}\n      </tbody>\n    </table>"
    )


def _list_items(offers: list[dict], *, with_prices: bool) -> str:
    items = []
    for offer in offers:
        price_attribute = f' data-unit-price="{offer["unit_price"]}"' if with_prices else ""
        price_text = offer["unit_price"] if with_prices else "价格未公布"
        items.append(
            f'      <li data-part-id="{offer["part_id"]}" data-sku="{offer["sku"]}"'
            f' data-currency="{offer["currency"]}"{price_attribute}>'
            f'<span class="part-id">{offer["part_id"]}</span> '
            f'<span class="sku">{offer["sku"]}</span> '
            f'<span class="unit-price">{price_text}</span> '
            f'<span class="currency">{offer["currency"]}</span></li>'
        )
    body = "\n".join(items) if items else "      <!-- 该供应商没有可用报价 -->"
    return f"    <ul>\n{body}\n    </ul>"


def _unsupported_section(supplier_id: str) -> str:
    missing = _unsupported(supplier_id)
    items = "\n".join(
        f'      <li data-part-id="{part_id}">{part_id} 无供货关系</li>' for part_id in missing
    )
    body = f"\n{items}\n    " if items else ""
    return (
        f'  <section id="unsupported" data-unsupported="{",".join(missing)}">\n'
        "    <h2>本供应商不提供的物料</h2>\n"
        f"    <ul>{body}</ul>\n"
        "  </section>"
    )


def quotes_html(supplier_id: str, *, with_prices: bool = True) -> str:
    """Render the quote page for one supplier."""
    style = RENDER_STYLE.get(supplier_id, "table")
    offers = _offers(supplier_id)
    rendered = (
        _table_rows(offers, with_prices=with_prices)
        if style == "table"
        else _list_items(offers, with_prices=with_prices)
    )
    container_tag = style

    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <title>{supplier_name(supplier_id)} 报价（演示数据）</title>
  </head>
  <body data-page="supplier-quotes">
    <header>
      <h1>{supplier_name(supplier_id)}</h1>
      <p class="demo-notice">{DEMO_NOTICE}</p>
      <p class="quote-meta">
        <span class="supplier-id" data-supplier-id="{supplier_id}">{supplier_id}</span>
        <time class="quoted-at" data-quoted-at="{QUOTED_AT}">{QUOTED_AT}</time>
      </p>
    </header>
    <section id="quotes" data-render="{container_tag}" data-supplier-id="{supplier_id}">
{rendered}
    </section>
{_unsupported_section(supplier_id)}
  </body>
</html>
"""
