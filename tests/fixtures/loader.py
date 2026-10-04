"""Locate and validate the shared JSON fixtures.

These helpers only read and validate fixture documents. They deliberately do not
re-implement ERP business logic: the authoritative implementations live in Java
(T02/T03). Anything arithmetic here is fixture self-consistency checking.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures"

SEED_PATH = FIXTURES_DIR / "seed-v1.json"
EXPECTED_PATH = FIXTURES_DIR / "expected-v1.json"
CASES_PATH = FIXTURES_DIR / "json-cases.json"

# Directories that hold JSON sample payloads, and the rejection class each one
# is allowed to contain. Valid and invalid samples never share a directory.
SAMPLE_DIRS = {
    "orders/valid": {"valid"},
    "orders/invalid": {"schema_invalid"},
    "orders/rejected": {"business_rejected"},
    "interrupts/valid": {"valid"},
    "interrupts/invalid": {"schema_invalid"},
    "sse/valid": {"valid"},
    "sse/invalid": {"schema_invalid"},
    "skills/valid": {"valid"},
    "skills/invalid": {"schema_invalid"},
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def seed() -> dict:
    return load_json(SEED_PATH)


@lru_cache(maxsize=None)
def expected() -> dict:
    return load_json(EXPECTED_PATH)


@lru_cache(maxsize=None)
def registry() -> dict:
    return load_json(CASES_PATH)


def cases() -> list[dict]:
    return registry()["cases"]


def case_path(relative: str) -> Path:
    return FIXTURES_DIR / relative


def load_case(case: dict) -> dict:
    return load_json(case_path(case["path"]))


def schema_path(name: str) -> Path:
    return FIXTURES_DIR / registry()["schemas_dir"] / name


def load_schema(name: str) -> dict:
    return load_json(schema_path(name))


def canonical(payload: object) -> str:
    """Stable serialisation used for determinism checks and hashing."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
