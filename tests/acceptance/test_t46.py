"""T46 control-plane checks; no model calls and no service substitutes."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.planning.evidence import verify_episode, verify_manifest, verify_orders
from scripts.planning.live_baseline import reserve_attempt, settle_attempt, sse_frames, terminal

from agent.evolution.episodes import digest


class Response:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


def frame(status: str, event: str = "done") -> str:
    return f'event: {event}\ndata: {{"v":1,"payload":{{"status":"{status}"}}}}\n\n'


@pytest.mark.unit
def test_sse_parser_keeps_event_name():
    parsed = sse_frames(Response(frame("completed")))
    assert (
        parsed[0]["event"] == "done" and parsed[0]["envelope"]["payload"]["status"] == "completed"
    )


@pytest.mark.unit
def test_sse_parser_joins_crlf_frames():
    parsed = sse_frames(Response(frame("interrupted").replace("\n", "\r\n")))
    assert terminal(parsed)["status"] == "interrupted"


@pytest.mark.unit
def test_terminal_rejects_missing_done():
    with pytest.raises(RuntimeError, match="missing"):
        terminal([])


@pytest.mark.unit
def test_terminal_rejects_duplicate_done():
    with pytest.raises(RuntimeError, match="duplicate"):
        terminal(sse_frames(Response(frame("completed") + frame("completed"))))


@pytest.mark.unit
def test_terminal_rejects_unknown_status():
    with pytest.raises(RuntimeError, match="invalid"):
        terminal(sse_frames(Response(frame("wat"))))


@pytest.mark.unit
def test_reservation_writes_authorized_total(tmp_path: Path):
    row = reserve_attempt(tmp_path / "ledger.json", 2, 50)
    data = json.loads((tmp_path / "ledger.json").read_text())
    assert row["reserved_cny"] == 2 and data["authorized_total_cny"] == 50


@pytest.mark.unit
def test_reservation_accumulates(tmp_path: Path):
    reserve_attempt(tmp_path / "ledger.json", 2, 50)
    reserve_attempt(tmp_path / "ledger.json", 2, 50)
    assert len(json.loads((tmp_path / "ledger.json").read_text())["reservations"]) == 2


@pytest.mark.unit
def test_reservation_is_cumulative_not_per_process(tmp_path: Path):
    for _ in range(3):
        reserve_attempt(tmp_path / "ledger.json", 2, 6)
    with pytest.raises(RuntimeError, match="exhausted"):
        reserve_attempt(tmp_path / "ledger.json", 2, 6)


@pytest.mark.unit
def test_reservation_rejects_over_limit(tmp_path: Path):
    with pytest.raises(RuntimeError, match="exhausted"):
        reserve_attempt(tmp_path / "ledger.json", 2, 1)


@pytest.mark.unit
def test_reservation_has_unique_id(tmp_path: Path):
    a = reserve_attempt(tmp_path / "ledger.json", 2, 50)
    b = reserve_attempt(tmp_path / "ledger.json", 2, 50)
    assert a["reservation_id"] != b["reservation_id"]


@pytest.mark.unit
def test_sse_parser_ignores_comments():
    parsed = sse_frames(Response(": keepalive\n\n" + frame("completed")))
    assert len(parsed) == 1


@pytest.mark.unit
def test_sse_parser_preserves_nonterminal_events():
    parsed = sse_frames(Response(frame("running", "run_started") + frame("completed")))
    assert [p["event"] for p in parsed] == ["run_started", "done"]


@pytest.mark.unit
def test_terminal_returns_payload_only():
    payload = terminal(sse_frames(Response(frame("completed"))))
    assert payload == {"status": "completed"}


@pytest.mark.unit
def test_corrupt_ledger_does_not_reset_budget(tmp_path):
    ledger = tmp_path / "ledger.json"
    ledger.write_text('{"reservations":')
    with pytest.raises(json.JSONDecodeError):
        reserve_attempt(ledger, 2, 50)
    assert ledger.read_text() == '{"reservations":'


@pytest.mark.unit
def test_existing_authorization_cannot_be_increased(tmp_path):
    ledger = tmp_path / "ledger.json"
    reserve_attempt(ledger, 2, 4)
    with pytest.raises(ValueError, match="authorization"):
        reserve_attempt(ledger, 2, 50)


@pytest.mark.unit
@pytest.mark.parametrize("amount,total", [(0, 50), (-1, 50), (3, 50), (2, 51), (float("nan"), 50)])
def test_invalid_fee_limits_are_refused(tmp_path, amount, total):
    with pytest.raises(ValueError):
        reserve_attempt(tmp_path / "ledger.json", amount, total)


@pytest.mark.unit
def test_concurrent_reservations_cannot_exceed_session_total(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    ledger = tmp_path / "ledger.json"
    def attempt(_):
        try:
            return reserve_attempt(ledger, 2, 4)["reservation_id"]
        except RuntimeError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(attempt, range(8)))
    assert len([x for x in ids if x]) == 2
    assert sum(r["reserved_cny"] for r in json.loads(ledger.read_text())["reservations"]) == 4


@pytest.mark.unit
def test_unknown_usage_retains_reservation(tmp_path):
    ledger = tmp_path / "ledger.json"
    r = reserve_attempt(ledger, 2, 2)
    with pytest.raises(ValueError):
        settle_attempt(ledger, r["reservation_id"], {}, 2)
    with pytest.raises(RuntimeError):
        reserve_attempt(ledger, 1, 2)


@pytest.mark.unit
def test_usage_settlement_preserves_failed_call_reservations(tmp_path):
    ledger = tmp_path / "ledger.json"
    r = reserve_attempt(ledger, 2, 2)
    settle_attempt(ledger, r["reservation_id"], {"reserved_upper_cny": 1.5, "usage_complete": False}, 2)
    with pytest.raises(RuntimeError):
        reserve_attempt(ledger, 1, 2)
    assert json.loads(ledger.read_text())["reservations"][0]["reserved_cny"] == 1.5


@pytest.fixture
def bound_evidence():
    """Synthetic control-plane data only; never counted as live model evidence."""
    bank = {"_id": "bank", "owner_user_id": "owner", "skills": [], "sha256": digest([])}
    episode = {"_id": "episode", "owner_user_id": "owner", "thread_id": "thread",
               "goal_id": "goal", "bank_id": "bank", "bank_sha256": bank["sha256"],
               "event_seq": 2, "run_ids": ["run1", "run2"]}
    events = []
    for revision in (1, 2):
        payload = {"public_input": {"goal": {"revision": revision}}}
        events.append({"kind": "run_started", "owner_user_id": "owner", "episode_id": "episode",
                       "run_id": f"run{revision}", "seq": revision, "payload": payload,
                       "sha256": digest(payload)})
    return {"owner": "owner", "thread_id": "thread", "goal": {"goal_id": "goal"},
            "episode": episode, "episode_export": {"episode": deepcopy(episode), "bank": bank,
                                                       "events": events}}


@pytest.mark.unit
def test_episode_hash_and_two_revision_binding(bound_evidence):
    assert verify_episode(bound_evidence)["runs"] == 2


@pytest.mark.unit
@pytest.mark.parametrize("corruption", ["payload", "sequence", "owner", "run", "bank", "revision"])
def test_episode_audit_rejects_corrupt_or_foreign_evidence(bound_evidence, corruption):
    export = bound_evidence["episode_export"]
    if corruption == "payload":
        export["events"][0]["payload"]["public_input"]["goal"]["revision"] = 9
    elif corruption == "sequence":
        export["events"].pop(0)
    elif corruption == "owner":
        export["events"][0]["owner_user_id"] = "other"
    elif corruption == "run":
        export["events"][0]["run_id"] = "late-run"
    elif corruption == "bank":
        export["bank"]["skills"] = [{"body": "changed"}]
    else:
        event = export["events"][1]
        event["payload"]["public_input"]["goal"]["revision"] = 1
        event["sha256"] = digest(event["payload"])
    with pytest.raises(ValueError):
        verify_episode(bound_evidence)


@pytest.mark.unit
def test_evidence_manifest_rejects_rewritten_result(tmp_path):
    import hashlib

    for name in ("result.json", "source.tar.gz"):
        (tmp_path / name).write_bytes(b"original")
    expected = hashlib.sha256(b"original").hexdigest()
    (tmp_path / "evidence-manifest.json").write_text(json.dumps(
        {"result.json": expected, "source.tar.gz": expected}))
    verify_manifest(tmp_path)
    (tmp_path / "result.json").write_bytes(b"rewritten")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_manifest(tmp_path)


@pytest.mark.unit
def test_evidence_manifest_rejects_path_outside_attempt(tmp_path):
    (tmp_path / "evidence-manifest.json").write_text(json.dumps(
        {"result.json": "none", "source.tar.gz": "none", "../outside": "none"}))
    # Validate the path set independently of its insertion order; missing files
    # are also a rejection, so create the first two to reach the traversal case.
    import hashlib

    expected = hashlib.sha256(b"").hexdigest()
    for name in ("result.json", "source.tar.gz"):
        (tmp_path / name).write_bytes(b"")
    (tmp_path / "evidence-manifest.json").write_text(json.dumps(
        {"result.json": expected, "source.tar.gz": expected, "../outside": expected}))
    with pytest.raises(ValueError, match="escapes"):
        verify_manifest(tmp_path)


@pytest.mark.unit
def test_order_audit_rejects_extra_erp_write():
    result = {"erp_before": [], "orders": [{}, {}], "erp_after": [
        {"order_id": "one"}, {"order_id": "two"}, {"order_id": "extra"}]}
    with pytest.raises(ValueError, match="exactly two"):
        verify_orders(result)
