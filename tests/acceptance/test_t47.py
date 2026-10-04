"""Behavioral checks for the real pilot's learning evidence boundary."""
import json

import pytest

from agent.evolution.curator import curator_input, training_record, validate_curated


def test_curator_omits_private_oracle():
    result = {"case_id": "train", "model_calls": 1, "feedback": {"success": True},
              "private_answer": "DO_NOT_LEAK", "episode_export": {"events": [
                  {"kind": "tool_requested", "payload": {"call": {"name": "planning_read", "args": {}}}},
                  {"kind": "turn_grounded", "payload": {"secret_prompt": "DO_NOT_LEAK"}}]}}
    assert "DO_NOT_LEAK" not in curator_input([training_record(result)])


@pytest.mark.parametrize("result", [
    {"model_calls": 0}, {"model_calls": 1, "episode_export": {"events": []}},
])
def test_training_requires_actual_actions(result):
    with pytest.raises(ValueError):
        training_record(result)


def test_empty_training_rejected():
    with pytest.raises(ValueError):
        curator_input([])


def test_training_limit_is_not_silent_truncation():
    with pytest.raises(ValueError):
        curator_input([{"actions": "a" * 24001}])


def test_valid_skill():
    skill = validate_curated(json.dumps({"skill_id": "checks", "description": "检查条件",
                                        "body": "先检查交期，再比较预算。数据完整时不重复询问。"}))
    assert skill.scope == "planning"


@pytest.mark.parametrize("body", ["Use P001", "Use S002", "price 24.00", "api_key=secret"])
def test_instance_answers_rejected(body):
    with pytest.raises(ValueError):
        validate_curated(json.dumps({"skill_id": "x", "description": "x", "body": body}))


def test_extra_curator_fields_rejected():
    with pytest.raises(ValueError):
        validate_curated('{"skill_id":"x","description":"x","body":"x","code":"bad"}')

