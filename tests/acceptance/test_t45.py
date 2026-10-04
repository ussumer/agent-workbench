"""Strict feedback persistence and no-call baseline gate."""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.evolution.episodes import EpisodeStore
from agent.evolution.feedback import (
    Feedback,
    FeedbackError,
    FeedbackStore,
    feedback_digest,
    validate_baseline_config,
)


def feedback(**overrides):
    value = {
        "schema_version": 1, "success": True, "constraint_status": "feasible",
        "environment_status": "feasible", "error_categories": (), "rule_ids": ("R-BUDGET",),
        "evidence_refs": ("evaluation:case-1",), "evaluator": "independent-judge-v1",
        "report_sha256": "a" * 64,
    }
    value.update(overrides)
    return value


@pytest.mark.unit
def test_feedback_accepts_only_strict_whitelist():
    result = Feedback.from_sanitized(feedback())
    assert result.success and result.rule_ids == ("R-BUDGET",)
    assert set(result.model_dump()) == set(feedback())


@pytest.mark.parametrize("extra", ["offer_id", "order_id", "quantity", "unit_price", "objective", "api_key"])
@pytest.mark.unit
def test_feedback_rejects_answer_or_credential_fields(extra):
    value = feedback(**{extra: "private"})
    with pytest.raises(FeedbackError):
        Feedback.from_sanitized(value)


@pytest.mark.parametrize("field,value", [
    ("success", "true"), ("constraint_status", "unknown"), ("environment_status", "failed"),
    ("report_sha256", "bad"), ("evidence_refs", ()), ("rule_ids", ("R-BUDGET", "R-BUDGET")),
    ("error_categories", ("api_key=secret",)),
])
@pytest.mark.unit
def test_feedback_rejects_invalid_types_and_sensitive_values(field, value):
    with pytest.raises(FeedbackError):
        Feedback.from_sanitized(feedback(**{field: value}))


@pytest.mark.unit
def test_feedback_digest_is_canonical_and_stable():
    first = Feedback.from_sanitized(feedback(success=False, environment_status="unresolved", error_categories=("SUBOPTIMAL", "SOURCE_UNRESOLVED")))
    second = Feedback.from_sanitized(feedback(success=False, environment_status="unresolved", error_categories=("SUBOPTIMAL", "SOURCE_UNRESOLVED")))
    assert feedback_digest(first) == feedback_digest(second)
    assert len(feedback_digest(first)) == 64


@pytest.mark.unit
def test_baseline_gate_validates_without_model_call():
    result = validate_baseline_config({"model_id": "configured-live-model", "base_url": "https://model.invalid/v1",
        "api_key": "secret-is-not-returned", "budget": {"total_cny": 10.0, "per_attempt_cny": 2.0,
        "max_model_calls": 16, "max_output_tokens": 2048}, "services": {"mongo": "mongodb://local",
        "erp": "http://erp", "sandbox": "http://sandbox", "mcp": "http://mcp"}})
    assert result["model_calls"] == 0 and result["credentials_present"] is True
    assert "api_key" not in json.dumps(result)


@pytest.mark.parametrize("config", [
    {}, {"model_id": "scripted-component", "base_url": "x", "api_key": "x", "budget": {}, "services": {"x": "y"}},
    {"model_id": "live", "base_url": "", "api_key": "x", "budget": {}, "services": {}},
    {"model_id": "live", "base_url": "x", "api_key": "x", "budget": {"total_cny": 1, "per_attempt_cny": 2,
        "max_model_calls": 1, "max_output_tokens": 1}, "services": {"x": "y"}},
])
@pytest.mark.unit
def test_baseline_gate_rejects_missing_double_or_invalid_budget(config):
    with pytest.raises(FeedbackError):
        validate_baseline_config(config)


@pytest.mark.unit
def test_feedback_does_not_mark_procurement_success_by_presence():
    value = Feedback.from_sanitized(feedback(success=True))
    assert "procurement_success" not in value.model_dump()


@pytest.mark.unit
def test_feedback_unknown_evaluator_is_not_silently_normalized():
    with pytest.raises(FeedbackError):
        Feedback.from_sanitized(feedback(evaluator="Unknown Evaluator"))


@pytest.fixture(scope="module")
def database():
    from tests.fixtures.mongo_service import drop_test_database, start_resources, unique_settings
    settings = unique_settings("t45-feedback")
    resources = start_resources(settings)
    yield resources.database
    resources.close()
    drop_test_database(settings)


@pytest.fixture()
def episode(database):
    owner, thread, run = "feedback-" + uuid.uuid4().hex[:8], uuid.uuid4().hex, uuid.uuid4().hex
    store = EpisodeStore(database)
    store.set_current(owner, None)
    episode_id = store.begin_run(owner, thread, thread, run,
        model={"provenance": "scripted-component", "model_type": "component"},
        interrupt_id=None, public={"case_id": "case-1"})
    yield database, store, owner, episode_id, run
    database.planning_episode_events.delete_many({"owner_user_id": owner})
    database.planning_episodes.delete_many({"owner_user_id": owner})
    database.planning_skill_banks.delete_many({"owner_user_id": owner})


@pytest.mark.integration
def test_feedback_attaches_to_real_mongo_episode_and_reads_after_service_rebuild(episode):
    database, store, owner, episode_id, run = episode
    service = FeedbackStore(database, episode_store=store)
    value = feedback()
    saved = service.attach(owner, episode_id, run, value)
    assert saved["feedback_sha256"] == feedback_digest(Feedback.from_sanitized(value))
    rebuilt = FeedbackStore(database, episode_store=EpisodeStore(database))
    assert rebuilt.read(owner, episode_id).success is True
    assert database.planning_episode_events.count_documents({"episode_id": episode_id, "kind": "feedback_attached"}) == 1


@pytest.mark.integration
def test_feedback_cas_rejects_duplicate_and_cross_owner_writes(episode):
    database, store, owner, episode_id, run = episode
    service = FeedbackStore(database, episode_store=store)
    service.attach(owner, episode_id, run, feedback())
    with pytest.raises(FeedbackError, match="already attached"):
        service.attach(owner, episode_id, run, feedback(success=False))
    with pytest.raises(FeedbackError, match="does not belong"):
        service.attach("other-owner", episode_id, run, feedback())


@pytest.mark.integration
def test_feedback_concurrent_cas_has_one_publisher(episode):
    database, store, owner, episode_id, run = episode

    def attach_once():
        try:
            return FeedbackStore(database, episode_store=EpisodeStore(database)).attach(owner, episode_id, run, feedback())
        except FeedbackError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attach_once(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert FeedbackStore(database).read(owner, episode_id) is not None
    assert database.planning_episode_events.count_documents({"episode_id": episode_id, "kind": "feedback_attached"}) == 1


@pytest.mark.integration
def test_feedback_run_fencing_and_tamper_detection(episode):
    database, store, owner, episode_id, run = episode
    service = FeedbackStore(database, episode_store=store)
    with pytest.raises(FeedbackError, match="does not belong"):
        service.attach(owner, episode_id, "other-run", feedback())
    service.attach(owner, episode_id, run, feedback())
    database.planning_episodes.update_one({"_id": episode_id}, {"$set": {"feedback": {"success": False}}})
    with pytest.raises(Exception, match="feedback"):
        service.read(owner, episode_id)


@pytest.mark.integration
def test_feedback_event_failure_rolls_back_cas(episode, monkeypatch):
    database, store, owner, episode_id, run = episode
    service = FeedbackStore(database, episode_store=store)
    original = store.append
    def fail(*args, **kwargs):
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(store, "append", fail)
    with pytest.raises(FeedbackError, match="event"):
        service.attach(owner, episode_id, run, feedback())
    assert service.read(owner, episode_id) is None
    monkeypatch.setattr(store, "append", original)
    service.attach(owner, episode_id, run, feedback())


@pytest.mark.integration
def test_feedback_pending_without_complete_event_is_not_visible(episode):
    database, _, owner, episode_id, _ = episode
    service = FeedbackStore(database)
    parsed = Feedback.from_sanitized(feedback())
    database.planning_episodes.update_one({"_id": episode_id}, {"$set": {
        "feedback_pending": {"token": "orphan", "payload": parsed.model_dump(mode="json"), "sha256": feedback_digest(parsed)}}})
    assert service.recover_pending(owner, episode_id) is None
    assert service.read(owner, episode_id) is None


@pytest.mark.integration
def test_feedback_recovery_publishes_after_event_before_final_cas(episode):
    database, store, owner, episode_id, run = episode
    service = FeedbackStore(database, episode_store=store)
    parsed = Feedback.from_sanitized(feedback())
    token = "recoverable"
    database.planning_episodes.update_one({"_id": episode_id}, {"$set": {
        "feedback_pending": {"token": token, "payload": parsed.model_dump(mode="json"), "sha256": feedback_digest(parsed)}}})
    store.append(owner, episode_id, run, "feedback_attached", {"feedback_sha256": feedback_digest(parsed), "pending_token": token})
    assert service.recover_pending(owner, episode_id) is not None


@pytest.mark.integration
def test_feedback_event_hash_corruption_stays_unpublished(episode):
    database, store, owner, episode_id, run = episode
    service = FeedbackStore(database, episode_store=store)
    parsed = Feedback.from_sanitized(feedback())
    token = "tampered"
    database.planning_episodes.update_one({"_id": episode_id}, {"$set": {
        "feedback_pending": {"token": token, "payload": parsed.model_dump(mode="json"), "sha256": feedback_digest(parsed)}}})
    event_id = store.append(owner, episode_id, run, "feedback_attached", {"feedback_sha256": feedback_digest(parsed), "pending_token": token})
    database.planning_episode_events.update_one({"_id": f"{event_id}:0"}, {"$set": {"content": "{}"}})
    assert service.recover_pending(owner, episode_id) is None


@pytest.mark.integration
def test_feedback_control_store_has_no_actor_tool_surface(episode):
    database, _, owner, episode_id, _ = episode
    service = FeedbackStore(database)
    assert not hasattr(service, "tool")
    assert "private_judge" not in json.dumps(database.planning_episodes.find_one({"_id": episode_id, "owner_user_id": owner}), default=str)
