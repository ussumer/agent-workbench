"""Candidate scripts run in real OpenSandbox; service-owned checks decide acceptance."""

import hashlib
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acceptance.test_t17 import fixture_files  # noqa: E402
from agent.skills.pipeline import (  # noqa: E402
    SkillValidationError,
    SourceBundle,
    run_smoke,
    validate_bundle,
    write_staging,
)
from fixtures import sandbox_service  # noqa: E402


@pytest.fixture(scope="module")
def sandbox():
    with sandbox_service.running_backend() as (backend, _):
        yield backend


def smoke(sandbox, script=None, *, core=False, files=None):
    files = dict(files or fixture_files())
    if script is not None:
        files["scripts/summarise.py"] = script.encode()
    staging = f"/workspace/scratch/t30-{uuid.uuid4().hex}"
    write_staging(sandbox, staging, files)
    return run_smoke(sandbox, staging=staging, entry="scripts/summarise.py",
                     slug="reorder-cost-summary" if core else "generic-report")


PREFIX = '''import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--input'); p.add_argument('--out-dir')
a = p.parse_args()
out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
'''


def test_exit_codes_without_outputs_do_not_pass(sandbox):
    record = smoke(sandbox, PREFIX + "json.loads(Path(a.input).read_text())\n")
    assert record["passed"] is False
    assert "EXAMPLE_OUTPUT_MISSING" in record["problems"]


def test_bad_input_outputs_cannot_replace_successful_example(sandbox):
    script = PREFIX + '''try:
    json.loads(Path(a.input).read_text())
except ValueError:
    (out / 'proof.txt').write_text('poisoned')
    raise SystemExit(2)
(out / 'proof.txt').write_text('example-only')
'''
    record = smoke(sandbox, script)
    assert record["passed"] is True
    assert record["outputs"]["proof.txt"]["sha256"] == hashlib.sha256(b"example-only").hexdigest()
    assert record["attempts"][0]["output_dir"] != record["attempts"][1]["output_dir"]


def test_outputs_with_same_basename_are_both_preserved(sandbox):
    script = PREFIX + '''json.loads(Path(a.input).read_text())
for name in ['one', 'two']:
    (out / name).mkdir()
    (out / name / 'report.txt').write_text(name)
'''
    record = smoke(sandbox, script)
    assert record["passed"] is True
    assert set(record["outputs"]) == {"one/report.txt", "two/report.txt"}


@pytest.mark.parametrize("invalid", [None, b"not json"])
def test_executable_bundle_must_supply_a_valid_example(invalid):
    files = fixture_files()
    if invalid is None:
        del files["examples/input.json"]
    else:
        files["examples/input.json"] = invalid
    bundle = SourceBundle("generated", "/workspace/scratch/skill", files, "", "")
    with pytest.raises(SkillValidationError) as failure:
        validate_bundle(bundle, slug="reorder-cost-summary")
    assert failure.value.code in {"EXAMPLE_MISSING", "EXAMPLE_INVALID"}


def test_core_skill_wrong_money_fails_independent_contract(sandbox):
    files = fixture_files()
    script = files["scripts/summarise.py"].decode().replace("total += amount", "total += amount + Decimal('1.00')")
    record = smoke(sandbox, script, core=True)
    assert record["passed"] is False
    assert record["business_validation"]["passed"] is False


def test_core_skill_that_accepts_negative_quantity_fails(sandbox):
    files = fixture_files()
    script = files["scripts/summarise.py"].decode().replace(
        "if not MIN_QUANTITY <= value <= MAX_QUANTITY:", "if False:")
    record = smoke(sandbox, script, core=True)
    assert record["passed"] is False
    assert any(case["id"] == "negative-quantity" and not case["passed"]
               for case in record["business_validation"]["cases"])


def test_shipped_core_skill_passes_service_owned_contract(sandbox):
    record = smoke(sandbox, core=True)
    assert record["passed"] is True
    assert record["observed_total"] == "1533.00"
    assert record["business_validation"]["passed"] is True
    assert len(record["business_validation"]["cases"]) >= 5


def test_empty_example_output_does_not_pass(sandbox):
    record = smoke(sandbox, PREFIX + "json.loads(Path(a.input).read_text())\n(out / 'empty.txt').touch()\n")
    assert record["passed"] is False
