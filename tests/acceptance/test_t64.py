"""Zero-model protocol checks for the full v3 evolution runner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.planning.gdpevo_v4_training import (
    ARMS,
    GROUPS,
    actor_messages,
    example_bank,
    fresh_call_directory,
    group_curator_view,
    instance_tokens,
    load_inputs,
    parse_skill_list,
    validate_skill,
)


@pytest.fixture(scope="module")
def inputs():
    return load_inputs()


def test_full_v3_has_twenty_train_and_twenty_heldout(inputs):
    _public, _training, _control, _answers, train, test = inputs
    assert len(train) == 20 and len(test) == 20
    assert {t["group_id"] for t in train} == set(GROUPS)
    assert all(sum(t["group_id"] == g for t in train) == 5 for g in GROUPS)
    assert all(sum(t["group_id"] == g for t in test) == 5 for g in GROUPS)


def test_curator_view_contains_public_input_and_no_control_plane(inputs):
    public, training, control, _answers, train, _test = inputs
    records = [{"task": {k: t[k] for k in ("task_id", "group_id", "split", "request", "input")}, "attempts": []} for t in train]
    view = group_curator_view(public, training, records, "quotes")
    encoded = json.dumps(view, ensure_ascii=False)
    assert len(view["train_records"]) == 5
    assert "private" not in encoded.lower()
    assert all(key not in view for key in ("rubrics", "manual_checks", "gold"))
    assert all(key not in json.dumps(record, ensure_ascii=False) for record in view["train_records"] for key in ("rubrics", "manual_checks", "gold"))
    assert all("policy" in rule and "stop" in rule for rule in view["policies"])


def test_fewshot_examples_exclude_current_task_and_are_train_only(inputs):
    _public, _training, _control, answers, train, _test = inputs
    examples = example_bank(answers, train, exclude=train[0]["task_id"])
    assert 1 <= len(examples) <= 4
    assert train[0]["task_id"] not in {e["task_id"] for e in examples}
    assert all(e["task_id"].endswith(tuple(f"{i:02d}" for i in range(1, 6))) for e in examples)


def test_skill_validation_rejects_instance_answers_but_allows_approval_semantics():
    skill = {"skill_id": "approval-check", "description": "审批边界", "body": "核对当前 revision 的 authorization 与最终 allocation 一致。"}
    assert validate_skill(skill, {"quotes-train-01"}) == skill
    with pytest.raises(ValueError):
        validate_skill({**skill, "body": "应选择 quotes-train-01 的 final 报价。"}, {"quotes-train-01"})
    assert "quotes-train-01" in instance_tokens({"task_id": "quotes-train-01"})


def test_dynamic_and_skill_arms_keep_task_boundary(inputs):
    public, training, _control, _answers, train, test = inputs
    task = test[0]
    skills = [{"skill_id": "planning_quotes_strategy", "description": "版本检查", "body": "先核对有效窗口。"}]
    dynamic = actor_messages(public, training, task, "dynamic", selected=skills)
    scoped = actor_messages(public, training, task, "skills", skills=skills)
    for messages in (dynamic, scoped):
        encoded = json.dumps(messages, ensure_ascii=False)
        assert task["task_id"] in encoded
        assert "training_materials" not in encoded
        assert "rubrics" not in encoded and "manual_checks" not in encoded


def test_arm_matrix_is_explicit():
    assert ARMS == ("fixed", "fewshot", "skills", "dynamic")


def test_each_group_curator_view_has_five_records(inputs):
    public, training, _control, _answers, train, _test = inputs
    records = [{"task": {k: t[k] for k in ("task_id", "group_id", "split", "request", "input")}, "attempts": []} for t in train]
    for group in GROUPS:
        view = group_curator_view(public, training, records, group)
        assert view["group_id"] == group
        assert len(view["train_records"]) == 5
        assert all(record["task_id"].startswith(group + "-") for record in view["train_records"])


def test_parse_skill_list_requires_bounded_unique_skills():
    value = {"skills": [
        {"skill_id": "one", "description": "一", "body": "先核对条件。"},
        {"skill_id": "two", "description": "二", "body": "再核对例外。"},
    ]}
    assert len(parse_skill_list({"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}]}, set())) == 2
    duplicate = {"skills": [value["skills"][0], value["skills"][0]]}
    with pytest.raises(ValueError):
        parse_skill_list({"choices": [{"message": {"content": json.dumps(duplicate, ensure_ascii=False)}}]}, set())


def test_resume_allocates_new_call_directory_without_overwriting_evidence(tmp_path):
    base = tmp_path / "selector"
    base.mkdir()
    assert fresh_call_directory(base).name == "selector-retry-1"
    (base.parent / "selector-retry-1").mkdir()
    assert fresh_call_directory(base).name == "selector-retry-2"
