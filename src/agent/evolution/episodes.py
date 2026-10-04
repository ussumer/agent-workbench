"""Owner-scoped, append-only evidence spanning all runs of a planning goal."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from agent.env_utils import redact


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat()


class EvidenceError(RuntimeError):
    code = "EPISODE_EVIDENCE_FAILED"


class TextSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    skill_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str = Field(min_length=1, max_length=2000)
    body: str = Field(min_length=1, max_length=32000)
    scope: str = "planning"


class EpisodeStore:
    """Control-plane service. No write/feedback/pointer API is exposed as an Actor tool.

    A bank freezes text only, not the complete environment Snapshot from SE02.
    Event payloads are redacted then chunked, with a commit row and full-payload hash.
    Reserved sequence gaps or incomplete chunks make export fail rather than drop evidence.
    """

    def __init__(self, database: Any, *, secrets: set[str] | None = None) -> None:
        self.database = database
        self.banks = database["planning_skill_banks"]
        self.episodes = database["planning_episodes"]
        self.events = database["planning_episode_events"]
        self.secrets = set(secrets or set())
        self.secrets.update(json.dumps(s, ensure_ascii=False)[1:-1] for s in list(self.secrets))

    def clean(self, value: Any) -> Any:
        if isinstance(value, str):
            return redact(value, self.secrets)
        if isinstance(value, dict):
            return {redact(k, self.secrets): self.clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        return value

    def freeze(self, owner: str, skills: list[TextSkill], *, provenance: str) -> str:
        if len(skills) > 32 or len({s.skill_id for s in skills}) != len(skills):
            raise EvidenceError("bank has too many or duplicate skills")
        if any(s.scope != "planning" for s in skills):
            raise EvidenceError("bank contains another Actor scope")
        content = [{**s.model_dump(), "body_sha256": hashlib.sha256(s.body.encode()).hexdigest()}
                   for s in skills]
        if self.clean(content) != content or self.clean(provenance) != provenance:
            raise EvidenceError("bank contains configured secret material")
        bank_id = uuid4().hex
        self.banks.insert_one({"_id": bank_id, "kind": "bank", "schema_version": 1,
            "owner_user_id": owner, "scope": "planning", "skills": content,
            "sha256": digest(content), "provenance": provenance, "created_at": now()})
        self.bank(owner, bank_id)
        return bank_id

    def bank(self, owner: str, bank_id: str) -> dict[str, Any]:
        row = self.banks.find_one({"_id": bank_id, "owner_user_id": owner, "kind": "bank"})
        if row is None or digest(row["skills"]) != row["sha256"]:
            raise EvidenceError("bank missing or corrupt")
        for skill in row["skills"]:
            if skill["scope"] != "planning" or hashlib.sha256(skill["body"].encode()).hexdigest() != skill["body_sha256"]:
                raise EvidenceError("skill scope or body corrupt")
        return row

    def set_current(self, owner: str, bank_id: str | None) -> None:
        """Explicit control-plane assignment; never changes a goal already bound."""
        if bank_id is not None:
            self.bank(owner, bank_id)
        self.banks.update_one({"_id": "current:" + owner}, {"$set": {
            "kind": "pointer", "owner_user_id": owner, "bank_id": bank_id}}, upsert=True)

    def bind(self, owner: str, thread: str, goal: str, *, model: dict[str, Any]) -> dict[str, Any]:
        if model.get("provenance") not in {"configured-live", "scripted-component"}:
            raise EvidenceError("explicit model provenance required")
        if self.clean(model) != model or any(k in canonical(model).lower() for k in
                                           ('"api_key"', '"authorization"', '"password"', '"grant_secret"')):
            raise EvidenceError("model identity must omit credentials")
        query = {"owner_user_id": owner, "thread_id": thread, "goal_id": goal}
        row = self.episodes.find_one(query)
        if row is None:
            pointer = self.banks.find_one({"_id": "current:" + owner})
            bank_id = (pointer or {}).get("bank_id")
            if bank_id is None:
                bank_id = self.freeze(owner, [], provenance="fixed-empty-baseline")
            bank = self.bank(owner, bank_id)
            row = {**query, "_id": uuid4().hex, "schema_version": 1,
                "experiment_id": "demo", "task_id": goal, "dataset": "demo", "split": "demo",
                "attempt": 1, "bank_id": bank_id, "bank_sha256": bank["sha256"],
                "model": model, "status": "open", "feedback": None, "created_at": now(),
                "run_ids": [], "event_seq": 0}
            try:
                self.episodes.insert_one(row)
            except DuplicateKeyError:
                row = self.episodes.find_one(query)
        if row is None or row["model"] != model:
            raise EvidenceError("goal model provenance changed")
        if self.bank(owner, row["bank_id"])["sha256"] != row["bank_sha256"]:
            raise EvidenceError("bound bank changed")
        return row

    def episode(self, owner: str, episode_id: str) -> dict[str, Any]:
        row = self.episodes.find_one({"_id": episode_id, "owner_user_id": owner})
        if row is None:
            raise EvidenceError("episode not found for owner")
        return row

    def append(self, owner: str, episode_id: str, run_id: str, kind: str, payload: Any) -> str:
        row = self.episodes.find_one_and_update(
            {"_id": episode_id, "owner_user_id": owner, "status": "open"},
            {"$inc": {"event_seq": 1}}, return_document=ReturnDocument.AFTER)
        if row is None:
            raise EvidenceError("episode missing or closed")
        # Only typed data enters evidence: no repr(runtime/config), headers or credentials.
        encoded = canonical(payload)
        cleaned = canonical(self.clean(payload))
        event_id = uuid4().hex
        chunks = [cleaned[i:i + 128000] for i in range(0, len(cleaned), 128000)]
        self.events.insert_many([{"_id": f"{event_id}:{i}", "owner_user_id": owner,
            "episode_id": episode_id, "kind": "chunk", "event_id": event_id,
            "part": i, "content": chunk} for i, chunk in enumerate(chunks)])
        self.events.insert_one({"_id": event_id, "schema_version": 1,
            "owner_user_id": owner, "episode_id": episode_id, "run_id": run_id,
            "kind": kind, "event_id": event_id, "seq": row["event_seq"], "chunks": len(chunks),
            "sha256": hashlib.sha256(cleaned.encode()).hexdigest(),
            "redacted": cleaned != encoded, "created_at": now()})
        return event_id

    def export(self, owner: str, episode_id: str) -> dict[str, Any]:
        row = self.episode(owner, episode_id)
        bank = self.bank(owner, row["bank_id"])
        if bank["sha256"] != row["bank_sha256"]:
            raise EvidenceError("bank no longer matches episode")
        events = list(self.events.find({"owner_user_id": owner, "episode_id": episode_id,
                                       "seq": {"$exists": True}}).sort("seq", 1))
        if [e["seq"] for e in events] != list(range(1, row["event_seq"] + 1)):
            raise EvidenceError("incomplete event commits")
        for event in events:
            parts = list(self.events.find({"owner_user_id": owner, "event_id": event["event_id"],
                                           "kind": "chunk"}).sort("part", 1))
            if [p["part"] for p in parts] != list(range(event["chunks"])):
                raise EvidenceError("incomplete event chunks")
            content = "".join(p["content"] for p in parts)
            if hashlib.sha256(content.encode()).hexdigest() != event["sha256"]:
                raise EvidenceError("event hash mismatch")
            event["payload"] = json.loads(content)
        return {"episode": row, "bank": bank, "events": events}

    def begin_run(self, owner: str, thread: str, goal: str, run_id: str,
                  *, model: dict[str, Any], interrupt_id: str | None, public: Any) -> str:
        row = self.bind(owner, thread, goal, model=model)
        self.append(owner, row["_id"], run_id, "run_started", {
            "interrupt_id": interrupt_id, "public_input": public})
        self.episodes.update_one({"_id": row["_id"], "owner_user_id": owner},
                                 {"$addToSet": {"run_ids": run_id}})
        return str(row["_id"])

    def settle(self, owner: str, episode_id: str, run_id: str, *, status: str,
               budget: Any, goal: Any, approvals: Any, error_code: str | None) -> None:
        failure_class = None
        if status == "failed":
            if error_code == "EPISODE_EVIDENCE_FAILED":
                failure_class = "environment"
            elif error_code and "BUDGET" in error_code:
                failure_class = "resource_limit"
            else:
                failure_class = "unclassified"
        self.append(owner, episode_id, run_id, "run_settled", {
            "api_status": status, "budget": budget, "business_state": goal,
            "approvals": approvals, "error_code": error_code,
            "failure_class": failure_class,
            "procurement_success": None})
        # Deliberately stays open/unscored across approvals and subsequent user turns.
