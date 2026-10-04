from __future__ import annotations

import importlib.util
from pathlib import Path

from agent.async_tasks.service import SdkProtocolClient
from api_view.internal.analysis import _planning_payload


def analyst_module():
    path = Path(__file__).parents[2] / "infra/agent-protocol/analyst_graph.py"
    spec = importlib.util.spec_from_file_location("t50_analyst_graph", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSyncClient:
    def __init__(self):
        self.input = None
        self.assistants = type("Assistants", (), {"search": lambda _: [
            {"graph_id": "procurement-analyst", "assistant_id": "assistant"}]})()
        self.threads = type("Threads", (), {"create": lambda _: {"thread_id": "async-thread"}})()
        self.runs = type("Runs", (), {"create": self._create_run})()

    def _create_run(self, *args, **kwargs):
        self.input = kwargs["input"]
        return {"run_id": "run"}


def test_protocol_run_carries_trusted_parent_context():
    client = FakeSyncClient()
    protocol = SdkProtocolClient("http://protocol", client=client)
    assert protocol.start_run(thread_id="t", assistant_id="a", instruction="审查规划",
                              context={"owner_user_id": "demo-a", "parent_thread_id": "goal"}) == "run"
    assert client.input["owner_user_id"] == "demo-a"
    assert client.input["parent_thread_id"] == "goal"


def test_planning_payload_is_public_and_owner_scoped_shape():
    payload = _planning_payload({
        "goal_id": "g", "revision": 2, "problem": {"offers": []},
        "sources": [{"part_id": "P1", "path": "/parts/P1", "body": {"data": 1}, "secret": "drop"}],
        "orders": [{"result": {"data": {"order_id": "O1"}}}], "approval_token": "drop",
    })
    assert payload["revision"] == 2
    assert payload["sources"][0] == {"part_id": "P1", "path": "/parts/P1", "body": {"data": 1}}
    assert "approval_token" not in payload


def test_async_plan_extracts_parent_context():
    module = analyst_module()
    result = module.plan({"owner_user_id": "demo-a", "parent_thread_id": "goal",
                          "messages": [{"role": "user", "content": "审查规划来源"}]})
    assert result == {"instruction": "审查规划来源", "owner_user_id": "demo-a",
                      "parent_thread_id": "goal"}


def test_planning_instruction_selects_planning_mode_without_model():
    module = analyst_module()
    result = module.analyse({"planning": True, "warnings": {
        "goal_id": "g", "revision": 3, "orders": [], "sources": [],
        "problem": {"offers": [{"part_id": "P1", "source_status": "missing"}]},
    }})
    assert result["analysis"] == {"planning": True, "revision": 3, "goal_id": "g",
                                   "decision": None, "order_count": 0, "source_count": 0,
                                   "missing_sources": ["P1"]}


def test_planning_report_surfaces_missing_sources_and_no_write_claim():
    module = analyst_module()
    result = module.finish({"analysis": {"planning": True, "goal_id": "g", "revision": 1,
                                         "order_count": 0, "source_count": 1,
                                         "missing_sources": ["P1"]}})
    assert "P1" in result["report"]["summary"]
    assert "订单" in result["report"]["summary"]
    assert result["report_name"] == "规划目标审查.md"


def test_inventory_mode_stays_separate():
    module = analyst_module()
    result = module.analyse({"planning": False, "warnings": {"data": {"items": []}}})
    assert result["analysis"]["line_count"] == 0


def test_planning_report_is_not_empty_when_no_sources_are_missing():
    module = analyst_module()
    result = module.finish({"analysis": {"planning": True, "goal_id": "g", "revision": 2,
                                         "order_count": 1, "source_count": 2, "missing_sources": []}})
    assert "需核对" not in result["report"]["summary"]
    assert result["report_markdown"].startswith("# 规划目标异步审查")


def test_planning_payload_does_not_invent_missing_goal():
    assert _planning_payload({"goal_id": "g", "revision": 1}).get("sources") == []
