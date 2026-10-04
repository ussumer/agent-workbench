"""T16 acceptance: preset fetch/analysis skills and downloadable reports.

Everything here runs against real services — a real OpenSandbox container, the real quote
site over HTTP, real MongoDB, the real chart endpoint. The model double is the only stand-in,
and only where a test needs one.

The properties under test:

* the shipped skills are complete and the scripts they name actually exist, so an agent
  following the SKILL.md cannot be following a stale path;
* both renderings parse, quotes carry their source URL and quote time, and a page that
  cannot be parsed fails **by name** instead of being papered over with a catalogue price;
* the comparison is what the ERP contract says it is: lowest unit price per part and
  currency, ties broken by ``supplier_id``, every amount computed in ``Decimal``;
* a warned part with no offer stays missing — it is warned about and excluded, never filled
  in — which is the difference between a report and a plausible-looking guess;
* the artifact round trip is honest: what is downloaded hashes to what was advertised, a
  foreign owner gets 404, and a registry row whose bytes are gone yields 410 rather than a
  zero-byte "success".
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.approval.service import ApprovalService  # noqa: E402
from agent.approval.store import MongoPendingActionStore  # noqa: E402
from agent.artifacts.service import ArtifactService  # noqa: E402
from agent.artifacts.store import MongoArtifactStore  # noqa: E402
from agent.async_tasks.service import build_async_task_service  # noqa: E402
from agent.env_utils import load_env  # noqa: E402
from agent.middlewares.skills_sync import SkillsSyncMiddleware, load_manifest  # noqa: E402
from agent.tools.download_sandbox_file import (  # noqa: E402
    build_download_sandbox_file_tool,
    check_path,
)
from api_view.api.artifacts import build_artifacts_router  # noqa: E402
from api_view.api.deps import WebContext  # noqa: E402
from api_view.run_registry import RunRegistry  # noqa: E402
from fixtures import mongo_service, sandbox_service, site_service  # noqa: E402

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PROCUREMENT = REPO_ROOT / "src" / "skills" / "procurement"

WEB_SCRAPER_SCRIPT = "web-scraper/scripts/fetch_quotes.py"
WEB_FETCHER_SCRIPT = "web-content-fetcher/scripts/fetch_page.py"
REPORT_SCRIPT = "procurement-analysis/scripts/build_report.py"
REPORT_TEMPLATE = "procurement-analysis/report-template.md"

OWNER_A = "demo-a"
OWNER_B = "demo-b"
THREAD_ID = "t16-thread"
GRANT_SECRET = "t16-grant-secret"

SCRATCH = "/workspace/scratch"
REPORT_DIR = "/workspace/report"

#: From docs/plan/contracts/erp.md: the warning set is exactly P001/P003/P004 with
#: suggestions 42/15/30. Written as the ERP's own payload shape, envelope and all.
WARNINGS = {
    "ok": True,
    "data": {
        "items": [
            {
                "part_id": "P001",
                "on_hand": 8,
                "warning_threshold": 20,
                "target_stock": 50,
                "suggested_quantity": 42,
                "currency": "CNY",
            },
            {
                "part_id": "P003",
                "on_hand": 5,
                "warning_threshold": 10,
                "target_stock": 20,
                "suggested_quantity": 15,
                "currency": "CNY",
            },
            {
                "part_id": "P004",
                "on_hand": 0,
                "warning_threshold": 10,
                "target_stock": 30,
                "suggested_quantity": 30,
                "currency": "CNY",
            },
        ],
        "total": 3,
        "page": 1,
        "page_size": 20,
    },
}

#: From fixtures/expected-v1.json → quote_comparison.
EXPECTED_LINES = {
    "P001": ("S002", "24.00", "1008.00"),
    "P003": ("S001", "68.00", "1020.00"),
    "P004": ("S002", "17.50", "525.00"),
}
EXPECTED_TOTAL = "2553.00"

#: Paths every URL in the report must be rewritten to when the fetch ran in a container.
SANDBOX_HOST = sandbox_service.SANDBOX_HOST_ALIAS


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def run_script(script: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a shipped skill script exactly as the sandbox would: same argv, same interpreter."""
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
    )


def write_warnings(path: Path, items: list[dict] | None = None) -> Path:
    payload = WARNINGS if items is None else {"ok": True, "data": {"items": items, "total": len(items)}}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def fetch_quotes_into(work: Path, base_url: str, suppliers: tuple[str, ...]) -> list[Path]:
    """Fetch the given suppliers with the shipped script. Fails the test if any call fails."""
    written: list[Path] = []
    for supplier_id in suppliers:
        out = work / f"quotes-{supplier_id}.json"
        result = run_script(
            PROCUREMENT / WEB_SCRAPER_SCRIPT,
            "--url",
            f"{base_url}/suppliers/{supplier_id}/quotes",
            "--out",
            str(out),
        )
        assert result.returncode == 0, f"{supplier_id}: {result.stderr}"
        written.append(out)
    return written


def build_report(
    work: Path,
    quote_files: list[Path],
    *,
    warnings: Path | None = None,
    extra: tuple[str, ...] = ("--skip-supplier", "S003"),
) -> tuple[subprocess.CompletedProcess, dict]:
    md = work / "report.md"
    csv = work / "report.csv"
    chart = work / "chart.json"
    result = run_script(
        PROCUREMENT / REPORT_SCRIPT,
        "--warnings",
        str(warnings or write_warnings(work / "warnings.json")),
        "--quotes",
        *[str(item) for item in quote_files],
        *extra,
        "--template",
        str(PROCUREMENT / REPORT_TEMPLATE),
        "--out-md",
        str(md),
        "--out-csv",
        str(csv),
        "--out-chart-data",
        str(chart),
    )
    if result.returncode != 0:
        return result, {}
    return result, {
        "summary": json.loads(result.stdout),
        "markdown": md.read_text(encoding="utf-8"),
        "csv": csv.read_text(encoding="utf-8"),
        "chart": json.loads(chart.read_text(encoding="utf-8")),
    }


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def settings():
    return mongo_service.unique_settings(f"t16-{uuid.uuid4().hex[:8]}")


@pytest.fixture(scope="module")
def database(settings):
    """A private database with the application's real indexes, dropped afterwards."""
    from pymongo import MongoClient

    from agent.persistence.indexes import (
        drop_undeclared_application_indexes,
        ensure_application_indexes,
    )

    mongo_service.require_reachable(settings)
    with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True) as client:
        db = client[settings.database]
        drop_undeclared_application_indexes(db)
        ensure_application_indexes(db)
        yield db
    mongo_service.drop_test_database(settings)


@pytest.fixture(scope="module")
def artifacts(database):
    return ArtifactService(store=MongoArtifactStore(database))


@pytest.fixture(scope="module")
def app(database, artifacts, settings):
    """The real application, built the way production builds it.

    Using ``create_app`` rather than mounting the router by hand is the point: it is what
    proves the artifact routes are actually wired into the app, and it puts the whole route
    table in one place where a duplicate path would show up.
    """
    from api_view.web_main import create_app

    context = WebContext(
        resources=type("Resources", (), {"database": database, "settings": settings})(),
        repository=None,
        registry=RunRegistry(),
        approvals=ApprovalService(
            store=MongoPendingActionStore(database), grant_secret=GRANT_SECRET
        ),
        artifacts=artifacts,
        async_tasks=build_async_task_service(database),
        settings=settings,
        graph_provider=lambda owner: None,
    )
    return create_app(context=context)


@pytest.fixture(scope="module")
def client(app):
    from starlette.testclient import TestClient

    return TestClient(app)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """The real quote site, bound so a container can reach it.

    ``host="0.0.0.0"`` is the difference between a test that proves the sandbox fetched over
    HTTP and one that only proves the host did.
    """
    with site_service.running_site(tmp_path_factory.mktemp("t16-site"), host="0.0.0.0") as running:
        yield running


@pytest.fixture(scope="module")
def sandbox_url(site) -> str:
    return sandbox_service.sandbox_url_for_host_config(site.base_url)


@pytest.fixture(scope="module")
def sandbox():
    with sandbox_service.running_backend() as (backend, _report):
        yield backend


@pytest.fixture(scope="module")
def skills_sandbox(sandbox):
    """A sandbox with the preset skills synced, exactly as the application does it.

    Using the real middleware rather than uploading by hand is what makes the paths in the
    SKILL.md assertions meaningful: if a script were not in the manifest it would not be
    there, and the documented command would fail.
    """
    report = SkillsSyncMiddleware(backend_provider=lambda: sandbox).sync(generation=1)
    assert report.uploaded, "预置技能应当被同步进沙箱"
    return sandbox


@pytest.fixture(scope="module")
def env() -> dict[str, str]:
    resolved = load_env()
    assert resolved.get("MODELSCOPE_MCP_URL"), "MODELSCOPE_MCP_URL 未配置"
    assert resolved.get("MODELSCOPE_API_TOKEN"), "MODELSCOPE_API_TOKEN 未配置"
    return resolved


# --------------------------------------------------------------------------- #
# the skills themselves
# --------------------------------------------------------------------------- #


def test_every_preset_skill_is_complete_and_mountable():
    """A skill that is missing its frontmatter would fail the whole manifest, not just itself."""
    manifest = load_manifest()
    by_name = {skill.name: skill for skill in manifest.skills}

    for expected in (
        "procurement-analysis",
        "supplier-price-urls",
        "web-scraper",
        "web-content-fetcher",
    ):
        assert expected in by_name, f"{expected} 不在预置技能里"

    for skill in manifest.skills:
        assert skill.description.strip(), f"{skill.name} 缺少 description"


def test_the_skills_ship_the_scripts_their_own_text_tells_the_agent_to_run():
    """The documented command and the shipped file must not drift apart.

    This is the failure mode that matters: an agent reads the SKILL.md, runs the path it
    names, and gets "No such file". The skill then looks present while being unusable.
    """
    documented = {
        "web-scraper": WEB_SCRAPER_SCRIPT,
        "web-content-fetcher": WEB_FETCHER_SCRIPT,
        "procurement-analysis": REPORT_SCRIPT,
    }
    for skill_name, relative in documented.items():
        body = (PROCUREMENT / skill_name / "SKILL.md").read_text(encoding="utf-8")
        filename = Path(relative).name
        assert filename in body, f"{skill_name}/SKILL.md 没有提到 {filename}"
        assert (PROCUREMENT / relative).is_file(), f"{relative} 不存在"

    analysis = (PROCUREMENT / "procurement-analysis" / "SKILL.md").read_text(encoding="utf-8")
    assert "build_report.py" in analysis
    assert (PROCUREMENT / REPORT_TEMPLATE).is_file()


def test_the_analysis_skill_forbids_computing_money_itself():
    """The instruction that keeps a model out of the arithmetic has to be in the skill."""
    body = (PROCUREMENT / "procurement-analysis" / "SKILL.md").read_text(encoding="utf-8")

    assert "不要自己心算" in body or "不要心算" in body
    assert "Decimal" in body
    # And the gap rule, in the skill rather than only in the script.
    assert "目录价" in body


def test_every_documented_placeholder_is_actually_substituted():
    """The template is a template, not documentation with examples in it.

    An earlier version of this file documented its placeholders in a table inside itself, and
    a blind ``str.replace`` then filled the documentation rows with values — producing a
    report whose own instructions had been overwritten. The template is now pure template.
    """
    template = (PROCUREMENT / REPORT_TEMPLATE).read_text(encoding="utf-8")
    body = (PROCUREMENT / "procurement-analysis" / "SKILL.md").read_text(encoding="utf-8")

    placeholders = {
        "generated_at",
        "line_count",
        "sources_table",
        "lines_table",
        "total_amount",
        "currency",
        "derivation",
        "warnings",
    }
    for name in placeholders:
        assert "{{" + name + "}}" in template, f"模板缺少 {{{{{name}}}}}"
        # Documented where documentation belongs — in the skill, not in the template.
        assert name in body, f"SKILL.md 未说明占位符 {name}"


# --------------------------------------------------------------------------- #
# fetching: both renderings, real HTTP, honest failures
# --------------------------------------------------------------------------- #


def test_both_renderings_parse_and_carry_their_source_and_quote_time(tmp_path, site):
    """S001 is a table and S002 is a list; one parser reads both, and both stay traceable."""
    files = fetch_quotes_into(tmp_path, site.base_url, ("S001", "S002"))
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in files]

    styles = {payload["supplier_id"]: payload["render_style"] for payload in payloads}
    assert styles == {"S001": "table", "S002": "list"}, (
        "两种 DOM 应各用其渲染方式，且都要能解析"
    )

    for payload in payloads:
        assert payload["source_url"].startswith("http")
        assert payload["quoted_at"], "缺少报价时间就无法证明数据来自页面"
        assert payload["quotes"], f"{payload['supplier_id']} 没有解析出报价"
        for quote in payload["quotes"]:
            assert quote["unit_price"].count(".") == 1
            assert len(quote["unit_price"].split(".")[1]) == 2, quote["unit_price"]


def test_a_page_without_prices_fails_by_name_and_leaves_no_half_file(tmp_path, site):
    """`缺报价要指名来源失败` — a failed scrape must not leave something that looks like data."""
    out = tmp_path / "never-written.json"

    result = run_script(
        PROCUREMENT / WEB_SCRAPER_SCRIPT,
        "--url",
        f"{site.base_url}/test/suppliers/S001/quotes/no-prices",
        "--out",
        str(out),
    )

    assert result.returncode == 5, result.stderr
    assert "P001" in result.stderr or "price" in result.stderr.lower()
    assert not out.exists(), "失败的抓取不得留下半成品文件"


def test_an_unreachable_page_is_reported_as_http_not_as_empty_quotes(tmp_path):
    result = run_script(
        PROCUREMENT / WEB_SCRAPER_SCRIPT,
        "--url",
        "http://127.0.0.1:1/nothing-here",
        "--out",
        str(tmp_path / "x.json"),
    )

    assert result.returncode == 3
    assert not (tmp_path / "x.json").exists()


def test_the_markdown_converter_writes_provenance_into_the_file(tmp_path, site):
    """The source line is written by the process that made the request, not by the model."""
    out = tmp_path / "page.md"
    result = run_script(
        PROCUREMENT / WEB_FETCHER_SCRIPT,
        "--url",
        f"{site.base_url}/suppliers/S001/quotes",
        "--max-chars",
        "4000",
        "--out",
        str(out),
    )

    assert result.returncode == 0, result.stderr
    body = out.read_text(encoding="utf-8")
    assert "来源：" in body
    assert site.base_url in body
    # Page text survived the conversion, and script/style content did not.
    assert "P001" in body
    assert "<section" not in body


def test_the_markdown_converter_marks_truncation_instead_of_dropping_it(tmp_path, site):
    out = tmp_path / "short.md"
    result = run_script(
        PROCUREMENT / WEB_FETCHER_SCRIPT,
        "--url",
        f"{site.base_url}/suppliers/S001/quotes",
        "--max-chars",
        "300",
        "--out",
        str(out),
    )

    assert result.returncode == 0, result.stderr
    assert "已截断" in out.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# the comparison
# --------------------------------------------------------------------------- #


def test_the_total_and_every_line_match_the_contract(tmp_path, site):
    """The headline number, computed in Decimal by the script rather than reasoned by a model."""
    work = tmp_path
    files = fetch_quotes_into(work, site.base_url, ("S001", "S002"))

    result, built = build_report(work, files)

    assert result.returncode == 0, result.stderr
    summary = built["summary"]
    assert summary["total_amount"] == EXPECTED_TOTAL
    assert summary["currency"] == "CNY"

    lines = {line["part_id"]: line for line in summary["lines"]}
    assert set(lines) == set(EXPECTED_LINES)
    for part_id, (supplier_id, unit_price, amount) in EXPECTED_LINES.items():
        found = lines[part_id]
        assert (found["supplier_id"], found["unit_price"], found["amount"]) == (
            supplier_id,
            unit_price,
            amount,
        ), part_id

    # The arithmetic is checkable from the report itself, and it is Decimal arithmetic.
    recomputed = sum(
        (Decimal(line["unit_price"]) * line["quantity"] for line in summary["lines"]),
        Decimal("0.00"),
    )
    assert f"{recomputed:.2f}" == EXPECTED_TOTAL

    # P001 exists at two suppliers; the cheaper one must be the one recommended.
    assert lines["P001"]["unit_price"] == "24.00", "P001 应取 S002 的 24.00 而非 S001 的 25.50"


def test_every_recommendation_carries_the_page_it_came_from(tmp_path, site):
    """`trace 证明从 HTTP 抓取` — a price without a source page and a quote time is a rumour."""
    work = tmp_path
    files = fetch_quotes_into(work, site.base_url, ("S001", "S002"))

    result, built = build_report(work, files)
    assert result.returncode == 0, result.stderr

    for line in built["summary"]["lines"]:
        assert line["source_url"].startswith(site.base_url), line
        assert line["quoted_at"], line

    markdown = built["markdown"]
    assert "## 1. 抓取来源" in markdown
    assert "HTTP 抓取" in markdown
    for line in built["summary"]["lines"]:
        assert line["source_url"] in markdown
        assert line["quoted_at"] in markdown


def test_the_csv_and_chart_data_come_from_the_same_lines(tmp_path, site):
    """Three outputs, one computation. A CSV that disagreed with the Markdown would be worse
    than not shipping one."""
    work = tmp_path
    files = fetch_quotes_into(work, site.base_url, ("S001", "S002"))

    result, built = build_report(work, files)
    assert result.returncode == 0, result.stderr

    rows = built["csv"].strip().splitlines()
    assert rows[0] == (
        "part_id,quantity,supplier_id,unit_price,currency,amount,source_url,quoted_at"
    )
    assert len(rows) - 1 == len(built["summary"]["lines"])

    chart = built["chart"]
    assert [row["label"].split()[0] for row in chart] == sorted(EXPECTED_LINES)
    assert sum(Decimal(str(row["value"])) for row in chart) == Decimal(EXPECTED_TOTAL)
    for row in chart:
        assert set(row) == {"label", "value"}, "图表数据必须是 chart_generator 认的 {label,value}"


def test_a_part_with_no_offer_is_warned_and_excluded_not_priced(tmp_path, site):
    """The rule the whole task turns on: a gap stays a gap.

    P005 is warned about but no page quotes it. Filling it with a catalogue price would
    produce a total that looks authoritative and is wrong; the script must instead name the
    gap and leave the number out.
    """
    work = tmp_path
    files = fetch_quotes_into(work, site.base_url, ("S001", "S002"))
    warnings = write_warnings(
        work / "warnings.json",
        [
            {"part_id": "P001", "suggested_quantity": 42},
            {"part_id": "P005", "suggested_quantity": 7},
        ],
    )

    result, built = build_report(work, files, warnings=warnings)

    assert result.returncode == 0, result.stderr
    summary = built["summary"]
    assert [line["part_id"] for line in summary["lines"]] == ["P001"]
    assert summary["total_amount"] == "1008.00"

    joined = " ".join(summary["warnings"])
    assert "P005" in joined
    assert "未计入" in joined or "没有" in joined
    # The catalogue price for P005 must not appear anywhere as if it were an offer.
    assert "P005" not in built["csv"]
    assert "P005" in built["markdown"], "缺口必须在报告里写明"


def test_the_inactive_supplier_is_excluded_even_when_its_price_is_lower(tmp_path, site):
    """S003 cannot be ordered from, so recommending it would be advice nobody can act on.

    The exclusion is tested by making it *matter*: a copy of a real page is relabelled S003
    and every price halved. If ``--skip-supplier`` were decorative, S003 would win all three
    lines and the total would collapse. It has to still be 2553.00.
    """
    work = tmp_path
    files = fetch_quotes_into(work, site.base_url, ("S001", "S002"))

    payload = json.loads(files[0].read_text(encoding="utf-8"))
    payload["supplier_id"] = "S003"
    payload["source_url"] = payload["source_url"].replace("/S001/", "/S003/")
    for quote in payload["quotes"]:
        quote["unit_price"] = f"{Decimal(quote['unit_price']) / 2:.2f}"
    cheaper_inactive = work / "quotes-S003.json"
    cheaper_inactive.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result, built = build_report(work, [*files, cheaper_inactive])
    assert result.returncode == 0, result.stderr

    assert all(line["supplier_id"] != "S003" for line in built["summary"]["lines"])
    assert built["summary"]["total_amount"] == EXPECTED_TOTAL, (
        "停用供应商的半价不得影响比价结果"
    )
    assert any("S003" in warning for warning in built["summary"]["warnings"]), (
        "排除了一家供应商却不说明，读者无法判断这个最低价是否成立"
    )


def test_an_excluded_supplier_that_was_never_fetched_is_still_recorded(tmp_path, site):
    """The exclusion is a fact about the comparison, not about what happened to be fetched."""
    work = tmp_path
    files = fetch_quotes_into(work, site.base_url, ("S001", "S002"))

    result, built = build_report(work, files)
    assert result.returncode == 0, result.stderr

    recorded = [warning for warning in built["summary"]["warnings"] if "S003" in warning]
    assert recorded, built["summary"]["warnings"]


def test_a_quote_file_without_its_source_is_refused_rather_than_filled_in(tmp_path, site):
    """`不按目录价冒充抓取价` starts here: a payload with no provenance is not a quote."""
    work = tmp_path
    files = fetch_quotes_into(work, site.base_url, ("S001",))
    stripped = json.loads(files[0].read_text(encoding="utf-8"))
    stripped.pop("source_url")
    broken = work / "quotes-broken.json"
    broken.write_text(json.dumps(stripped, ensure_ascii=False), encoding="utf-8")

    result, _ = build_report(work, [broken])

    assert result.returncode == 4
    assert "source_url" in result.stderr


def test_a_page_quoting_one_part_twice_with_different_prices_is_refused(tmp_path, site):
    """Averaging or picking one silently would invent a price the page never stated."""
    work = tmp_path
    files = fetch_quotes_into(work, site.base_url, ("S001",))
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    payload["quotes"].append({**payload["quotes"][0], "unit_price": "99.99"})
    contradictory = work / "quotes-contradictory.json"
    contradictory.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result, _ = build_report(work, [contradictory])

    assert result.returncode == 4
    assert "twice" in result.stderr or "different" in result.stderr


# --------------------------------------------------------------------------- #
# inside the sandbox: the real path an agent takes
# --------------------------------------------------------------------------- #


def test_the_report_is_produced_inside_the_sandbox_from_a_real_http_fetch(
    skills_sandbox, site, sandbox_url
):
    """The end-to-end claim, run in the container rather than on the host.

    Every URL in the resulting report is the ``host.docker.internal`` address, which is only
    reachable from inside the container — so the presence of that address is itself the proof
    that the sandbox fetched the pages over HTTP, and not that the host looked something up.
    """
    backend = skills_sandbox
    assert backend.upload_files(
        [(f"{SCRATCH}/warnings.json", json.dumps(WARNINGS, ensure_ascii=False).encode())]
    )[0].error is None

    fetched = backend.execute(
        "set -e\n"
        f"mkdir -p {SCRATCH}\n"
        f"python3 /skills/procurement/{WEB_SCRAPER_SCRIPT} "
        f"--url {sandbox_url}/suppliers/S001/quotes --out {SCRATCH}/quotes-S001.json\n"
        f"python3 /skills/procurement/{WEB_SCRAPER_SCRIPT} "
        f"--url {sandbox_url}/suppliers/S002/quotes --out {SCRATCH}/quotes-S002.json\n"
        "echo fetch-done\n",
        timeout=180,
    )
    assert fetched.exit_code == 0, fetched.output
    assert "fetch-done" in fetched.output

    reported = backend.execute(
        f"python3 /skills/procurement/{REPORT_SCRIPT} "
        f"--warnings {SCRATCH}/warnings.json "
        f"--quotes {SCRATCH}/quotes-S001.json {SCRATCH}/quotes-S002.json "
        "--skip-supplier S003 "
        f"--template /skills/procurement/{REPORT_TEMPLATE} "
        f"--out-md {REPORT_DIR}/reorder-report.md "
        f"--out-csv {REPORT_DIR}/reorder-lines.csv "
        f"--out-chart-data {REPORT_DIR}/reorder-chart.json\n",
        timeout=180,
    )
    assert reported.exit_code == 0, reported.output

    summary = json.loads(reported.output[reported.output.index("{") : reported.output.rindex("}") + 1])
    assert summary["total_amount"] == EXPECTED_TOTAL
    for line in summary["lines"]:
        assert line["source_url"].startswith(f"http://{SANDBOX_HOST}:"), (
            f"报告里的来源地址应是沙箱自己抓取的地址：{line['source_url']}"
        )
        assert line["quoted_at"]

    listing = backend.execute(f"ls -1 {REPORT_DIR}")
    assert listing.exit_code == 0
    for name in ("reorder-report.md", "reorder-lines.csv", "reorder-chart.json"):
        assert name in listing.output, f"{name} 未在沙箱内生成"


def test_the_sandbox_report_round_trips_into_a_verifiable_download(
    skills_sandbox, site, sandbox_url, artifacts, client
):
    """Sandbox → artifact → HTTP download, with the digest checked at the end."""
    backend = skills_sandbox
    tool = build_download_sandbox_file_tool(backend=backend, artifacts=artifacts)
    scope = {"configurable": {"owner_user_id": OWNER_A, "thread_id": THREAD_ID}}

    exported = json.loads(
        tool.invoke({"path": f"{REPORT_DIR}/reorder-report.md"}, config=scope)
    )
    assert exported["ok"] is True, exported
    artifact_id = exported["data"]["artifact_id"]

    metadata = client.get(f"/api/artifacts/{artifact_id}", headers={"X-Demo-User": OWNER_A})
    assert metadata.status_code == 200
    advertised = metadata.json()["data"]["sha256"]

    downloaded = client.get(
        f"/api/artifacts/{artifact_id}/content", headers={"X-Demo-User": OWNER_A}
    )
    assert downloaded.status_code == 200
    assert hashlib.sha256(downloaded.content).hexdigest() == advertised, (
        "下载内容与登记的 hash 不一致，这个链接就不是可核对的"
    )
    assert downloaded.headers["x-content-sha256"] == advertised
    assert EXPECTED_TOTAL.encode() in downloaded.content
    # The sandbox-only address is in the delivered file: proof the fetch happened in there.
    assert SANDBOX_HOST.encode() in downloaded.content

    # And the same bytes are still in the sandbox, so the copy is a copy.
    original = backend.download_files([f"{REPORT_DIR}/reorder-report.md"])[0]
    assert original.error is None
    assert hashlib.sha256(original.content).hexdigest() == advertised


def test_the_export_tool_reads_the_skipped_file_and_refuses_to_invent_it(
    skills_sandbox, artifacts
):
    """A missing file is reported as missing, and registers nothing."""
    tool = build_download_sandbox_file_tool(backend=skills_sandbox, artifacts=artifacts)
    scope = {"configurable": {"owner_user_id": OWNER_A, "thread_id": THREAD_ID}}
    absent = f"{REPORT_DIR}/never-written.md"

    result = json.loads(tool.invoke({"path": absent}, config=scope))

    assert result["ok"] is False
    assert result["error"]["code"] == "file_not_found"
    assert result["data"] is None
    assert artifacts.store.find(OWNER_A, absent.rsplit("/", 1)[-1]) is None


def test_the_sandbox_shell_cannot_reach_the_storage_mounts(skills_sandbox):
    """The skills mount is a real directory; the Store-backed mounts must not be there.

    Checked here because the export tool can read any path under ``/workspace`` — this pins
    that ``/memories`` is not one of them, so there is nothing to export from it.
    """
    probe = skills_sandbox.execute("ls -d /memories /persisted-skills 2>&1; echo rc=$?")

    assert "No such file" in probe.output, probe.output
    listing = skills_sandbox.execute("ls /skills/procurement")
    assert "web-scraper" in listing.output


# --------------------------------------------------------------------------- #
# the path boundary and ownership
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "code"),
    [
        (f"{REPORT_DIR}/report.md", None),
        ("/workspace", None),
        # Climbing out of the workspace must land outside it, not pass on a string prefix.
        ("/workspace/../etc/passwd", "path_not_allowed"),
        ("/workspace/../../etc/shadow", "path_not_allowed"),
        ("/etc/passwd", "path_not_allowed"),
        # A sibling directory that merely shares a prefix is not inside.
        ("/workspace-other/report.md", "path_not_allowed"),
        ("report.md", "invalid_path"),
        ("", "invalid_path"),
    ],
)
def test_the_export_boundary_is_decided_on_the_resolved_path(path, code):
    assert check_path(path) == code


def test_the_tool_schema_does_not_ask_the_model_who_the_owner_is(artifacts, sandbox):
    """The scope is injected, not requested: a model that could name the owner could file a
    document under someone else's account."""
    tool = build_download_sandbox_file_tool(backend=sandbox, artifacts=artifacts)

    properties = set(tool.args_schema.model_json_schema()["properties"])

    assert properties == {"path", "name"}, properties
    assert "owner_user_id" not in properties
    assert "thread_id" not in properties


def test_a_run_without_a_scope_registers_nothing(artifacts, sandbox):
    tool = build_download_sandbox_file_tool(backend=sandbox, artifacts=artifacts)

    result = json.loads(tool.invoke({"path": f"{REPORT_DIR}/reorder-report.md"}))

    assert result["ok"] is False
    assert result["error"]["code"] == "SCOPE_MISSING"


def test_another_users_artifact_is_a_404_on_every_route(artifacts, client):
    record = artifacts.register(
        owner_user_id=OWNER_A,
        thread_id=THREAD_ID,
        name="private.md",
        content=b"# private\n",
        source="sandbox:/workspace/report/private.md",
    )

    for url in (
        f"/api/artifacts/{record.artifact_id}",
        f"/api/artifacts/{record.artifact_id}/content",
    ):
        assert client.get(url, headers={"X-Demo-User": OWNER_B}).status_code == 404, url

    listing = client.get(
        "/api/artifacts", params={"thread_id": THREAD_ID}, headers={"X-Demo-User": OWNER_B}
    )
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 0, "别的用户的会话不应列出任何产件"


def test_a_registry_row_whose_bytes_are_gone_is_reported_not_served(
    artifacts, database, client
):
    """`缺产物不发假链接`, tested on the state where it is easy to get wrong.

    The row survives, the bytes do not. A route that only checked the row would hand back a
    zero-byte download with a 200, which every client reads as success.
    """
    record = artifacts.register(
        owner_user_id=OWNER_A,
        thread_id=THREAD_ID,
        name="purged.csv",
        content=b"part_id,quantity\n",
        source="sandbox:/workspace/report/purged.csv",
    )
    database["artifact_blobs"].delete_one({"artifact_id": record.artifact_id})

    metadata = client.get(
        f"/api/artifacts/{record.artifact_id}", headers={"X-Demo-User": OWNER_A}
    )
    assert metadata.status_code == 200
    assert metadata.json()["data"]["download_ready"] is False

    content = client.get(
        f"/api/artifacts/{record.artifact_id}/content", headers={"X-Demo-User": OWNER_A}
    )
    assert content.status_code == 410, "字节已丢失时必须明说，不能用 200 + 空文件糊过去"


def test_the_artifact_routes_are_registered_once(app):
    """The placeholder route is gone, and what replaced it is registered exactly once.

    Read from the OpenAPI document rather than from ``app.routes``: the route list holds
    framework wrappers whose shape is not part of any contract, and a test that walks them
    is testing the router implementation instead of the app's surface.

    Two routes on one path make behaviour depend on registration order, so the set has to
    match exactly rather than merely contain the expected paths.
    """
    paths = [path for path in app.openapi()["paths"] if path.startswith("/api/artifacts")]

    assert sorted(paths) == [
        "/api/artifacts",
        "/api/artifacts/{artifact_id}",
        "/api/artifacts/{artifact_id}/content",
    ], paths


def test_the_placeholder_reader_of_context_extra_is_gone():
    """`context.extra["artifacts"]` had no producer, so that route could only ever 404.

    Asserted against the module rather than by probing: a reintroduced reader would be
    invisible until someone noticed downloads never worked.
    """
    source = (REPO_ROOT / "src" / "api_view" / "api" / "history.py").read_text(encoding="utf-8")

    assert 'extra.get("artifacts")' not in source
    assert "/artifacts/{artifact_id}" not in source


def test_a_non_ascii_name_survives_the_download_headers(artifacts, client):
    """The report is called 补货比价报告.md, so the header has to carry UTF-8 properly."""
    record = artifacts.register(
        owner_user_id=OWNER_A,
        thread_id=THREAD_ID,
        name="补货比价报告.md",
        content="# 报告\n".encode(),
        source="sandbox:/workspace/report/reorder-report.md",
    )

    response = client.get(
        f"/api/artifacts/{record.artifact_id}/content", headers={"X-Demo-User": OWNER_A}
    )

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    assert "attachment" in disposition


# --------------------------------------------------------------------------- #
# the chart, through the same artifact path
# --------------------------------------------------------------------------- #


def test_the_chart_becomes_a_downloadable_artifact_of_its_own(env, artifacts, client):
    """The chart is drawn remotely and arrives in the agent process, so its registration
    happens where the bytes are — and the result is downloadable like any other artifact."""
    from agent.tools.chart_generator import build_chart_generator_tool

    tool = build_chart_generator_tool(
        url=env["MODELSCOPE_MCP_URL"],
        token=env["MODELSCOPE_API_TOKEN"],
        on_asset=artifacts.chart_hook(download_url_template="/api/artifacts/{artifact_id}/content"),
    )
    scope = {"configurable": {"owner_user_id": OWNER_A, "thread_id": THREAD_ID}}
    rows = [
        {"label": part_id, "value": float(amount)}
        for part_id, (_supplier, _price, amount) in EXPECTED_LINES.items()
    ]

    payload = json.loads(
        tool.invoke(
            {"chart_type": "bar", "data": rows, "title": "补货金额"}, config=scope
        )
    )

    assert payload["ok"] is True, payload
    assert payload["data"]["artifact_id"], "图表应当被登记为产件"
    assert payload["data"]["sha256"] == hashlib.sha256(
        __import__("base64").b64decode(payload["data"]["base64"])
    ).hexdigest()

    artifact_id = payload["data"]["artifact_id"]
    downloaded = client.get(
        f"/api/artifacts/{artifact_id}/content", headers={"X-Demo-User": OWNER_A}
    )
    assert downloaded.status_code == 200
    assert downloaded.content[:4] == b"\x89PNG", "下载到的必须是真图片，不是占位内容"
    assert hashlib.sha256(downloaded.content).hexdigest() == payload["data"]["sha256"]


def test_a_chart_without_a_scope_still_returns_the_image_but_registers_nothing(
    env, artifacts
):
    """Refusing to file an ownerless artifact must not break the chart itself."""
    from agent.tools.chart_generator import build_chart_generator_tool

    tool = build_chart_generator_tool(
        url=env["MODELSCOPE_MCP_URL"],
        token=env["MODELSCOPE_API_TOKEN"],
        on_asset=artifacts.chart_hook(),
    )

    payload = json.loads(
        tool.invoke({"chart_type": "bar", "data": [{"label": "P001", "value": 1008.0}]})
    )

    assert payload["ok"] is True
    assert "artifact_id" not in payload["data"]
    assert payload["data"]["base64"]
