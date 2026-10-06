"""Zero-model protocol checks for the full v3 evolution runner."""
from __future__ import annotations

import json

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


# ---- compute-actor runner protocol checks (zero model) ----

from scripts.planning.gdpevo_tool_diagnostic import _compute_actor  # noqa: E402
from scripts.planning.gdpevo_v4_compute_training import (  # noqa: E402
    ACTOR_KIND,
    UsageCollector,
    arm_skills,
    owner_for,
    repair_suffix,
    reusable_row,
    select_validation_candidates,
    static_skill_block,
    verify,
    write_manifest,
)


def _row(arm, task_id, split, repeat=None, status="scored"):
    row = {"task_id": task_id, "group_id": task_id.rsplit("-", 2)[0], "split": split, "arm": arm,
           "owner": owner_for(arm, task_id, repeat if split == "test" else None),
           "status": status, "grade": {"score": 0.5, "business_success": False, "points": {}},
           "metrics": {"input_tokens": 10, "output_tokens": 2}}
    if repeat is not None:
        row["repeat"] = repeat
    return row


def _synthetic_attempt(tmp_path, inputs):
    _public, _training, _control, _answers, train, test = inputs
    output = tmp_path / "attempt"
    (output / "curator-input").mkdir(parents=True)
    (output / "test").mkdir()
    (output / "protocol.json").write_text(json.dumps(
        {"actor": ACTOR_KIND, "test_feedback": False, "test_repeats": 3}, ensure_ascii=False))
    records = [{"task": {k: t[k] for k in ("task_id", "group_id", "split", "request", "input")}, "attempts": []}
               for t in train]
    (output / "training-records.json").write_text(json.dumps(records, ensure_ascii=False))
    validation = [_row(arm, t["task_id"], "train") for t in train for arm in ARMS]
    (output / "validation.json").write_text(json.dumps(validation, ensure_ascii=False))
    (output / "candidate-selection.json").write_text(json.dumps(
        select_validation_candidates(validation), ensure_ascii=False))
    test_rows = [_row(arm, t["task_id"], "test", repeat=r)
                 for r in range(1, 4) for arm in ARMS for t in test]
    (output / "test-results.json").write_text(json.dumps(test_rows, ensure_ascii=False))
    (output / "curator-input" / "quotes.json").write_text(json.dumps(
        {"group_id": "quotes", "train_records": [{"task_id": "quotes-train-01"}]}, ensure_ascii=False))
    bank = {group: [{"skill_id": f"{group}-op", "description": "操作", "body": "先核对条件与例外。"}]
            for group in GROUPS}
    (output / "skills.json").write_text(json.dumps(bank, ensure_ascii=False))
    (output / "fewshot-skills.json").write_text(json.dumps(bank, ensure_ascii=False))
    write_manifest(output)
    return output, test_rows


def test_compute_owner_is_unique_across_full_episode_matrix(inputs):
    _public, _training, _control, _answers, train, test = inputs
    owners = set()
    for task in train:
        owners.add(owner_for("fixed", task["task_id"], phase="train"))
        owners.add(owner_for("repair", task["task_id"], phase="train"))
        for round_id in range(1, 4):
            owners.add(owner_for(f"reflect-{round_id}", task["task_id"], phase="reflect"))
        for arm in ARMS:
            owners.add(owner_for(arm, task["task_id"], phase="validation"))
    for repeat in range(1, 4):
        for arm in ARMS:
            for task in test:
                owners.add(owner_for(arm, task["task_id"], repeat, phase="test"))
    assert len(owners) == 20 + 20 + 60 + 80 + 240
    assert all("/" not in owner and len(owner) <= 64 for owner in owners)


def test_compute_arm_skill_modes_are_exclusive(inputs):
    _public, _training, _control, _answers, train, _test = inputs
    task = train[0]
    skills = {task["group_id"]: [{"skill_id": "s1", "description": "d", "body": "b"}]}
    fewshot = {task["group_id"]: [{"skill_id": "f1", "description": "d", "body": "b"}]}
    assert arm_skills("dynamic", task, skills, fewshot) == (skills[task["group_id"]], None)
    assert arm_skills("fewshot", task, skills, fewshot) == (None, fewshot[task["group_id"]])
    assert arm_skills("skills", task, skills, fewshot) == (None, skills[task["group_id"]])
    assert arm_skills("reflect-2", task, skills, fewshot) == (None, skills[task["group_id"]])
    assert arm_skills("fixed", task, skills, fewshot) == (None, None)
    block = static_skill_block(skills[task["group_id"]])
    assert "[s1]" in block and "b" in block


def test_compute_actor_rejects_dual_skill_injection(tmp_path):
    skill = [{"skill_id": "s1", "description": "d", "body": "b"}]
    with pytest.raises(ValueError):
        _compute_actor(None, None, {}, tmp_path / "x", "sys", skills=skill, static_skills=skill)


def test_dynamic_bank_dicts_validate_into_text_skills():
    from agent.evolution.episodes import TextSkill
    skill = {"skill_id": "s1", "description": "版本检查", "body": "先核对有效窗口。"}
    validated = TextSkill.model_validate(skill)
    assert validated.skill_id == "s1"
    assert TextSkill.model_validate(validated).skill_id == "s1"


def test_repair_suffix_carries_public_diagnosis_without_gold():
    suffix = repair_suffix({"disposition": "order"}, {"success": False, "failed_outcomes": ["freight_audit"],
                                                      "diagnostics": ["freight_cents 与整车阈值不一致"]})
    assert "公开诊断" in suffix and "freight_audit" in suffix
    assert "correct_answer" not in suffix and "rubric" not in suffix


def test_usage_collector_separates_selector_overhead():
    class _Message:
        usage_metadata = None

    class _Generation:
        message = _Message()

    class _Response:
        def __init__(self, usage):
            self.llm_output = {"usage": usage}
            self.generations = [[_Generation()]]

    collector = UsageCollector()
    collector.on_llm_end(_Response({"prompt_tokens": 10, "completion_tokens": 2}), run_id="a")
    collector.on_llm_end(_Response({"prompt_tokens": 3, "completion_tokens": 1}), run_id="b",
                         metadata={"planning_role": "skill_selector"})
    collector.on_llm_end(_Response({}), run_id="c")
    metrics = collector.metrics()
    assert metrics["input_tokens"] == 13 and metrics["output_tokens"] == 3
    assert metrics["selector_input_tokens"] == 3 and metrics["selector_output_tokens"] == 1
    assert metrics["episode_model_calls"] == 3 and metrics["usage_complete"] is False
    assert type(metrics["input_tokens"]) is int and type(metrics["output_tokens"]) is int


def test_reusable_row_rules(tmp_path):
    scored = tmp_path / "episode"
    scored.mkdir()
    (scored / "result.json").write_text(json.dumps({"status": "scored", "grade": {"score": 1.0}}))
    assert reusable_row(scored)["grade"]["score"] == 1.0
    failed = tmp_path / "broken"
    failed.mkdir()
    (failed / "result.json").write_text(json.dumps({"status": "environment_failed"}))
    assert reusable_row(failed) is None
    retry = tmp_path / "broken-retry-1"
    retry.mkdir()
    (retry / "result.json").write_text(json.dumps({"status": "format_failed"}))
    assert reusable_row(failed)["status"] == "format_failed"


def test_verify_passes_on_synthetic_compute_attempt(tmp_path, inputs):
    output, test_rows = _synthetic_attempt(tmp_path, inputs)
    result = verify(output)
    assert result["status"] == "passed"
    assert result["repeats"] == 3 and result["test_rows"] == len(test_rows) == 240
    assert result["learning_gain_proven"] is False and result["production_assignment_changed"] is False


def test_verify_blocked_when_repeat_missing(tmp_path, inputs):
    output, test_rows = _synthetic_attempt(tmp_path, inputs)
    dropped = test_rows[:-1]
    (output / "test-results.json").write_text(json.dumps(dropped, ensure_ascii=False))
    write_manifest(output)
    assert verify(output)["status"] == "blocked"


def test_verify_blocked_when_test_contains_repair(tmp_path, inputs):
    output, _rows = _synthetic_attempt(tmp_path, inputs)
    (output / "test" / "repeat-1" / "fixed" / "quotes-test-01" / "attempt-2").mkdir(parents=True)
    write_manifest(output)
    assert verify(output)["status"] == "blocked"


def test_verify_rejects_curator_input_referencing_heldout(tmp_path, inputs):
    output, _rows = _synthetic_attempt(tmp_path, inputs)
    (output / "curator-input" / "quotes.json").write_text(json.dumps(
        {"group_id": "quotes", "leak": "quotes-test-01"}, ensure_ascii=False))
    write_manifest(output)
    with pytest.raises(ValueError):
        verify(output)


def test_verify_rejects_tampered_evidence(tmp_path, inputs):
    output, _rows = _synthetic_attempt(tmp_path, inputs)
    (output / "validation-summary.json").write_text("{}")
    write_manifest(output)
    (output / "validation-summary.json").write_text('{"tampered": true}')
    with pytest.raises(ValueError):
        verify(output)
