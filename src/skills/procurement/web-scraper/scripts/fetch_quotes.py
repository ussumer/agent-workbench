#!/usr/bin/env python3
"""Fetch a demo supplier quote page and extract structured offers.

Runs *inside* the sandbox, so it uses the standard library only.

The two demo renderings differ in markup — S001 renders a ``<table>``, S002 renders a
``<ul>`` — but both carry the same machine-readable ``data-*`` attributes. Parsing those
attributes rather than the tag structure is what makes one script handle both, and is why
this survives a restyle that keeps the contract.

Exit codes are distinct so the caller can tell "the page is gone" from "the page changed"
from "the page has no prices", instead of treating them all as one failure:

    0  success
    2  bad usage
    3  HTTP error                (page unreachable or not 200)
    4  structure not found       (no quote container: the page changed shape)
    5  price missing or invalid  (container found, prices unusable)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser

PRICE_PATTERN = re.compile(r"^\d+\.\d{2}$")
PART_ID_PATTERN = re.compile(r"^P\d{3}$")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_HTTP = 3
EXIT_STRUCTURE = 4
EXIT_PRICE = 5


class QuotePageParser(HTMLParser):
    """Extract offers from either rendering by reading the data attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.supplier_id: str | None = None
        self.quoted_at: str | None = None
        self.render_style: str | None = None
        self.offers: list[dict[str, str]] = []
        self.unsupported: list[str] = []
        self._section_stack: list[str] = []
        self._in_quotes = False
        self._in_unsupported = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value for key, value in attrs if value is not None}

        if tag == "section":
            section_id = attributes.get("id", "")
            self._section_stack.append(section_id)
            if section_id == "quotes":
                self._in_quotes = True
                self.render_style = attributes.get("data-render")
                if attributes.get("data-supplier-id"):
                    self.supplier_id = attributes["data-supplier-id"]
            elif section_id == "unsupported":
                self._in_unsupported = True
                if attributes.get("data-unsupported"):
                    self.unsupported.extend(
                        item for item in attributes["data-unsupported"].split(",") if item
                    )
            return

        if attributes.get("data-supplier-id") and self.supplier_id is None:
            self.supplier_id = attributes["data-supplier-id"]
        if attributes.get("data-quoted-at") and self.quoted_at is None:
            self.quoted_at = attributes["data-quoted-at"]

        part_id = attributes.get("data-part-id")
        if part_id and self._in_unsupported and part_id not in self.unsupported:
            self.unsupported.append(part_id)
            return

        if part_id and self._in_quotes and tag in {"tr", "li"}:
            offer = {
                "part_id": part_id,
                "sku": attributes.get("data-sku", ""),
                "currency": attributes.get("data-currency", ""),
            }
            price = attributes.get("data-unit-price")
            if price is not None:
                offer["unit_price"] = price
            self.offers.append(offer)

    def handle_endtag(self, tag: str) -> None:
        if tag == "section" and self._section_stack:
            closed = self._section_stack.pop()
            if closed == "quotes":
                self._in_quotes = False
            elif closed == "unsupported":
                self._in_unsupported = False


def fetch(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "rush-harness-skill/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - demo http
        if response.status != 200:
            print(f"HTTP {response.status} for {url}", file=sys.stderr)
            raise SystemExit(EXIT_HTTP)
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_offers(html_text: str, url: str) -> tuple[QuotePageParser, list[dict[str, str]]]:
    parser = QuotePageParser()
    parser.feed(html_text)

    if not parser.offers and parser.render_style is None:
        print(
            "quote container not found: the page no longer exposes a section#quotes "
            "with a data-render attribute",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_STRUCTURE)

    offers: list[dict[str, str]] = []
    for offer in parser.offers:
        part_id = offer["part_id"]
        if not PART_ID_PATTERN.match(part_id):
            print(f"unexpected part id {part_id!r}", file=sys.stderr)
            raise SystemExit(EXIT_STRUCTURE)
        if "unit_price" not in offer:
            print(
                f"price missing for {part_id}: the page rendered without prices, so no "
                f"usable quote exists at {url}",
                file=sys.stderr,
            )
            raise SystemExit(EXIT_PRICE)
        if not PRICE_PATTERN.match(offer["unit_price"]):
            print(
                f"price for {part_id} is not a two-decimal amount: {offer['unit_price']!r}",
                file=sys.stderr,
            )
            raise SystemExit(EXIT_PRICE)
        offers.append(
            {
                "part_id": part_id,
                "sku": offer["sku"],
                "unit_price": offer["unit_price"],
                "currency": offer["currency"] or "CNY",
            }
        )
    return parser, offers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and parse a demo supplier quote page.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    try:
        html_text = fetch(args.url, args.timeout)
    except urllib.error.HTTPError as failure:
        print(f"HTTP {failure.code} for {args.url}", file=sys.stderr)
        return EXIT_HTTP
    except urllib.error.URLError as failure:
        print(f"cannot reach {args.url}: {failure.reason}", file=sys.stderr)
        return EXIT_HTTP
    except TimeoutError:
        print(f"timed out after {args.timeout}s: {args.url}", file=sys.stderr)
        return EXIT_HTTP

    page, offers = parse_offers(html_text, args.url)

    payload = {
        "supplier_id": page.supplier_id,
        "source_url": args.url,
        "render_style": page.render_style,
        "quoted_at": page.quoted_at,
        "quotes": offers,
        "unsupported_parts": sorted(set(page.unsupported)),
    }

    # Only write on success: a failed run must not leave a half-finished artifact behind.
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"{len(offers)} quotes from {page.supplier_id} written to {args.out}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
