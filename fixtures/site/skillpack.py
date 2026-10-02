"""Deterministic packaging of the downloadable skill.

The ZIP is rebuilt from ``fixtures/skills/reorder-cost-summary-v1/`` on every
server start. Two properties matter:

* **Reproducible bytes.** Entry order and timestamps are fixed, so the same
  sources always produce the same SHA-256. A caller can therefore verify the
  download against the catalog without trusting the transport.
* **One source of truth.** The archive is not committed as a binary blob; it is
  derived from files that reviewers can read and diff.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
SKILL_ROOT = SKILLS_DIR / "reorder-cost-summary-v1"

SLUG = "reorder-cost-summary"
VERSION = "1.0.0"
ARCHIVE_NAME = f"{SLUG}-v1.zip"

# Fixed so the archive hash is stable across machines, operating systems and runs.
# ``create_system`` matters: zipfile defaults it to the host OS, which alone would make a Windows
# build and a Linux build of identical sources produce different bytes.
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o644
UNIX_CREATE_SYSTEM = 3
DEFLATE_VERSION = 20

DESCRIPTION = (
    "把库存预警与已抓取报价汇总成补货数量、金额合计和 Markdown 表格；"
    "输入 JSON 文件，输出 summary.json 与 report.md，不发起任何订单写操作。"
)


def source_files() -> list[Path]:
    """Skill sources in a stable order (relative POSIX path ascending)."""
    files = [path for path in SKILL_ROOT.rglob("*") if path.is_file()]
    return sorted(files, key=lambda path: path.relative_to(SKILL_ROOT).as_posix())


def build_skill_zip() -> bytes:
    """Return the deterministic ZIP bytes for the skill package."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source_files():
            relative = path.relative_to(SKILL_ROOT).as_posix()
            info = zipfile.ZipInfo(relative, date_time=FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = UNIX_CREATE_SYSTEM
            info.create_version = DEFLATE_VERSION
            info.extract_version = DEFLATE_VERSION
            info.internal_attr = 0
            info.external_attr = FILE_MODE << 16
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def catalog_payload() -> dict:
    """The ``/skills/catalog.json`` document, computed from the real archive."""
    archive = build_skill_zip()
    return {
        "schema_version": 1,
        "note": "演示数据：技能包由本项目自建，仅用于课程演示，不是外部真实资源。",
        "skills": [
            {
                "slug": SLUG,
                "version": VERSION,
                "url": f"/skills/{ARCHIVE_NAME}",
                "sha256": sha256(archive),
                "size": len(archive),
                "description": DESCRIPTION,
            }
        ],
    }
