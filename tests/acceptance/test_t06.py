"""T06 acceptance: real model, framework and service compatibility.

Mode is ``live``. Every CAP runs against the real provider and real local services; no
model substitute is used anywhere in this file. A missing credential is reported as a
typed, secret-free diagnosis rather than being papered over with a fake model.

The eight CAP experiments live in :mod:`tests.compat.capabilities`; this file runs them
once (module-scoped), asserts each one's evidence, and freezes the parts that later tasks
must be written against — the installed signatures and the real v2 stream sample.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.config import ModelConfig, ServiceAddresses, capability_report  # noqa: E402
from agent.env_utils import MissingConfiguration, load_env, secret_values  # noqa: E402
from compat import capabilities, signatures  # noqa: E402
from fixtures import agent_protocol_service, mongo_service, sandbox_service  # noqa: E402

pytestmark = pytest.mark.live

STREAM_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "stream" / "t06-v2-stream.json"
SIGNATURE_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "t06-signatures.json"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def model_config() -> ModelConfig:
    """Model configuration, or a clear failure explaining what is missing."""
    try:
        return ModelConfig.from_env()
    except MissingConfiguration as failure:
        pytest.fail(
            f"live check cannot run: {failure}. Configure MODEL_API_KEY / MODEL_BASE_URL / "
            "MODEL_ID in .env, then re-run. This is a blocked prerequisite, not a test defect."
        )


@pytest.fixture(scope="module")
def caps(model_config) -> dict[str, capabilities.CapabilityRecord]:
    """Run the whole CAP matrix once and share the records."""
    records: dict[str, capabilities.CapabilityRecord] = {}

    records["CAP-01"] = capabilities.cap01_model_chinese_and_two_turns(model_config)
    records["CAP-02"] = capabilities.cap02_tool_call_and_tool_message(model_config)
    records["CAP-03"] = capabilities.cap03_streaming_tool_arguments(model_config)
    records["CAP-04"] = capabilities.cap04_subagent_delegation(model_config)
    records["CAP-05"] = capabilities.cap05_subagent_interrupt_and_resume(
        model_config, decision="approve"
    )
    records["CAP-05R"] = capabilities.cap05_subagent_interrupt_and_resume(
        model_config, decision="reject"
    )

    settings = mongo_service.unique_settings("t06")
    mongo_service.require_reachable(settings)
    try:
        records["CAP-06"] = capabilities.cap06_mongo_restart_readback(
            settings.mongo_uri, settings.database
        )
    finally:
        mongo_service.drop_test_database(settings)

    with sandbox_service.running_backend() as (backend, _):
        records["CAP-07"] = capabilities.cap07_sandbox_io(backend)

    with agent_protocol_service.running_service() as handle:
        client = agent_protocol_service.client(handle.base_url)
        records["CAP-08"] = capabilities.cap08_agent_protocol(
            handle.base_url,
            client=client,
            assistant_id=agent_protocol_service.echo_assistant_id(client),
        )

    return records


# --------------------------------------------------------------------------- #
# the frozen interface record
# --------------------------------------------------------------------------- #


def test_every_tracked_symbol_resolves_at_its_locked_version():
    """A rename in a future dependency bump must fail here, not inside T10/T11/T20."""
    report = signatures.collect_signatures()

    assert report["failures"] == {}, f"未解析的符号: {report['failures']}"
    assert len(report["resolved"]) == len(signatures.TRACKED_SYMBOLS)

    # Assert the protocol surface the backend adapter is written against. Note that
    # `inspect.signature(cls)` returns the *constructor*, so the methods are checked on
    # the classes themselves rather than by substring-matching a class signature.
    from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol

    backend_methods = {
        "ls",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "delete",
        "upload_files",
        "download_files",
    }
    assert backend_methods <= set(dir(BackendProtocol))
    assert {"execute", "aexecute"} <= set(dir(SandboxBackendProtocol))

    # And the constructor later tasks call must still accept the arguments they pass.
    agent_signature = report["resolved"]["deepagents.create_deep_agent"]["signature"]
    for parameter in ("subagents", "interrupt_on", "backend", "checkpointer", "store"):
        assert parameter in agent_signature


def test_the_v2_streaming_vocabulary_is_recorded():
    vocabulary = signatures.streaming_vocabulary()

    assert "messages" in vocabulary["stream_mode_literals"]
    assert "values" in vocabulary["stream_mode_literals"]
    assert "updates" in vocabulary["stream_mode_literals"]
    assert "v2" in vocabulary["version_literals"]
    assert vocabulary["part_keys"] == ["type", "ns", "data", "interrupts"]


def test_signature_record_is_written_for_later_tasks():
    """Persist the record so T10/T11/T20 can read it instead of re-deriving it."""
    report = signatures.signature_report()
    SIGNATURE_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    SIGNATURE_FIXTURE.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    written = json.loads(SIGNATURE_FIXTURE.read_text(encoding="utf-8"))
    assert written["streaming"]["part_keys"] == ["type", "ns", "data", "interrupts"]
    assert written["symbols"]["failures"] == {}


# --------------------------------------------------------------------------- #
# CAP-01 .. CAP-05
# --------------------------------------------------------------------------- #


def test_cap01_model_answers_in_chinese_across_two_turns(caps):
    record = caps["CAP-01"]

    assert record.passed, record.as_dict()
    assert "turn1_correct" in record.checks
    assert "turn2_uses_context" in record.checks, "第二轮必须依赖上一轮的回答"
    assert "chinese_response" in record.checks
    # The provider's own model name is recorded, and is not assumed from the shorthand.
    assert record.evidence["reported_model_name"]
    assert record.evidence["protocol"] == "openai-compatible"


def test_cap02_tool_call_produces_a_matching_tool_message(caps):
    record = caps["CAP-02"]

    assert record.passed, record.as_dict()
    assert record.evidence["tool_call_name"] == "add"
    assert record.evidence["tool_call_args"] == {"a": 17, "b": 25}
    assert record.evidence["tool_call_id"].startswith("call_")
    assert record.evidence["tool_message_content"].strip() == "42"
    assert record.evidence["tool_message_id"] == record.evidence["tool_call_id"]
    assert "42" in record.evidence["final_answer"]


def test_cap03_streaming_tool_arguments_need_concatenation(caps):
    record = caps["CAP-03"]

    assert record.passed, record.as_dict()
    assert record.evidence["fragment_count"] > 1, "参数确实是被拆成多片下发的"
    assert record.evidence["parsed_args"] == {"a": 17, "b": 25}
    assert record.evidence["parse_error"] is None

    # Recording the raw sample is the point: it lets a later task verify its own parser
    # against real fragments rather than a hand-written approximation.
    stream = record.evidence["v2_stream"]
    assert stream["part_count"] > 0
    assert set(stream["types_seen"]) <= {"messages", "values", "updates", "tasks", "custom", "debug"}
    assert "messages" in stream["types_seen"]


def test_cap03_real_stream_sample_is_frozen_to_a_fixture(caps):
    record = caps["CAP-03"]
    STREAM_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    STREAM_FIXTURE.write_text(
        json.dumps(record.evidence["v2_stream"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    written = json.loads(STREAM_FIXTURE.read_text(encoding="utf-8"))
    assert written["tool_arg_fragments"] == record.evidence["raw_fragments"]
    assert written["concatenated_tool_args"] == record.evidence["concatenated"]
    # The frozen sample must be replayable: concatenating its fragments yields valid JSON.
    assert json.loads(written["concatenated_tool_args"]) == {"a": 17, "b": 25}


def test_cap04_delegation_and_namespace_semantics(caps):
    record = caps["CAP-04"]

    assert record.passed, record.as_dict()
    assert record.evidence["task_call_count"] >= 1
    assert "part-researcher" in json.dumps(record.evidence["task_call_args"], ensure_ascii=False)

    # Measured semantics: namespaces require stream(subgraphs=True); with the flag off
    # everything is flattened onto the root and attribution is impossible.
    assert record.evidence["task_delegation_namespaces"], "task 委派的子代理应落在子命名空间"
    assert record.evidence["compiled_subgraph_namespaces"], "编译子图节点应落在子命名空间"
    assert record.evidence["namespaces_without_subgraphs_flag"] == [], (
        "不传 subgraphs=True 时命名空间必须被压平——这是实测的对比基线"
    )


def test_cap05_interrupt_is_raised_by_a_subagent_and_resumes_on_the_same_thread(caps):
    record = caps["CAP-05"]

    assert record.passed, record.as_dict()
    assert record.evidence["interrupt_count"] >= 1
    assert record.evidence["action_request_tools"] == ["lookup_part"]
    assert record.evidence["decision_sent"] == "approve"
    assert record.evidence["still_interrupted_after_resume"] is False
    assert record.evidence["resumed_answer"]


def test_cap05_rejection_resumes_without_running_the_paused_tool(caps):
    record = caps["CAP-05R"]

    assert record.passed, record.as_dict()
    assert record.evidence["interrupt_count"] >= 1
    assert record.evidence["decision_sent"] == "reject"
    assert record.evidence["still_interrupted_after_resume"] is False
    assert record.evidence["resumed_answer"]


# --------------------------------------------------------------------------- #
# CAP-06 .. CAP-08
# --------------------------------------------------------------------------- #


def test_cap06_mongo_state_survives_a_process_restart(caps):
    record = caps["CAP-06"]

    assert record.passed, record.as_dict()
    writer, reader = record.evidence["writer"], record.evidence["reader"]
    assert writer["returncode"] == 0 and reader["returncode"] == 0
    # Different pids is what rules out "read it back from the same in-memory object".
    assert writer["pid"] != reader["pid"]
    assert reader["note"] == "written-by-writer-process"
    assert reader["store_value"] == "store-value-from-writer"
    assert reader["checkpoint_id"] == writer["checkpoint_id"]


def test_cap07_sandbox_executes_and_round_trips_a_file(caps):
    record = caps["CAP-07"]

    assert record.passed, record.as_dict()
    assert record.evidence["sandbox_id"]
    assert record.evidence["image"] == "opensandbox/code-interpreter:v1.1.0"
    assert record.evidence["exit_code"] == 0
    assert record.evidence["sha256_returned"] == record.evidence["sha256_expected"]


def test_cap08_agent_protocol_service_returns_task_results(caps):
    record = caps["CAP-08"]

    assert record.passed, record.as_dict()
    assert record.evidence["health"] == {"ok": True}
    assert record.evidence["graph_id"] == "echo"
    probe = record.evidence["probe"]
    assert record.evidence["sync_result"]["result"] == probe.upper()
    assert record.evidence["async_result"]["result"] == probe.upper()
    assert record.evidence["sub_agent_fields"]["url"] == record.evidence["base_url"]


def test_every_capability_has_a_result_not_just_an_import(caps):
    """The matrix must be complete, and no entry may pass on import success alone."""
    expected = {"CAP-01", "CAP-02", "CAP-03", "CAP-04", "CAP-05", "CAP-05R", "CAP-06", "CAP-07", "CAP-08"}

    assert set(caps) == expected
    for cap, record in caps.items():
        assert record.passed, f"{cap} 未通过: {record.as_dict()}"
        assert record.checks, f"{cap} 没有记录任何检查项"
        assert record.evidence, f"{cap} 没有留下证据"


# --------------------------------------------------------------------------- #
# blocked prerequisites and secret hygiene
# --------------------------------------------------------------------------- #


def test_a_missing_model_credential_is_a_typed_failure_not_a_fake():
    with pytest.raises(MissingConfiguration) as caught:
        ModelConfig.from_env({})

    assert caught.value.capability == "model"
    assert any("MODEL_API_KEY" in item for item in caught.value.missing)


def test_capability_report_is_secret_free_and_identifies_each_gap():
    """A blocked diagnosis must name the variable without revealing any value.

    The structure is asserted for every capability rather than pinning which ones are
    currently missing. An earlier version required ``search`` and ``chart`` to be unsatisfied,
    which was true when it was written and became false the moment the 智谱 and ModelScope
    credentials were provisioned — a test that fails because the project progressed is
    measuring the calendar, not the code. The blocked case is forced in the test below.
    """
    env = load_env()
    report = capability_report(env)
    secrets = {value for value in secret_values(env) if value}

    assert report["model"]["satisfied"] is True

    serialised = json.dumps(report, ensure_ascii=False)
    for secret in secrets:
        assert secret not in serialised, "能力诊断不得泄露任何密钥值"

    for capability, entry in report.items():
        assert set(entry) >= {"satisfied", "missing", "diagnosis"}, capability
        assert isinstance(entry["satisfied"], bool), capability
        if not entry["satisfied"]:
            assert entry["missing"], f"{capability} 未满足时必须指出缺少哪个变量"
            assert entry["diagnosis"], f"{capability} 未满足时必须给出可读的诊断"


def test_a_capability_with_no_credentials_names_every_variable_it_needs():
    """The blocked path, forced rather than waited for."""
    report = capability_report({})

    assert report["model"]["satisfied"] is False
    for capability, expected in (
        ("search", "ZHIPU_API_KEY"),
        ("chart", "MODELSCOPE_MCP_URL"),
        ("chart", "MODELSCOPE_API_TOKEN"),
    ):
        entry = report[capability]
        assert entry["satisfied"] is False, capability
        assert expected in " ".join(entry["missing"]), f"{capability} 应点名 {expected}"


def test_service_addresses_have_documented_local_defaults():
    addresses = ServiceAddresses.from_env({})

    assert addresses.erp_base_url == "http://localhost:8080"
    assert addresses.opensandbox_base_url == "http://localhost:18080"
    assert addresses.agent_protocol_base_url == "http://localhost:8123"
    assert addresses.mcp_base_url == "http://localhost:8000"


def test_no_recorded_evidence_contains_a_key_shaped_string(caps):
    """Guards the whole matrix, including the frozen fixtures."""
    secrets = {value for value in secret_values(load_env()) if value}
    key_shaped = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")

    payloads = [json.dumps(record.as_dict(), ensure_ascii=False, default=str) for record in caps.values()]
    payloads.append(STREAM_FIXTURE.read_text(encoding="utf-8"))
    payloads.append(SIGNATURE_FIXTURE.read_text(encoding="utf-8"))

    for payload in payloads:
        for secret in secrets:
            assert secret not in payload, "证据中出现了密钥值"
        assert not key_shaped.search(payload), "证据中出现了形似密钥的字符串"
