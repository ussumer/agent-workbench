"""T67 boundaries: component tests are not DS calibration evidence."""
from pathlib import Path
import importlib.util
import json
import pytest

SPEC = importlib.util.spec_from_file_location("v4_taskset", Path(__file__).parents[2] / "scripts/planning/gdpevo_v4_taskset.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)

def formal(tmp_path):
    group = tmp_path / "task_group_025"
    group.mkdir()
    value = {"task_group": {"task_group_id": group.name}, "env": {"state_mode": "read_only"},
             "train_tasks": [], "test_tasks": []}
    for split in ("train", "test"):
        for number in range(1, 6):
            base = f"{split}_tasks/{number:03d}"
            row = {"task_id": f"{split}_{number:03d}", "input": base + "/input/",
                   "prompt_txt": base + "/input/prompt.txt",
                   "payloads": [base + "/input/payloads/answer_template.json"],
                   "notes": base + "/notes/notes.md", "answer_json": base + "/output/answer.json",
                   "eval": {"script": base + "/eval/eval.sh", "files": [base + "/eval/eval.sh"]}}
            for rel, text in ((row["prompt_txt"], "Find program PRG-021 using sourcing tools."),
                              (row["payloads"][0], '{"recommendation":"string"}'),
                              (row["notes"], "PRIVATE RUBRIC"),
                              (row["answer_json"], '{"recommendation":"SECRET GOLD"}'),
                              (row["eval"]["script"], "#!/bin/sh\n")):
                path = group / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            value[split + "_tasks"].append(row)
    (group / "task_group.yaml").write_text(json.dumps(value))
    return group

def test_prepare_only_stages_input_and_train_supervision(tmp_path):
    group = formal(tmp_path)
    output = tmp_path / "stage"
    result = runtime.prepare(group, output)
    assert result["status"] == "prepared"
    assert len(list((output / "actor-inputs").glob("*"))) == 10
    assert not any(p.name in {"notes.md", "answer.json", "eval.sh"} for p in (output / "actor-inputs").rglob("*"))
    training = json.loads((output / "fewshot-input.json").read_text())
    assert {r["task_id"] for r in training} == {f"train_{i:03d}" for i in range(1, 6)}
    assert "test_" not in json.dumps(training)
    assert len(json.loads((output / "protocol.json").read_text())["schedule"]) == 30

def test_formal_manifest_missing_blocks(tmp_path):
    with pytest.raises(ValueError, match="formal"):
        runtime.load_formal(tmp_path)

def test_manifest_cannot_route_prompt_to_hidden_answer(tmp_path):
    group = formal(tmp_path)
    index = json.loads((group / "task_group.yaml").read_text())
    index["test_tasks"][0]["prompt_txt"] = index["test_tasks"][0]["answer_json"]
    (group / "task_group.yaml").write_text(json.dumps(index))
    with pytest.raises(ValueError, match="input"):
        runtime.load_formal(group)

@pytest.mark.parametrize("path", ["/api/judge", "/health", "/api/sourcing/../orders",
                                  "http://localhost:27028", "/api/sourcing/documents/../../notes",
                                  "/api/sourcing/programs?limit=1", "//host/api/sourcing/programs"])
def test_domain_route_refuses_control_paths(path):
    with pytest.raises(ValueError):
        runtime.validate_domain_request(path, {})

def test_domain_route_and_query_whitelist():
    assert runtime.validate_domain_request("/api/sourcing/quotes", {"program_id": "PRG-021", "limit": 50})[0] == "/api/sourcing/quotes"
    with pytest.raises(ValueError):
        runtime.validate_domain_request("/api/sourcing/quotes", {"url": "http://host"})

def test_verify_never_treats_preparation_as_calibration(tmp_path):
    group = formal(tmp_path)
    output = tmp_path / "stage"
    runtime.prepare(group, output)
    result = runtime.verify(output)
    assert result["status"] == "blocked"
    assert result["calibration_complete"] is False

def test_sandbox_isolation_rejects_host_mount_and_bridge():
    good = {"Mounts": [], "HostConfig": {"Privileged": False, "PidMode": ""},
            "NetworkSettings": {"Networks": {"t67-internal": {}}}}
    runtime.validate_container_isolation(good, {"Internal": True}, "t67-internal")
    bad = {**good, "Mounts": [{"Source": "/mnt/c/dev"}]}
    with pytest.raises(ValueError, match="mount"):
        runtime.validate_container_isolation(bad, {"Internal": True}, "t67-internal")
    with pytest.raises(ValueError, match="internal"):
        runtime.validate_container_isolation(good, {"Internal": False}, "t67-internal")

def test_changed_task_input_breaks_frozen_gate(tmp_path):
    group = formal(tmp_path)
    output = tmp_path / "stage"
    runtime.prepare(group, output)
    (group / "test_tasks/001/input/prompt.txt").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        runtime.check_frozen_sources(output)

def test_run_refuses_failed_doctor_before_paid_calls(tmp_path, monkeypatch):
    group = formal(tmp_path)
    output = tmp_path / "stage"
    runtime.prepare(group, output)
    config = tmp_path / "runtime.json"
    config.write_text("{}")
    monkeypatch.setattr(runtime, "doctor", lambda *a, **k: {"status": "blocked", "reasons": ["isolation"]})
    with pytest.raises(ValueError, match="doctor"):
        runtime.run(output, config)
    assert not (output / "run").exists()


def test_sdk_proxy_configuration_uses_installed_pydantic_object():
    config = runtime.sandbox_config({'sandbox_port': 18125, 'sandbox_image': 'opensandbox/code-interpreter:v1.1.0'})
    connection = config.connection_config()
    assert connection.use_server_proxy is True
    assert connection.retry_policy.max_retries == 0
    assert config.ready_timeout_seconds == 30


def evidence_env(group):
    files = ("seed.json", "generate_seed.py", "source-manifest.json", "erp/src/main/java/Controller.java", "erp/src/main/resources/schema.sql")
    for rel in files:
        path = group / "env" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}" if rel.endswith(".json") else "# synthetic verifier unit fixture")
    index = runtime.read(group / "task_group.yaml")
    index["env"]["files"] = ["env/" + rel for rel in files]
    runtime.write(group / "task_group.yaml", index)

def forged_calibration(tmp_path):
    group = formal(tmp_path)
    evidence_env(group)
    output = tmp_path / "stage"
    runtime.prepare(group, output)
    rows = []
    for entry in runtime.read(output / "protocol.json")["schedule"]:
        directory = output / "run/attempts" / f"r{entry['repeat']}-{entry['task_id']}-{entry['arm']}"
        runtime.write(directory / "submission.json", {})
        for name in ("raw-trace.jsonl", "mcp/tools.jsonl"):
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        runtime.write(directory / "mcp/executions.json", [{"status": "completed", "code": "npv=1"}])
        runtime.write(directory / "mcp/sandbox-inspection.json", {})
        runtime.write(directory / "mcp/cleanup.json", {})
        rows.append({**entry, "status": "scored", "grade": {"score": .5 if entry["arm"] == "base" else .7, "points": []}})
    runtime.write(output / "run/result.json", {"status": "completed", "rows": rows,
                  "skills": [{}, {}, {}], "usage": {"model_calls": 0}})
    runtime.freeze_manifest(output / "run")
    return output

def test_negative_review_fixture_empty_zero_call_run_is_blocked(tmp_path):
    result = runtime.verify(forged_calibration(tmp_path))
    assert result["status"] == "blocked"
    assert result["calibration_complete"] is False
    assert any("model" in reason or "doctor" in reason for reason in result["reasons"])

@pytest.mark.parametrize("relative", ["env/seed.json", "env/generate_seed.py", "env/source-manifest.json",
                                      "env/erp/src/main/java/Controller.java", "env/erp/src/main/resources/schema.sql"])
def test_prepare_freezes_actual_environment_inputs(tmp_path, relative):
    group = formal(tmp_path)
    evidence_env(group)
    output = tmp_path / "stage"
    runtime.prepare(group, output)
    (group / relative).write_text("changed source or dataset")
    with pytest.raises(ValueError, match="changed"):
        runtime.check_frozen_sources(output)

def test_undeclared_evaluator_file_change_is_detected(tmp_path):
    group = formal(tmp_path)
    evidence_env(group)
    extra = group / "test_tasks/001/eval/actual_scorer.py"
    extra.write_text("old scorer")
    output = tmp_path / "stage"
    runtime.prepare(group, output)
    extra.write_text("replacement scorer")
    with pytest.raises(ValueError, match="changed"):
        runtime.check_frozen_sources(output)

def test_rescore_rejects_different_stored_business_score(tmp_path):
    group = formal(tmp_path)
    task = runtime.load_formal(group)[1][-1]
    script = group / task["eval"]["script"]
    script.write_text('printf \'{"score":0,"points":[{"point_id":"decision","weight":1,"passed":false,"assigned_score":1,"earned_score":0,"details":"independent"}]}\'')
    submission = tmp_path / "submission.json"
    runtime.write(submission, {"wrong": True})
    with pytest.raises(ValueError, match="grade"):
        runtime.verify_scored_submission(task, submission, group, {"score": .7, "points": []})

def ledger_fixture(tmp_path):
    directory = tmp_path / "session"
    directory.mkdir()
    messages = [{"type": "human", "content": "synthetic unit input"}]
    output = {"generations": [[{"message": {"type": "ai", "content": "answer",
               "usage_metadata": {"input_tokens": 5, "output_tokens": 7}}}]], "llm_output": {}}
    (directory / "raw-trace.jsonl").write_text(json.dumps({"kind": "model_input", "value": [messages]}) + "\n" +
                         json.dumps({"kind": "model_output", "value": output}) + "\n")
    request = {"call": 1, "started_at": "2026-10-12T10:00:00Z", "reserved_upper_cny": .001}
    final = {**request, "http_status": 200, "finished_at": "2026-10-12T10:00:01Z",
             "observed_model": "configured-model", "request_sha256": "a" * 64,
             "usage": {"prompt_tokens": 5, "completion_tokens": 7}}
    (tmp_path / "model-calls.jsonl").write_text(json.dumps(request) + "\n" + json.dumps(final) + "\n")
    result = {"model_identity": {"model_id": "configured-model"}, "usage": {"model_calls": 1,
              "input_tokens": 5, "output_tokens": 7, "usage_complete": True, "actual_cost_cny": None}}
    scope = {"directory": str(directory), "model_calls_before": 0, "model_calls_after": 1}
    return result, scope

def test_gateway_callback_reconciliation_preserves_nonzero_calls(tmp_path):
    result, scope = ledger_fixture(tmp_path)
    assert runtime.verify_model_ledger(tmp_path, result, [scope])["model_calls"] == 1

@pytest.mark.parametrize("mutation", ["empty_callback", "usage_mismatch", "no_gateway", "no_scope_call", "failed_http"])
def test_gateway_callback_reconciliation_rejects_false_evidence(tmp_path, mutation):
    result, scope = ledger_fixture(tmp_path)
    trace = Path(scope["directory"]) / "raw-trace.jsonl"
    if mutation == "empty_callback":
        trace.write_text("")
    elif mutation == "usage_mismatch":
        result["usage"]["output_tokens"] = 8
    elif mutation == "no_gateway":
        (tmp_path / "model-calls.jsonl").write_text("")
    elif mutation == "no_scope_call":
        scope["model_calls_after"] = 0
    else:
        log = (tmp_path / "model-calls.jsonl").read_text().replace('"http_status": 200', '"http_status": 503')
        (tmp_path / "model-calls.jsonl").write_text(log)
    with pytest.raises(ValueError):
        runtime.verify_model_ledger(tmp_path, result, [scope])

def test_completed_arithmetic_source_does_not_claim_semantic_contribution(tmp_path):
    result = runtime.verify(forged_calibration(tmp_path))
    assert result["computation_contribution"] == "unassessed"
    assert result["learning_gain_proven"] is False

