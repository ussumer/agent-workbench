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



def test_actor_tool_surface_excludes_operator_shutdown():
    from types import SimpleNamespace
    tools = [SimpleNamespace(name=name) for name in ("sourcing_read", "computation_execute", "computation_status", "finish_session")]
    visible = runtime.actor_tools(tools)
    assert {t.name for t in visible} == {"sourcing_read", "computation_execute", "computation_status"}
    with pytest.raises(ValueError, match="capability"):
        runtime.actor_tools(tools + [SimpleNamespace(name="read_host_file")])

def test_doctor_validates_actual_committed_mongo_probe():
    domain = {"data": {"items": [{"program_id": "PRG-021"}], "total": 150}}
    execution = {"status": "completed", "version": 1, "saved_names": ["doctor_probe"], "stdout": "42\n"}
    status = {"version": 1, "data": {"doctor_probe": {"sha256": "a" * 64}}, "last_commit": "operation"}
    restored = {"status": "completed", "stdout": "42\n"}
    assert runtime.validate_mcp_doctor(domain, execution, status, restored)["passed"] is True
    with pytest.raises(ValueError, match="Mongo"):
        runtime.validate_mcp_doctor(domain, execution, {**status, "data": {}}, restored)
    with pytest.raises(ValueError, match="42"):
        runtime.validate_mcp_doctor(domain, execution, status, {**restored, "stdout": "41\n"})

def test_trusted_business_freeze_rejects_wrong_served_seed():
    inspected = {"Id": "container", "Image": "sha256:" + "a" * 64}
    expected = {"business_image": inspected["Image"], "business_seed_sha256": "b" * 64}
    assert runtime.validate_business_identity(inspected, "b" * 64, expected)["image_id"] == inspected["Image"]
    with pytest.raises(ValueError, match="seed"):
        runtime.validate_business_identity(inspected, "c" * 64, expected)
    with pytest.raises(ValueError, match="image"):
        runtime.validate_business_identity({**inspected, "Image": "sha256:" + "c" * 64}, "b" * 64, expected)

def test_mcp_json_result_rejects_server_error():
    from types import SimpleNamespace
    result = SimpleNamespace(isError=False, structuredContent=None, content=[SimpleNamespace(type="text", text='{"value":42}')])
    assert runtime.mcp_json(result) == {"value": 42}
    with pytest.raises(ValueError, match="MCP"):
        runtime.mcp_json(SimpleNamespace(isError=True, content=[], structuredContent=None))


def test_normalized_skill_requires_actual_body():
    assert runtime.normalize_skill_body('---\nname: sourcing\ndescription: finance\n---\nWorked procurement method') == '---\nname: sourcing\ndescription: finance\n---\nWorked procurement method'
    with pytest.raises(ValueError, match='content'):
        runtime.normalize_skill_body('---\nname: sourcing\ndescription: finance\n---')


def mcp_mechanism_fixture(tmp_path):
    folder = tmp_path / "mechanism"
    scope = {"owner": "owned", "thread": "thread"}
    records = [
        {"kind": "sourcing_read", "request": {"path": "/api/sourcing/programs/PRG-021", "params": {}}, "response": {}, **scope},
        {"kind": "computation_execute", "request": {"code": "print(42)", "read_names": [], "session": "analysis", "timeout": 60}, "response": {}, **scope},
    ]
    (folder / "mcp").mkdir(parents=True)
    (folder / "mcp/tools.jsonl").write_text("\n".join(json.dumps(x) for x in reversed(records)) + "\n")
    events = [{"kind": "tool_start", "value": {"name": x["kind"], "inputs": x["request"],
               "input": json.dumps(x["request"])}} for x in records]
    (folder / "raw-trace.jsonl").write_text("\n".join(json.dumps(x) for x in events) + "\n")
    runtime.write(folder / "mcp/executions.json", [{"status": "completed", "owner_user_id": scope["owner"], "thread_id": scope["thread"]}])
    runtime.write(folder / "mcp/sandbox-inspection.json", {"container_id": "sandbox", "network_id": "private", "internal": True})
    runtime.write(folder / "mcp/cleanup.json", {"recycled": True, "container_absent": True})
    runtime.write(folder / "stdio-cleanup.json", {"closed": True})
    return folder, scope, records

def test_mcp_verifier_matches_parallel_completions_by_arguments(tmp_path):
    folder, scope, records = mcp_mechanism_fixture(tmp_path)
    assert runtime.verify_mcp_attempt(folder, scope)["completed_executions"] == 1

def test_mcp_verifier_rejects_different_request_despite_same_tool_name(tmp_path):
    folder, scope, records = mcp_mechanism_fixture(tmp_path)
    records[0]["request"]["path"] = "/api/sourcing/programs/PRG-034"
    (folder / "mcp/tools.jsonl").write_text("\n".join(json.dumps(x) for x in records) + "\n")
    with pytest.raises(ValueError, match="arguments"):
        runtime.verify_mcp_attempt(folder, scope)

def unit_run_fixture(tmp_path, monkeypatch):
    from dataclasses import dataclass
    from contextlib import contextmanager
    from types import SimpleNamespace, ModuleType
    import sys
    group = formal(tmp_path)
    output = tmp_path / "stage"
    runtime.prepare(group, output)
    config_path = tmp_path / "runtime.json"
    runtime.write(config_path, {})
    monkeypatch.setattr(runtime, "doctor", lambda *a: {"status": "ready", "reasons": []})
    @dataclass
    class FakeConfig:
        base_url: str = "test"
        api_key: str = "synthetic-test-key"
        max_tokens: int = 1
        def create_chat_model(self, *, max_retries=None):
            return SimpleNamespace(root_client=SimpleNamespace(max_retries=max_retries),
                                   root_async_client=SimpleNamespace(max_retries=max_retries))
        def redacted(self):
            return {"model_id": "synthetic-unit-model"}
    budget = SimpleNamespace(calls=0, summary=lambda: {"model_calls": budget.calls})
    @contextmanager
    def fake_gateway(*a):
        yield "unit", budget
    from scripts.planning import gdpevo_v4_gateway
    monkeypatch.setattr(gdpevo_v4_gateway, "gateway", fake_gateway)
    monkeypatch.setattr(runtime, "model_config", lambda c: (FakeConfig(), {}))
    monkeypatch.setattr(runtime, "policy", lambda c: {})
    monkeypatch.setattr(runtime, "register_profile", lambda m: None)
    @contextmanager
    def fake_control(c, directory):
        runtime.write(directory / "control-runtime.json", {"container_name": "unit-control"})
        try:
            yield "unit-private"
        finally:
            runtime.write(directory / "control-cleanup.json", {"status": "passed"})
    monkeypatch.setattr(runtime, "isolated_control", fake_control)
    monkeypatch.setattr(runtime, "business_identity", lambda c: {})
    monkeypatch.setattr(runtime, "probe_isolation", lambda *a: {})
    def fake_generation(model, directory, supervision, **kwargs):
        budget.calls += 1
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text("---\nname: fixture\ndescription: fixture\n---\nsynthetic unit body")
        return {"owner": "owner", "thread": "thread"}
    monkeypatch.setattr(runtime, "generate_skill", fake_generation)
    return runtime, output, config_path, budget


def test_run_stops_after_first_skill_missing_mandated_mechanism(tmp_path, monkeypatch):
    runtime, output, config_path, budget = unit_run_fixture(tmp_path, monkeypatch)
    result = runtime.run(output, config_path)
    assert len(result["skills"]) == 1
    assert result["skills"][0]["status"] == "failed"
    assert result["usage"]["model_calls"] == 1
    assert result["rows"] == []


def test_isolation_probe_uses_actual_configured_mongo_and_business_ports():
    config = {'sandbox_port': 18125, 'mongo_uri': 'mongodb://127.0.0.1:27017', 'business_url': 'http://task-env:9025'}
    assert runtime.isolation_ports(config) == [18125, 27017, 9025, 443]
    assert runtime.isolation_ports({**config, 'mongo_uri': 'mongodb://127.0.0.1:27500', 'business_url': 'http://task-env:9033'}) == [18125, 27500, 9033, 443]


def test_published_mcp_description_lists_actual_routes_and_query_contract():
    from types import SimpleNamespace
    description = runtime.SOURCING_READ_DESCRIPTION
    tool = SimpleNamespace(name="sourcing_read", description=description,
        inputSchema={"properties": {"path": {"type": "string"}, "params": {"type": ["object", "null"]}}})
    evidence = runtime.validate_published_sourcing_tool([tool])
    assert evidence["passed"] is True
    for route in ("programs", "forecasts", "quotes", "documents", "engineering", "contracts", "payments", "suppliers"):
        assert "/api/sourcing/" + route in description
    assert "/api/sourcing/programs/PRG-021" in description
    assert "1 <= limit <= 100" in description
    assert "request_id" in description and "items" in description

def test_published_mcp_description_rejects_old_undocumented_surface():
    from types import SimpleNamespace
    tool = SimpleNamespace(name="sourcing_read", description="Read only domain sourcing endpoints.",
        inputSchema={"properties": {"path": {}, "params": {}}})
    with pytest.raises(ValueError, match="description"):
        runtime.validate_published_sourcing_tool([tool])

def test_failure_details_preserve_unwrapped_leaf_and_redact_secret(monkeypatch):
    monkeypatch.setenv("T67_TEST_SECRET_TOKEN", "synthetic-secret-value")
    leaf = ValueError("generated SKILL content/front matter missing synthetic-secret-value")
    outer = ExceptionGroup("unhandled errors in a TaskGroup", [leaf])
    result = runtime.failure_details(outer)
    assert result["type"] == "ExceptionGroup"
    assert any(x["type"] == "ValueError" and "front matter" in x["message"] for x in result["causes"])
    assert "front matter" in result["traceback"]
    assert "synthetic-secret-value" not in json.dumps(result)

def test_public_description_does_not_accept_undocumented_aliases():
    assert runtime.validate_domain_request("/api/sourcing/programs/PRG-021", {})[0].startswith("/api/sourcing/")
    with pytest.raises(ValueError):
        runtime.validate_domain_request("/sourcing/programs/PRG-021", {})


def test_actual_mcp_registration_publishes_exact_documented_read_surface():
    import asyncio
    from mcp.server.fastmcp import FastMCP
    server = FastMCP('t67-schema-registration-test')
    runtime.register_sourcing_read(server, {}, lambda *args: None)
    tools = asyncio.run(server.list_tools())
    assert runtime.validate_published_sourcing_tool(tools)['passed'] is True
    assert tools[0].description == runtime.SOURCING_READ_DESCRIPTION
    assert {'path', 'params'} <= set(tools[0].inputSchema['properties'])

def test_callback_usage_reads_actual_run1_langchain_token_usage():
    value = {'llm_output': {'token_usage': {'prompt_tokens': 41384, 'completion_tokens': 338,
        'total_tokens': 41722, 'prompt_cache_hit_tokens': 41216}},
        'generations': [[{'message': {'content': '', 'type': 'ai'}}]]}
    assert runtime.callback_usage(value) == (41384, 338)

@pytest.mark.parametrize('value', [
    {'llm_output': {'usage': {'prompt_tokens': 5, 'completion_tokens': 7}}},
    {'llm_output': {}, 'generations': [[{'message': {'usage_metadata': {'input_tokens': 5, 'output_tokens': 7}}}]]}
])
def test_callback_usage_retains_existing_wire_schema_compatibility(value):
    assert runtime.callback_usage(value) == (5, 7)

@pytest.mark.parametrize('bad', [-1, '338', None])
def test_callback_token_usage_still_rejects_invalid_tokens(bad):
    value = {'llm_output': {'token_usage': {'prompt_tokens': 41384, 'completion_tokens': bad}},
             'generations': [[{'message': {'content': '', 'type': 'ai'}}]]}
    with pytest.raises(ValueError, match='usage'):
        runtime.callback_usage(value)


def test_skill_normalizer_accepts_intro_with_nested_bare_code_pairs():
    body = '---\nname: headlamp-sourcing-decision\ndescription: >\n  Compare sourcing evidence.\n---\n# Method\n\n```\nvalue = 6 * 7\n```\n\nPreserve the complete method.\n\n```\nprint(value)\n```\n\nFinal conclusion.'
    raw = "Verified. Now I'll produce the SKILL.md.\n\n```markdown\n" + body + "\n```"
    assert runtime.normalize_skill_body(raw).encode('utf-8') == body.encode('utf-8')
def test_skill_normalizer_preserves_nested_python_fences_with_intro():
    body = '---\nname: sourcing\ndescription: finance\n---\n# Method\n\n```python\nprint(42)\n```\n\nComplete conclusion.'
    raw = "Verified output follows.\n\n```markdown\n" + body + "\n```\n"
    assert runtime.normalize_skill_body(raw) == body

def test_skill_normalizer_rejects_two_complete_valid_candidates():
    body = "---\nname: sourcing\ndescription: finance\n---\nActual method."
    raw = "Choose one:\n```markdown\n" + body + "\n```\nOr:\n```md\n" + body + "\n```"
    with pytest.raises(ValueError, match="multiple"):
        runtime.normalize_skill_body(raw)

@pytest.mark.parametrize("raw", [
    "Intro\n```markdown\n---\nname: sourcing\ndescription: finance\n---\n```",
    "Intro\n```markdown\n---\nname: sourcing\ndescription: finance\n---\nUnterminated body.",
    "Intro\n```markdown\nNo valid front matter.\n```",
])
def test_skill_normalizer_rejects_missing_body_or_incomplete_candidate(raw):
    with pytest.raises(ValueError):
        runtime.normalize_skill_body(raw)






def test_continuation_generates_only_third_with_exact_two_bound_skills():
    assert runtime.remaining_skill_sessions([]) == [1, 2, 3]
    reused = [{"session": 1, "ledger_source": "reused-run2"},
              {"session": 2, "ledger_source": "reused-run2"}]
    assert runtime.remaining_skill_sessions(reused) == [3]
    with pytest.raises(ValueError, match="two"):
        runtime.remaining_skill_sessions(reused[:1])

def test_historical_recovery_cannot_relax_fresh_actor_stdio(tmp_path):
    folder, scope, _ = mcp_mechanism_fixture(tmp_path)
    runtime.write(folder / "stdio-cleanup.json", {"closed": False})
    with pytest.raises(ValueError, match="historical"):
        runtime.verify_mcp_attempt(folder, scope, recovery=tmp_path)

def test_historical_stdio_recovery_requires_strict_helper_validation(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    folder, scope, _ = mcp_mechanism_fixture(tmp_path)
    runtime.write(folder / "stdio-cleanup.json", {"closed": False})
    runtime.write(folder / 'mcp/cleanup.json', {'recycled': True, 'container_absent': True, 'sandbox_id': 'old-sandbox'})
    row = {**scope, 'session': 2, 'source_session': 2, 'ledger_source': 'reused-run2', 'status': 'reused_recovered'}
    helper = ModuleType("scripts.planning.gdpevo_v4_reuse")
    helper.verify_reused_skill = lambda *a: {'kind': 'operator-observed-postprocessing-recovery', 'owner': scope['owner'], 'thread': scope['thread'], 'sandbox_id': 'old-sandbox', 'historical_stdio_closed': False, 'process_absent': True, 'container_absent': True}
    monkeypatch.setitem(sys.modules, helper.__name__, helper)
    evidence = runtime.verify_mcp_attempt(folder, row, recovery=tmp_path)
    assert evidence["historical_stdio_recovery"] is True
    assert runtime.read(folder / "stdio-cleanup.json")["closed"] is False
    helper.verify_reused_skill = lambda *a: {'kind': 'operator-observed-postprocessing-recovery', 'owner': scope['owner'], 'thread': scope['thread'], 'sandbox_id': 'old-sandbox', 'historical_stdio_closed': False, 'process_absent': False, 'container_absent': True}
    with pytest.raises(ValueError, match="recovery"):
        runtime.verify_mcp_attempt(folder, row, recovery=tmp_path)

def test_continuation_ledgers_cover_scopes_separately_without_renumbering(tmp_path):
    current = tmp_path / "current"
    current.mkdir()
    historical = tmp_path / "historical"
    source_run = historical / "run"
    source_run.mkdir(parents=True)
    result, scope = ledger_fixture(current)
    old_result, old_scope = ledger_fixture(source_run)
    old_folder = source_run / 'skills/session-1'
    old_folder.mkdir(parents=True)
    (old_folder / 'raw-trace.jsonl').write_bytes((Path(old_scope['directory']) / 'raw-trace.jsonl').read_bytes())
    old_scope.update(session=1, source_session=1)
    runtime.write(source_run / "result.json", old_result)
    stage = tmp_path / "stage"
    runtime.write(stage / "reuse-source.json", {"source_stage": str(historical), "reused_usage": old_result["usage"],
                                               "prior_failed_usage": [{"source_stage": "prior-failed", "model_calls": 11}]})
    result["reused_usage"] = old_result["usage"]
    result["prior_failed_usage"] = [{"source_stage": "prior-failed", "model_calls": 11}]
    scopes = [{**scope, "ledger_source": "current"}, {**old_scope, "ledger_source": "reused-run2"}]
    from scripts.planning.gdpevo_v4_reuse import accounting
    result['usage_accounting'] = accounting(result['usage'], result['reused_usage'], result['prior_failed_usage'])
    observed = runtime.verify_origin_ledgers(stage, current, result, scopes)
    assert observed["current"]["model_calls"] == 1
    assert observed["reused-run2"]["model_calls"] == 1
    assert observed["prior_failed_usage"][0]["model_calls"] == 11
    assert result["usage"]["model_calls"] == 1
    assert {row["call"] for row in runtime.jsonl(current / "model-calls.jsonl")} == {1}
    assert {row["call"] for row in runtime.jsonl(source_run / "model-calls.jsonl")} == {1}

def test_prepare_binds_explicit_reuse_before_manifest(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    group = formal(tmp_path)
    evidence_env(group)
    config = tmp_path / "config.json"
    runtime.write(config, {"sandbox_control_image": "sha256:" + "a" * 64,
                          "business_image": "sha256:" + "b" * 64,
                          "business_seed_sha256": runtime.digest(group / "env/seed.json")})
    helper = ModuleType("scripts.planning.gdpevo_v4_reuse")
    def bind(output, source, configured, normalize, current_runtime):
        assert normalize is runtime.normalize_skill_body
        assert Path(current_runtime).name == "gdpevo_v4_taskset.py"
        runtime.write(output / "reuse-source.json", {"source_stage": str(source)})
        protocol = runtime.read(output / "protocol.json")
        protocol["reuse"] = {"source_stage": str(source)}
        runtime.write(output / "protocol.json", protocol)
    helper.bind_reuse = bind
    monkeypatch.setitem(sys.modules, helper.__name__, helper)
    output = tmp_path / "stage"
    runtime.prepare(group, output, config, reuse_from=tmp_path / "old")
    assert "reuse-source.json" in runtime.read(output / "manifest.json")
    assert len(runtime.read(output / "protocol.json")["schedule"]) == 30
    runtime.check_frozen_sources(output)



