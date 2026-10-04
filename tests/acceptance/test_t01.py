"""T01 acceptance: one fixed, resettable seed and one set of contract samples.

Every module (Java init, quote site, acceptance) must read the same
``fixtures/seed-v1.json``; the fixed expectations live in
``fixtures/expected-v1.json`` and must never be adjusted to fit an
implementation. This suite checks the fixtures themselves, so it is a unit-mode
check: it needs no service. The authoritative business implementation arrives
with Java in T02/T03.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures import loader  # noqa: E402

pytestmark = pytest.mark.unit

MONEY_RE = re.compile(r"^(?:0\.(?:0[1-9]|[1-9][0-9])|[1-9][0-9]{0,5}\.[0-9]{2})$")
ISO_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

# Fields that must never appear in the seed: a fixed baseline cannot contain
# wall-clock values, or restarts and reruns would disagree.
VOLATILE_KEY_FRAGMENTS = ("created_at", "updated_at", "timestamp", "quoted_at", "random", "uuid")


def money(value: str) -> Decimal:
    assert MONEY_RE.match(value), f"not a two-decimal money string: {value!r}"
    return Decimal(value)


def active_supplier_ids(seed: dict) -> set[str]:
    return {s["supplier_id"] for s in seed["suppliers"] if s["active"]}


def active_part_ids(seed: dict) -> set[str]:
    return {p["part_id"] for p in seed["parts"] if p["active"]}


# --------------------------------------------------------------------------- #
# seed structure and constraints
# --------------------------------------------------------------------------- #


def test_seed_validates_against_its_schema():
    validator = Draft202012Validator(loader.load_schema("seed.schema.json"))
    errors = sorted(validator.iter_errors(loader.seed()), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


def test_seed_declares_the_fixed_baseline_identity():
    seed = loader.seed()
    assert seed["schema_version"] == 1
    assert seed["seed_id"] == "seed-v1"
    assert seed["currency"] == "CNY"
    assert seed["orders"] == [], "基础订单集必须为空"


def test_supplier_part_and_inventory_keys_are_unique():
    seed = loader.seed()
    supplier_ids = [s["supplier_id"] for s in seed["suppliers"]]
    part_ids = [p["part_id"] for p in seed["parts"]]
    skus = [p["sku"] for p in seed["parts"]]
    pairs = [(r["supplier_id"], r["part_id"]) for r in seed["supplier_parts"]]
    inventory_parts = [row["part_id"] for row in seed["inventory"]]

    assert len(supplier_ids) == len(set(supplier_ids))
    assert len(part_ids) == len(set(part_ids))
    assert len(skus) == len(set(skus))
    assert len(pairs) == len(set(pairs)), "SupplierPart 复合唯一"
    assert len(inventory_parts) == len(set(inventory_parts))


def test_inventory_target_is_never_below_its_warning_threshold():
    for row in loader.seed()["inventory"]:
        assert row["target_stock"] >= row["warning_threshold"], row


def test_referenced_suppliers_and_parts_exist_in_the_seed():
    seed = loader.seed()
    supplier_ids = {s["supplier_id"] for s in seed["suppliers"]}
    part_ids = {p["part_id"] for p in seed["parts"]}

    for row in seed["supplier_parts"]:
        assert row["supplier_id"] in supplier_ids, row
        assert row["part_id"] in part_ids, row
    for row in seed["inventory"]:
        assert row["part_id"] in part_ids, row


def test_seed_carries_the_objects_needed_for_rejection_cases():
    seed = loader.seed()
    rejection = seed["rejection_cases"]

    inactive_supplier = next(
        s for s in seed["suppliers"] if s["supplier_id"] == rejection["inactive_supplier"]
    )
    inactive_part = next(
        p for p in seed["parts"] if p["part_id"] == rejection["inactive_part"]
    )
    assert inactive_supplier["active"] is False
    assert inactive_part["active"] is False

    pair = rejection["unsupported_pair"]
    pairs = {(r["supplier_id"], r["part_id"]) for r in seed["supplier_parts"]}
    assert (pair["supplier_id"], pair["part_id"]) not in pairs, "必须存在一个无供货关系组合"

    inactive_relations = [
        r for r in seed["supplier_parts"] if r["supplier_id"] == inactive_supplier["supplier_id"]
    ]
    assert inactive_relations, "停用供应商也要有供货关系，用于证明拒绝来自供应商状态"


def test_supplier_p003_has_no_supply_relation_from_s002():
    pairs = {(r["supplier_id"], r["part_id"]) for r in loader.seed()["supplier_parts"]}
    assert ("S002", "P003") not in pairs


def test_seed_is_deterministic_and_free_of_volatile_values():
    first = loader.seed()
    second = json.loads(loader.SEED_PATH.read_text(encoding="utf-8"))
    assert loader.canonical(first) == loader.canonical(second)

    def walk(node, path=()):
        if isinstance(node, dict):
            for key, value in node.items():
                for fragment in VOLATILE_KEY_FRAGMENTS:
                    assert fragment not in key.lower(), f"易变字段 {'.'.join(path + (key,))}"
                walk(value, (*path, key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, (*path, str(index)))
        elif isinstance(node, str):
            assert not ISO_DATETIME_RE.search(node), f"seed 出现具体时间: {node!r}"

    walk(loader.seed())


def test_seed_hashes_stably_across_reads():
    digest_a = hashlib.sha256(loader.SEED_PATH.read_bytes()).hexdigest()
    digest_b = hashlib.sha256(loader.SEED_PATH.read_bytes()).hexdigest()
    assert digest_a == digest_b


def test_baseline_numbers_live_only_in_the_seed_file():
    """Java init, quote site and acceptance must not keep private copies."""
    offenders = []
    for path in sorted(loader.FIXTURES_DIR.rglob("*.json")):
        if "schemas" in path.parts or path.name == "seed-v1.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:  # pragma: no cover - fixture must be valid JSON
            offenders.append(path.name)
            continue
        if isinstance(payload, dict) and any(
            key in payload for key in ("suppliers", "parts", "inventory")
        ):
            offenders.append(path.relative_to(loader.FIXTURES_DIR).as_posix())
    assert not offenders, f"这些文件私藏了基线数据: {offenders}"


# --------------------------------------------------------------------------- #
# derivations that the fixed expectations pin down
# --------------------------------------------------------------------------- #


def test_inventory_warning_set_is_exactly_p001_p003_p004():
    seed = loader.seed()
    warned = sorted(
        row["part_id"]
        for row in seed["inventory"]
        if row["on_hand"] < row["warning_threshold"]
    )
    assert warned == loader.expected()["inventory_warning"]["part_ids"]


def test_suggested_reorder_quantities_are_fixed():
    """Suggestion is reported per warned row: only on_hand < warning_threshold qualifies.

    A part may sit below its target without being warned (P002 does); the
    /inventory/warnings endpoint deliberately does not invent a suggestion for
    it, and this test pins that semantic so T02 cannot quietly widen it.
    """
    expected = loader.expected()["inventory_warning"]
    inventory = loader.seed()["inventory"]

    suggested = {
        row["part_id"]: max(row["target_stock"] - row["on_hand"], 0)
        for row in inventory
        if row["on_hand"] < row["warning_threshold"]
    }
    assert suggested == expected["suggested_quantity"]
    assert suggested == {"P001": 42, "P003": 15, "P004": 30}

    below_target_but_not_warned = {
        row["part_id"]
        for row in inventory
        if row["on_hand"] >= row["warning_threshold"] and row["on_hand"] < row["target_stock"]
    }
    assert below_target_but_not_warned == {"P002", "P005"}
    assert not (below_target_but_not_warned & set(suggested))


def test_parts_that_are_not_flagged_satisfy_the_warning_predicate():
    seed = loader.seed()
    flagged = set(loader.expected()["inventory_warning"]["part_ids"])
    for row in seed["inventory"]:
        if row["part_id"] not in flagged:
            assert row["on_hand"] >= row["warning_threshold"], row


def test_all_money_values_are_two_decimal_strings():
    for row in loader.seed()["supplier_parts"]:
        assert MONEY_RE.match(row["catalog_price"]), row
        assert row["currency"] == "CNY"


def test_order_totals_match_the_recorded_fixed_values():
    for case in loader.expected()["order_totals"]:
        total = sum(
            (money(line["unit_price"]) * line["quantity"] for line in case["lines"]),
            Decimal("0.00"),
        )
        assert f"{total:.2f}" == case["total_amount"], case["case_id"]
        assert case["source"], f"{case['case_id']} 必须记录输入来源"


def test_reorder_cost_summary_total_is_1533_00():
    block = loader.expected()["reorder_cost_summary"]
    total = sum(
        (money(line["unit_price"]) * line["quantity"] for line in block["lines"]),
        Decimal("0.00"),
    )
    assert f"{total:.2f}" == "1533.00" == block["total_amount"]


def test_quote_comparison_picks_the_cheapest_active_supplier():
    seed = loader.seed()
    expected = loader.expected()["quote_comparison"]
    active = active_supplier_ids(seed)
    catalog = {}
    for row in seed["supplier_parts"]:
        if row["supplier_id"] in active:
            catalog.setdefault(row["part_id"], []).append(
                (money(row["catalog_price"]), row["supplier_id"])
            )

    total = Decimal("0.00")
    for line in expected["lines"]:
        options = sorted(catalog[line["part_id"]], key=lambda item: (item[0], item[1]))
        cheapest_price, cheapest_supplier = options[0]
        assert cheapest_supplier == line["supplier_id"], line
        assert f"{cheapest_price:.2f}" == line["unit_price"], line
        amount = cheapest_price * line["quantity"]
        assert f"{amount:.2f}" == line["amount"], line
        total += amount
    assert f"{total:.2f}" == "2553.00" == expected["total_amount"]


def test_expected_block_references_the_same_seed():
    assert loader.expected()["seed_id"] == loader.seed()["seed_id"]


# --------------------------------------------------------------------------- #
# contract samples: valid, schema-invalid, business-rejected
# --------------------------------------------------------------------------- #


def test_every_valid_sample_passes_its_schema():
    for case in loader.cases():
        if case["kind"] != "valid":
            continue
        validator = Draft202012Validator(loader.load_schema(case["schema"]))
        errors = list(validator.iter_errors(loader.load_case(case)))
        assert not errors, f"{case['path']}: {[e.message for e in errors]}"


def test_every_schema_invalid_sample_is_rejected():
    for case in loader.cases():
        if case["kind"] != "schema_invalid":
            continue
        validator = Draft202012Validator(loader.load_schema(case["schema"]))
        assert list(validator.iter_errors(loader.load_case(case))), (
            f"{case['path']} 应当被 schema 拒绝，但没有产生错误"
        )
        assert case.get("reason"), f"{case['path']} 必须记录拒绝原因"


def test_business_rejected_samples_are_schema_valid():
    """They must fail on business rules, not on shape, or they prove nothing."""
    for case in loader.cases():
        if case["kind"] != "business_rejected":
            continue
        validator = Draft202012Validator(loader.load_schema(case["schema"]))
        errors = list(validator.iter_errors(loader.load_case(case)))
        assert not errors, f"{case['path']} 应当是结构合法的业务拒绝样例: {errors}"


def test_every_business_rejected_sample_breaks_its_stated_rule():
    seed = loader.seed()
    active_suppliers = active_supplier_ids(seed)
    active_parts = active_part_ids(seed)
    pairs = {(r["supplier_id"], r["part_id"]) for r in seed["supplier_parts"]}

    for case in loader.cases():
        if case["kind"] != "business_rejected":
            continue
        payload = loader.load_case(case)
        part_ids = [line["part_id"] for line in payload["lines"]]
        rejection = case["rejection"]

        if rejection == "DUPLICATE_PART":
            assert len(part_ids) != len(set(part_ids)), case["path"]
        elif rejection == "INACTIVE_SUPPLIER":
            assert payload["supplier_id"] not in active_suppliers, case["path"]
        elif rejection == "UNSUPPORTED_PART":
            # 停用物料与「无供货关系」共用同一个错误码，因此两种底层原因都算成立。
            assert any(
                part_id not in active_parts
                or (payload["supplier_id"], part_id) not in pairs
                for part_id in part_ids
            ), case["path"]
        else:  # pragma: no cover - guards against a typo in the registry
            pytest.fail(f"未知拒绝类型 {rejection}")


def test_samples_are_registered_and_sit_in_matching_directories():
    registered = {case["path"]: case for case in loader.cases()}
    assert len(registered) == len(loader.cases()), "样例路径不得重复"

    for relative, allowed_kinds in loader.SAMPLE_DIRS.items():
        directory = loader.case_path(relative)
        assert directory.is_dir(), f"缺少样例目录 {relative}"
        for path in sorted(directory.glob("*.json")):
            key = path.relative_to(loader.FIXTURES_DIR).as_posix()
            assert key in registered, f"{key} 未登记到 json-cases.json"
            assert registered[key]["kind"] in allowed_kinds, f"{key} 的 kind 与目录不符"

    for key, case in registered.items():
        parent = Path(key).parent.as_posix()
        assert parent in loader.SAMPLE_DIRS, f"{key} 不在受管样例目录内"
        assert case["kind"] in loader.SAMPLE_DIRS[parent]


def test_valid_and_invalid_samples_never_share_a_directory():
    """“无效样例与有效样例分目录”是硬要求，不是约定。"""
    kinds_by_dir: dict[str, set[str]] = {}
    for case in loader.cases():
        kinds_by_dir.setdefault(Path(case["path"]).parent.as_posix(), set()).add(case["kind"])

    assert kinds_by_dir, "至少要登记一个样例"
    for directory, kinds in kinds_by_dir.items():
        assert len(kinds) == 1, f"{directory} 混放了多种样例: {kinds}"
        if directory.endswith("/valid"):
            assert kinds == {"valid"}, directory
        if directory.endswith("/invalid"):
            assert kinds == {"schema_invalid"}, directory


# --------------------------------------------------------------------------- #
# interrupt, SSE and skill fixtures
# --------------------------------------------------------------------------- #


def test_approval_fixture_hash_matches_its_frozen_payload_file():
    case = next(c for c in loader.cases() if c["path"].endswith("hitl-approval-create.json"))
    payload = loader.load_case(case)
    frozen = loader.case_path(case["frozen_payload"]).read_bytes()
    assert payload["payload"]["payload_sha256"] == hashlib.sha256(frozen).hexdigest()


def test_both_interrupt_layers_are_covered_by_fixtures():
    kinds = set()
    for case in loader.cases():
        if case["path"].startswith("interrupts/valid/"):
            kinds.add(loader.load_case(case)["interrupt_type"])
    assert kinds == {"order_info_supplement", "hitl_approval"}


def test_sse_fixtures_always_carry_both_chunks_and_expected_events():
    checked = 0
    for case in loader.cases():
        if not case["path"].startswith("sse/valid/"):
            continue
        payload = loader.load_case(case)
        assert payload["chunks"], case["path"]
        assert payload["expected_events"], case["path"]
        seqs = [event["seq"] for event in payload["expected_events"]]
        assert seqs == sorted(seqs), f"{case['path']} 的 seq 必须递增"
        checked += 1
    assert checked >= 2, "至少要有中文跨分片与工具参数分片两个用例"


def test_utf8_split_fixture_splits_inside_a_multibyte_character():
    payload = loader.load_json(loader.case_path("sse/valid/token-utf8-split.json"))
    import base64

    chunks = [base64.b64decode(chunk["base64"]) for chunk in payload["chunks"]]
    joined = b"".join(chunks).decode("utf-8")
    assert payload["expected_events"][1]["payload"]["text"] in joined
    # At least one chunk boundary must not be valid UTF-8 on its own.
    assert any(_decode_fails(chunk) for chunk in chunks[:-1])


def _decode_fails(raw: bytes) -> bool:
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def test_skill_manifest_digests_match_the_skill_sources():
    manifest = loader.load_json(
        loader.case_path("skills/valid/reorder-cost-summary-v1.manifest.json")
    )
    skill_root = loader.FIXTURES_DIR / "skills" / "reorder-cost-summary-v1"
    for entry in manifest["files"]:
        blob = (skill_root / entry["path"]).read_bytes()
        assert hashlib.sha256(blob).hexdigest() == entry["sha256"], entry["path"]
        assert len(blob) == entry["size"], entry["path"]

    manifest_lines = "\n".join(
        f"{e['path']}:{e['sha256']}:{e['size']}" for e in manifest["files"]
    )
    assert (
        hashlib.sha256(manifest_lines.encode("utf-8")).hexdigest() == manifest["manifest_sha256"]
    )
    assert manifest["total_size"] == sum(e["size"] for e in manifest["files"])


def test_skill_manifest_declares_its_script_entry():
    manifest = loader.load_json(
        loader.case_path("skills/valid/reorder-cost-summary-v1.manifest.json")
    )
    assert manifest["scripts_entry"] in {entry["path"] for entry in manifest["files"]}
