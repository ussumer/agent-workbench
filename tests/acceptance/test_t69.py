import hashlib
import json
import pytest
from scripts.planning.fewshot_dynamic_paired import injection, expected_keys, summarize, verify_manifest

BANK = [{"skill_id": "a", "body": "test", "description": "test", "scope": "planning"}]

def test_static_and_dynamic_have_same_knowledge():
    static = injection("static", BANK, "examples")
    dynamic = injection("dynamic", BANK, "examples")
    assert static["static"] == dynamic["dynamic_bank"] == BANK
    assert static["system_suffix"] == dynamic["system_suffix"] == "examples"

def test_modes_are_exclusive():
    assert injection("static", BANK, "x")["dynamic_bank"] is None
    assert injection("dynamic", BANK, "x")["static"] is None

def test_unknown_arm_rejected():
    with pytest.raises(ValueError): injection("wrong", BANK, "x")

def test_repeats_not_silently_reduced():
    with pytest.raises(ValueError): expected_keys([{"task_id":"t"}], 1)

def test_pair_matrix():
    assert len(expected_keys([{"task_id":"t"}], 3)) == 6

def test_failed_rows_remain_denominator():
    rows=[{"arm":"static","grade":{"score":1,"business_success":True}}, {"arm":"static","grade":{"score":0,"business_success":False}}]
    assert summarize(rows)["static"] == {"count":2,"mean_score":0.5,"business_success":1}

def test_valid_manifest(tmp_path):
    (tmp_path/"x").write_bytes(b"data")
    (tmp_path/"manifest.json").write_text(json.dumps({"x":hashlib.sha256(b"data").hexdigest()}))
    verify_manifest(tmp_path)

def test_corrupt_manifest_rejected(tmp_path):
    (tmp_path/"x").write_bytes(b"wrong")
    (tmp_path/"manifest.json").write_text(json.dumps({"x":hashlib.sha256(b"data").hexdigest()}))
    with pytest.raises(ValueError): verify_manifest(tmp_path)
