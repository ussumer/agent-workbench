"""Real publisher validation errors retain their shape through the tool boundary."""

import json

import pytest

from agent.skills.pipeline import SkillPublisher
from agent.tools.assign_skill import build_assign_skill_tool, publish_skill


CASES = [
    ("ftp://approved.invalid/skill.zip", "SOURCE_INVALID"),
    ("https://unapproved.invalid/skill.zip", "SOURCE_NOT_ALLOWED"),
    ("not-a-url", "SOURCE_INVALID"),
]


def publisher():
    # These URLs are refused before any backend operation or persistence is allowed.
    # Opaque sentinels make an accidental execution/write fail rather than simulate it.
    return SkillPublisher(backend_provider=object, store=object(), allowed_source_hosts=())


@pytest.mark.parametrize("source,code", CASES)
def test_validation_failure_returns_internal_dict(source, code):
    result = publish_skill(
        publisher=publisher(), owner_user_id="unit-owner", source_type="package",
        source=source, slug="invalid-package", target_scope="main",
    )
    assert isinstance(result, dict)
    assert result["ok"] is False
    assert result["code"] == code
    assert result["message"]


@pytest.mark.parametrize("source,code", CASES)
def test_validation_failure_returns_parseable_tool_envelope(source, code):
    tool = build_assign_skill_tool(publisher=publisher())
    result = json.loads(tool.invoke(
        {"source_type": "package", "source": source, "slug": "invalid-package", "target_scope": "main"},
        config={"configurable": {"owner_user_id": "unit-owner", "thread_id": "unit-thread"}},
    ))
    assert result["ok"] is False
    assert result["data"] is None
    assert result["error"]["code"] == code
    assert result["error"]["message"]
    assert result["error"]["retryable"] is False
