"""Versioned service-owned acceptance cases, independent of candidate examples.

These checks establish the specified procurement contract, not sealed generalization.
Only the sandbox runs candidate code; the service reads results and computes its oracle.
"""

from __future__ import annotations

import json
import shlex
from copy import deepcopy
from decimal import Decimal
from typing import Any

CONTRACT_VERSION = "reorder-cost-summary-v1"


def _cases() -> list[tuple[str, dict[str, Any], bool]]:
    base: dict[str, Any] = {"currency": "CNY", "lines": [
        {"part_id": "P001", "supplier_id": "S002", "quantity": 42,
         "unit_price": "24.00", "currency": "CNY", "source_url": "https://quotes.example/S002"},
        {"part_id": "P004", "supplier_id": "S002", "quantity": 30,
         "unit_price": "17.50", "currency": "CNY", "source_url": "https://quotes.example/S002"},
    ]}
    varied = deepcopy(base)
    varied["lines"][0].update(quantity=17, unit_price="6.50")
    varied["lines"][1].update(quantity=3, unit_price="0.03")
    negative = deepcopy(base)
    negative["lines"][0]["quantity"] = -1
    boolean = deepcopy(base)
    boolean["lines"][0]["quantity"] = True
    currency = deepcopy(base)
    currency["currency"] = "USD"
    return [("fixed-money", base, True), ("varied-money", varied, True),
            ("negative-quantity", negative, False), ("boolean-quantity", boolean, False),
            ("unsupported-currency", currency, False)]


def _expected(payload: dict[str, Any]) -> dict[str, Any]:
    lines = []
    total = Decimal("0.00")
    urls = []
    for line in payload["lines"]:
        amount = Decimal(line["unit_price"]) * line["quantity"]
        lines.append({**line, "amount": f"{amount:.2f}"})
        total += amount
        if line["source_url"] not in urls:
            urls.append(line["source_url"])
    return {"currency": "CNY", "lines": lines, "total_amount": f"{total:.2f}", "source_urls": urls}


def _summary_matches(document: Any, expected: dict[str, Any]) -> bool:
    if not isinstance(document, dict):
        return False
    if document.get("currency") != "CNY" or document.get("total_amount") != expected["total_amount"]:
        return False
    lines = document.get("lines")
    if not isinstance(lines, list) or len(lines) != len(expected["lines"]):
        return False
    if any(not isinstance(line, dict) or type(line.get("quantity")) is not int for line in lines):
        return False
    indexed = {line.get("part_id"): line for line in lines if isinstance(line.get("part_id"), str)}
    for line in expected["lines"]:
        actual = indexed.get(line["part_id"], {})
        if any(actual.get(key) != value for key, value in line.items()):
            return False
    urls = document.get("source_urls")
    return (isinstance(urls, list) and all(isinstance(url, str) for url in urls)
            and sorted(urls) == sorted(expected["source_urls"]))


def _problems(outputs: dict[str, bytes], payload: dict[str, Any]) -> list[str]:
    expected = _expected(payload)
    summaries = []
    reports = []
    for name, content in outputs.items():
        try:
            text = content.decode("utf-8")
            if name.endswith(".json"):
                summaries.append(json.loads(text))
            if name.endswith(".md"):
                reports.append(text)
        except (ValueError, UnicodeDecodeError):
            continue
    problems = []
    if not any(_summary_matches(document, expected) for document in summaries):
        problems.append("SUMMARY_SCHEMA_OR_VALUES_MISMATCH")
    tokens = [expected["total_amount"], "CNY", *expected["source_urls"]]
    for line in expected["lines"]:
        tokens.extend(str(line[key]) for key in ("part_id", "supplier_id", "quantity", "unit_price", "amount"))
    if not any("|" in report and all(token in report for token in tokens) for report in reports):
        problems.append("MARKDOWN_REPORT_MISSING_OR_INCORRECT")
    return problems


def validate_procurement(backend: Any, *, staging: str, entry: str) -> dict[str, Any]:
    from agent.skills.pipeline import _collect_outputs, _read_output_files, _run

    records = []
    for case_id, payload, valid in _cases():
        directory = f"{staging}/.contract-validation/{case_id}"
        output_dir = f"{directory}/outputs"
        input_path = f"{directory}/input.json"
        _run(backend, f"rm -rf {shlex.quote(directory)}; mkdir -p {shlex.quote(output_dir)}")
        upload = backend.upload_files([(input_path, json.dumps(payload).encode())])
        if len(upload) != 1 or getattr(upload[0], "error", None):
            raise RuntimeError(f"contract input upload failed: {case_id}")
        result = _run(backend, f"cd {shlex.quote(staging)} && python3 {shlex.quote(entry)} "
                      f"--input {shlex.quote(input_path)} --out-dir {shlex.quote(output_dir)}")
        problems = _problems(_read_output_files(backend, output_dir), payload) if valid else []
        correct_exit = result.exit_code == 0 if valid else result.exit_code not in (None, 0)
        if not correct_exit:
            problems.append("UNEXPECTED_EXIT_CODE")
        records.append({"id": case_id, "input": payload, "exit_code": result.exit_code,
                        "stdout": result.output[-2000:], "output_dir": output_dir,
                        "outputs": _collect_outputs(backend, output_dir), "problems": problems,
                        "passed": not problems})
    return {"contract_version": CONTRACT_VERSION, "cases": records,
            "passed": all(record["passed"] for record in records)}
