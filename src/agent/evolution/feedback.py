"""Control-plane persistence for independent, sanitized evaluation feedback."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from math import isfinite
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)
from pymongo import ReturnDocument

from agent.evolution.episodes import EpisodeStore, canonical


class FeedbackError(RuntimeError):
    code = "FEEDBACK_INVALID"


class Feedback(BaseModel):
    """The only feedback shape that may be attached to an Episode."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(default=1, strict=True)
    success: StrictBool
    constraint_status: StrictStr = Field(pattern=r"^(feasible|infeasible)$")
    environment_status: StrictStr = Field(pattern=r"^(feasible|infeasible|unresolved)$")
    error_categories: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    evaluator: StrictStr = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,63}$")
    report_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("error_categories", "rule_ids", "evidence_refs")
    @classmethod
    def safe_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 32 or len(set(values)) != len(values) or any(
            not isinstance(value, str) or not value.strip() or len(value) > 512
            or re.search(r"(?:api[_-]?key|authorization|grant[_-]?secret|password)", value, re.I)
            for value in values
        ):
            raise ValueError("feedback contains duplicate, empty, oversized or secret material")
        return values

    @field_validator("evidence_refs")
    @classmethod
    def public_evidence_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        pattern = re.compile(r"^(?:evaluation:[a-z0-9][a-z0-9_.-]*|fixtures/planning/decision-cases-v1\.json#[a-z0-9][a-z0-9_.-]*)$")
        if any(not pattern.fullmatch(value) for value in values):
            raise ValueError("evidence reference is outside the public evaluation registry")
        return values

    @field_validator("error_categories")
    @classmethod
    def known_categories(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        allowed = {"SUBOPTIMAL", "SOURCE_UNRESOLVED", "BUDGET", "DEADLINE", "QUANTITY", "PARTIAL", "SUPPLIER_SPLIT"}
        if any(value not in allowed for value in values):
            raise ValueError("unknown feedback category")
        return values

    @field_validator("rule_ids")
    @classmethod
    def known_rules(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(value not in {"R-DEADLINE", "R-BUDGET", "R-SOURCE", "R-PARTIAL"} for value in values):
            raise ValueError("unknown feedback rule")
        return values

    @model_validator(mode="after")
    def coherent_result(self) -> Feedback:
        if self.success and (self.constraint_status != "feasible" or self.environment_status != "feasible" or self.error_categories):
            raise ValueError("successful feedback must be feasible, resolved and error-free")
        if self.environment_status == "unresolved" and "SOURCE_UNRESOLVED" not in self.error_categories:
            raise ValueError("unresolved environment requires SOURCE_UNRESOLVED")
        return self

    @classmethod
    def from_sanitized(cls, value: Mapping[str, Any]) -> Feedback:
        if not isinstance(value, Mapping):
            raise FeedbackError("feedback must be an object")
        allowed = set(cls.model_fields)
        if set(value) != allowed:
            raise FeedbackError("feedback fields are not the independent whitelist")
        try:
            return cls.model_validate(value)
        except Exception as failure:  # pydantic details stay on the control plane
            raise FeedbackError("feedback failed its strict schema") from failure


def feedback_digest(feedback: Feedback) -> str:
    return hashlib.sha256(canonical(feedback.model_dump(mode="json")).encode()).hexdigest()


class FeedbackStore:
    """CAS attachment to the existing Episode collection; no Actor tool exposes it."""

    def __init__(self, database: Any, *, episode_store: EpisodeStore | None = None) -> None:
        self.database = database
        self.episodes = database["planning_episodes"]
        self.events = episode_store or EpisodeStore(database)

    def attach(self, owner: str, episode_id: str, run_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
        feedback = Feedback.from_sanitized(value)
        row = self.episodes.find_one({"_id": episode_id, "owner_user_id": owner})
        if row is None or run_id not in row.get("run_ids", []):
            raise FeedbackError("episode/run does not belong to owner")
        payload = feedback.model_dump(mode="json")
        digest = feedback_digest(feedback)
        token = uuid4().hex
        updated = self.episodes.find_one_and_update(
            {"_id": episode_id, "owner_user_id": owner, "feedback": None, "feedback_pending": {"$exists": False}},
            {"$set": {"feedback_pending": {"token": token, "payload": payload, "sha256": digest},
                       "feedback_evaluator": feedback.evaluator}},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise FeedbackError("feedback already attached or episode owner mismatch")
        try:
            self.events.append(owner, episode_id, run_id, "feedback_attached", {
                "feedback_sha256": digest, "evaluator": feedback.evaluator,
                "success": feedback.success, "constraint_status": feedback.constraint_status,
                "environment_status": feedback.environment_status, "pending_token": token,
            })
        except Exception as failure:
            self.episodes.update_one({"_id": episode_id, "owner_user_id": owner,
                                      "feedback_pending.token": token}, {"$unset": {
                                          "feedback_pending": "", "feedback_evaluator": ""}})
            # Do not decrement event_seq here: another concurrent append may have
            # claimed the next sequence. An incomplete event remains visible to
            # EpisodeStore.export as a failed evidence boundary rather than being
            # mistaken for a contiguous history.
            raise FeedbackError("feedback event could not be committed") from failure
        published = self._publish(owner, episode_id, token)
        if published is None:
            raise FeedbackError("feedback evidence is incomplete; pending remains unscored")
        return published

    def _publish(self, owner: str, episode_id: str, token: str) -> dict[str, Any] | None:
        try:
            exported = self.events.export(owner, episode_id)
        except Exception:
            return None
        event_payload = None
        for event in exported["events"]:
            if event["kind"] != "feedback_attached":
                continue
            candidate = event["payload"]
            if candidate.get("pending_token") == token:
                event_payload = candidate
                break
        if event_payload is None:
            return None
        current = self.episodes.find_one({"_id": episode_id, "owner_user_id": owner})
        pending = (current or {}).get("feedback_pending")
        if pending is None or pending.get("token") != token:
            return None
        if event_payload.get("feedback_sha256") != pending["sha256"]:
            return None
        return self.episodes.find_one_and_update(
            {"_id": episode_id, "owner_user_id": owner, "feedback": None,
             "feedback_pending.token": token, "feedback_pending.sha256": event_payload["feedback_sha256"]},
            {"$set": {"feedback": pending["payload"], "feedback_sha256": event_payload["feedback_sha256"],
                       "feedback_token": token},
             "$unset": {"feedback_pending": "", "feedback_evaluator": ""}}, return_document=ReturnDocument.AFTER)

    def recover_pending(self, owner: str, episode_id: str) -> Feedback | None:
        row = self.episodes.find_one({"_id": episode_id, "owner_user_id": owner})
        pending = (row or {}).get("feedback_pending")
        if not pending:
            return self.read(owner, episode_id)
        self._publish(owner, episode_id, pending["token"])
        return self.read(owner, episode_id)

    def read(self, owner: str, episode_id: str) -> Feedback | None:
        row = self.episodes.find_one({"_id": episode_id, "owner_user_id": owner})
        if row is None:
            raise FeedbackError("episode not found for owner")
        payload = row.get("feedback")
        if payload is None:
            return None
        try:
            feedback = Feedback.model_validate(payload)
        except Exception as failure:
            raise FeedbackError("feedback schema or payload is corrupt") from failure
        if row.get("feedback_sha256") != feedback_digest(feedback):
            raise FeedbackError("feedback hash mismatch")
        try:
            exported = self.events.export(owner, episode_id)
        except Exception as failure:
            raise FeedbackError("feedback evidence is incomplete or corrupt") from failure
        matching = [event for event in exported["events"]
                    if event["kind"] == "feedback_attached"
                    and event["payload"].get("pending_token") == row.get("feedback_token")
                    and event["payload"].get("feedback_sha256") == feedback_digest(feedback)]
        if len(matching) != 1:
            raise FeedbackError("feedback commit event missing or duplicated")
        return feedback


def validate_baseline_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a future live baseline without opening a model connection."""
    required = {"model_id", "base_url", "api_key", "budget", "services"}
    if not isinstance(config, Mapping) or not required <= set(config):
        raise FeedbackError("baseline config is incomplete; no model call made")
    model_id, base_url, api_key = (config[k] for k in ("model_id", "base_url", "api_key"))
    parsed = urlparse(base_url) if isinstance(base_url, str) else None
    if not all(isinstance(value, str) and value.strip() for value in (model_id, base_url, api_key)) or not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FeedbackError("baseline model identity/endpoint/credential is missing")
    if re.search(r"scripted|fake|stub|mock|dummy", model_id, re.I):
        raise FeedbackError("baseline model identity is a test double")
    budget = config["budget"]
    if not isinstance(budget, Mapping) or any(
        type(budget.get(key)) not in (int, float) or not isfinite(float(budget[key])) or budget[key] <= 0
        for key in ("total_cny", "per_attempt_cny", "max_model_calls", "max_output_tokens")
    ) or budget["per_attempt_cny"] > budget["total_cny"]:
        raise FeedbackError("baseline budget is invalid")
    services = config["services"]
    required_services = {"mongo", "erp", "sandbox", "mcp"}
    if not isinstance(services, Mapping) or not required_services <= set(services) or any(not isinstance(v, str) or not v for v in services.values()):
        raise FeedbackError("baseline services are incomplete")
    return {"model_id": model_id, "base_url": base_url, "budget": dict(budget),
            "services": dict(services), "checked_at": datetime.now(UTC).isoformat(),
            "model_calls": 0, "credentials_present": True}


__all__ = ["Feedback", "FeedbackError", "FeedbackStore", "feedback_digest", "validate_baseline_config"]
