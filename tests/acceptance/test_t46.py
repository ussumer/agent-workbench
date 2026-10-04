"""T46 control-plane checks; no model calls and no service substitutes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.planning.live_baseline import reserve_attempt, settle_attempt, sse_frames, terminal


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
