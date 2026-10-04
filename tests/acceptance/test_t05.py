"""T05 acceptance: the demo quote site and the downloadable skill package.

Everything is fetched over real HTTP from a separate process. The suite checks
that both supplier pages carry the seeded prices despite using different DOM
shapes, that a missing supply relation is distinguishable from unparsable prices,
and that the published skill archive really runs and produces the fixed 1533.00
total.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import loader, site_service  # noqa: E402

pytestmark = pytest.mark.integration

SKILL_SLUG = "reorder-cost-summary"
ARCHIVE_NAME = f"{SKILL_SLUG}-v1.zip"
DEMO_MARKER = "演示数据"
MONEY_RE = re.compile(r"^[0-9]{1,6}\.[0-9]{2}$")


class QuotePageParser(HTMLParser):
    """Minimal DOM reader: collects quote rows by data attributes, not by layout."""

    def __init__(self) -> None:
        super().__init__()
        self.quotes: list[dict] = []
        self.render_style: str | None = None
        self.unsupported: list[str] = []
        self.quoted_at: str | None = None
        self.supplier_id: str | None = None

    def handle_starttag(self, tag, attrs):  # noqa: ANN001 - stdlib signature
        attributes = dict(attrs)

        if attributes.get("id") == "quotes":
            self.render_style = attributes.get("data-render")
            self.supplier_id = attributes.get("data-supplier-id")

        if attributes.get("id") == "unsupported":
            raw = attributes.get("data-unsupported") or ""
            self.unsupported = [item for item in raw.split(",") if item]

        if attributes.get("class") == "quoted-at" or "data-quoted-at" in attributes:
            self.quoted_at = attributes.get("data-quoted-at")

        # A quote is an element that carries both an id and a price. The unsupported list uses
        # data-part-id alone, so it can never be mistaken for an offer.
        if attributes.get("data-part-id") and "data-unit-price" in attributes:
            self.quotes.append(
                {
                    "part_id": attributes["data-part-id"],
                    "sku": attributes.get("data-sku"),
                    "currency": attributes.get("data-currency"),
                    "unit_price": attributes["data-unit-price"],
                }
            )


def parse_quotes(html: str) -> QuotePageParser:
    parser = QuotePageParser()
    parser.feed(html)
    return parser


def get_text(site: site_service.FixtureSite, path: str) -> httpx.Response:
    with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0), trust_env=False) as http:
        return http.get(f"{site.base_url}{path}")


def fetch_archive(site: site_service.FixtureSite) -> tuple[bytes, str]:
    response = get_text(site, f"/skills/{ARCHIVE_NAME}")
    assert response.status_code == 200
    payload = response.content
    return payload, hashlib.sha256(payload).hexdigest()


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    with site_service.running_site(tmp_path_factory.mktemp("t05-site"), test_mode=True) as running:
        yield running


@pytest.fixture(scope="module")
def published_site(tmp_path_factory):
    """The site without test-only failure endpoints."""
    with site_service.running_site(
        tmp_path_factory.mktemp("t05-published"), test_mode=False
    ) as running:
        yield running


# --------------------------------------------------------------------------- #
# site basics
# --------------------------------------------------------------------------- #


def test_health_identifies_the_demo_service(site):
    payload = get_text(site, "/health").json()
    assert payload["status"] == "ok"
    assert payload["service"] == "fixtures-site"
    assert payload["demo_data"] is True


def test_s001_quotes_are_rendered_as_a_table(site):
    response = get_text(site, "/suppliers/S001/quotes")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

    page = parse_quotes(response.text)
    assert page.render_style == "table"
    assert DEMO_MARKER in response.text, "演示数据必须显式标注"
    assert page.quoted_at == "2026-09-16T00:00:00Z"
    assert len(page.quotes) == 4


def test_s002_quotes_are_rendered_as_a_list(site):
    response = get_text(site, "/suppliers/S002/quotes")
    page = parse_quotes(response.text)

    assert page.render_style == "list", "两家供应商必须使用不同 DOM 形状"
    assert DEMO_MARKER in response.text
    assert len(page.quotes) == 3


def test_both_pages_carry_exactly_the_seeded_prices(site):
    seed = loader.seed()
    expected = {
        (row["supplier_id"], row["part_id"]): row["catalog_price"]
        for row in seed["supplier_parts"]
    }

    for supplier_id in ("S001", "S002"):
        page = parse_quotes(get_text(site, f"/suppliers/{supplier_id}/quotes").text)
        for quote in page.quotes:
            assert quote["currency"] == "CNY"
            assert MONEY_RE.match(quote["unit_price"]), quote
            assert quote["unit_price"] == expected[(supplier_id, quote["part_id"])], quote


def test_unavailable_parts_are_stated_explicitly(site):
    """A missing supply relation must be visible, not simply absent."""
    page = parse_quotes(get_text(site, "/suppliers/S002/quotes").text)

    assert page.unsupported == ["P003", "P005"]
    assert "P003" not in [quote["part_id"] for quote in page.quotes]

    s001 = parse_quotes(get_text(site, "/suppliers/S001/quotes").text)
    assert s001.unsupported == ["P005"]


def test_an_unknown_supplier_is_a_404(site):
    assert get_text(site, "/suppliers/S999/quotes").status_code == 404


def test_lowest_price_total_matches_the_fixed_expectation(site):
    """The 'cheapest active supplier' figure of 2553.00, computed from the live pages."""
    warning = loader.expected()["inventory_warning"]
    seed = loader.seed()
    active = {row["supplier_id"] for row in seed["suppliers"] if row["active"]}

    cheapest: dict[str, str] = {}
    for supplier_id in sorted(active):
        for quote in parse_quotes(get_text(site, f"/suppliers/{supplier_id}/quotes").text).quotes:
            part_id = quote["part_id"]
            if part_id not in warning["part_ids"]:
                continue
            if part_id not in cheapest or quote["unit_price"] < cheapest[part_id]:
                cheapest[part_id] = quote["unit_price"]

    total = sum(
        (
            as_decimal(cheapest[part_id]) * warning["suggested_quantity"][part_id]
            for part_id in cheapest
        ),
        Decimal("0.00"),
    )
    assert f"{total:.2f}" == "2553.00"
    assert set(cheapest) == set(warning["part_ids"])


def as_decimal(money: str) -> Decimal:
    """Test-side arithmetic only; the ERP itself uses Java BigDecimal."""
    return Decimal(money)


# --------------------------------------------------------------------------- #
# skill catalogue and archive
# --------------------------------------------------------------------------- #


def test_the_catalog_marks_itself_as_demo_data(site):
    catalog = get_text(site, "/skills/catalog.json").json()

    assert DEMO_MARKER in catalog["note"]
    entry = catalog["skills"][0]
    assert entry["slug"] == SKILL_SLUG
    assert entry["version"] == "1.0.0"
    assert entry["url"] == f"/skills/{ARCHIVE_NAME}"
    assert len(entry["sha256"]) == 64
    assert entry["size"] > 0


def test_the_published_archive_hash_matches_the_catalog(site):
    catalog = get_text(site, "/skills/catalog.json").json()["skills"][0]
    payload, digest = fetch_archive(site)

    assert digest == catalog["sha256"]
    assert len(payload) == catalog["size"]


def test_the_archive_is_byte_identical_across_processes(site, published_site):
    """Reproducibility across independent server processes, not just within one."""
    _, first = fetch_archive(site)
    _, second = fetch_archive(published_site)

    assert first == second, "技能包必须可复现，否则内容 hash 无法作为校验依据"


def test_the_archive_contains_frontmatter_script_and_examples(site):
    payload, _ = fetch_archive(site)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = sorted(archive.namelist())
        assert names == ["SKILL.md", "examples/input.json", "scripts/summarise.py"]

        skill_md = archive.read("SKILL.md").decode("utf-8")
        assert skill_md.startswith("---"), "SKILL.md 必须带 YAML frontmatter"
        frontmatter = skill_md.split("---")[1]
        assert "name: reorder-cost-summary" in frontmatter
        assert "description:" in frontmatter


def test_the_archive_has_no_unsafe_members(site):
    payload, _ = fetch_archive(site)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            assert not info.filename.startswith("/"), info.filename
            assert ".." not in Path(info.filename).parts, info.filename
            assert not info.is_dir()


def test_the_archive_header_is_platform_independent(site):
    """Pin the mechanism behind reproducibility.

    ``zipfile`` records the creating OS in ``create_system``; left at its default,
    a Windows build and a Linux build of identical sources would produce different
    bytes and the catalog hash would not survive a container deploy.
    """
    payload, _ = fetch_archive(site)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        infos = archive.infolist()

    assert [info.filename for info in infos] == sorted(info.filename for info in infos)
    for info in infos:
        assert info.date_time == (1980, 1, 1, 0, 0, 0), info.filename
        assert info.create_system == 3, f"{info.filename}: create_system 必须固定为 Unix"
        assert info.external_attr == 0o644 << 16, info.filename
        assert info.compress_type == zipfile.ZIP_DEFLATED, info.filename


def test_the_extracted_skill_script_produces_the_fixed_total(site, tmp_path: Path):
    """Download, verify, extract and actually run the packaged skill."""
    payload, _ = fetch_archive(site)
    skill_dir = extract_archive(payload, tmp_path / "skill")
    out_dir = tmp_path / "out"

    completed = subprocess.run(
        [
            sys.executable,
            str(skill_dir / "scripts" / "summarise.py"),
            "--input",
            str(skill_dir / "examples" / "input.json"),
            "--out-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "1533.00"

    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["total_amount"] == "1533.00"
    assert summary["currency"] == "CNY"
    assert [line["part_id"] for line in summary["lines"]] == ["P001", "P004"]
    assert (out_dir / "report.md").is_file()
    assert "1533.00" in (out_dir / "report.md").read_text(encoding="utf-8")


def test_the_skill_script_rejects_a_bad_input(site, tmp_path: Path):
    payload, _ = fetch_archive(site)
    skill_dir = extract_archive(payload, tmp_path / "skill-bad")
    bad_input = tmp_path / "bad.json"
    bad_input.write_text(
        json.dumps(
            {
                "currency": "CNY",
                "lines": [
                    {
                        "part_id": "P001",
                        "supplier_id": "S002",
                        "quantity": 42,
                        "unit_price": "24.005",
                        "currency": "CNY",
                        "source_url": "http://localhost:8088/suppliers/S002/quotes",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(skill_dir / "scripts" / "summarise.py"),
            "--input",
            str(bad_input),
            "--out-dir",
            str(tmp_path / "bad-out"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode != 0, "三位小数必须被拒绝，而不是被四舍五入"
    assert "invalid input" in completed.stderr


def extract_archive(payload: bytes, target: Path) -> Path:
    """Extract an archive after rejecting absolute paths and traversal."""
    target.mkdir(parents=True, exist_ok=True)
    resolved_root = target.resolve()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            member = Path(info.filename)
            assert not member.is_absolute(), info.filename
            assert ".." not in member.parts, info.filename
            destination = (resolved_root / member).resolve()
            assert destination.is_relative_to(resolved_root), info.filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info))
    return target


# --------------------------------------------------------------------------- #
# failure fixtures exist only in test mode
# --------------------------------------------------------------------------- #


def test_test_mode_exposes_the_injected_failure_fixtures(site):
    assert get_text(site, "/test/error").status_code == 500

    slow = get_text(site, "/test/slow?seconds=0.1")
    assert slow.status_code == 200

    without_prices = get_text(site, "/test/suppliers/S001/quotes/no-prices")
    assert without_prices.status_code == 200
    page = parse_quotes(without_prices.text)
    assert page.quotes == [], "无价格页面不得暴露任何单价"
    assert page.unsupported == ["P005"], "页面本身仍然可解析，与报价缺失区分开"


def test_a_broken_archive_fails_a_hash_check(site):
    response = get_text(site, "/test/skills/broken.zip")
    assert response.status_code == 200
    digest = hashlib.sha256(response.content).hexdigest()

    catalog = get_text(site, "/skills/catalog.json").json()["skills"][0]
    assert digest != catalog["sha256"], "坏包不得与目录中的内容 hash 相同"
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(io.BytesIO(response.content))


def test_the_published_site_has_no_failure_endpoints(published_site):
    """The normal site must never fail at random."""
    assert get_text(published_site, "/test/error").status_code == 404
    assert get_text(published_site, "/test/skills/broken.zip").status_code == 404
    assert get_text(published_site, "/test/suppliers/S001/quotes/no-prices").status_code == 404

    assert get_text(published_site, "/suppliers/S001/quotes").status_code == 200
    assert get_text(published_site, "/skills/catalog.json").status_code == 200
