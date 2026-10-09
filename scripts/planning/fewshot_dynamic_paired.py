"""T69: identical gold-derived skills/examples; static versus per-turn selection.
Reuses T65 compute Actor, never its learning or failed T68 experiment.
"""
from __future__ import annotations
import argparse
import dataclasses
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT, ROOT / "src", ROOT / "tests", Path("/mnt/c/dev/rsi-eval")):
    if str(folder) not in sys.path: sys.path.insert(0, str(folder))
SOURCE = ROOT / "artifacts/experiments/t65-compute-dense-20261007"
OUTPUT = ROOT / "artifacts/experiments/t69-fewshot-dynamic-20261009"
ARMS = ("static", "dynamic")

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def verify_manifest(directory):
    directory = Path(directory).resolve()
    for relative, sha in json.loads((directory / "manifest.json").read_text()).items():
        target = (directory / relative).resolve()
        if not target.is_relative_to(directory) or digest(target) != sha:
            raise ValueError("manifest mismatch: " + relative)

def injection(arm, bank, examples):
    if arm not in ARMS: raise ValueError("unknown arm")
    return {"dynamic_bank": bank if arm == "dynamic" else None,
            "static": bank if arm == "static" else None, "system_suffix": examples}

def expected_keys(tasks, repeats):
    if repeats != 3: raise ValueError("T69 requires three declared repeats")
    return {(r, a, t["task_id"]) for r in range(1, repeats + 1) for a in ARMS for t in tasks}

def summarize(rows):
    return {a: {"count": sum(r["arm"] == a for r in rows),
                "mean_score": sum(r["grade"]["score"] for r in rows if r["arm"] == a) / max(1, sum(r["arm"] == a for r in rows)),
                "business_success": sum(bool(r["grade"]["business_success"]) for r in rows if r["arm"] == a)} for a in ARMS}

def inputs(source=SOURCE):
    from scripts.planning import gdpevo_v5_compute_training as base
    public, training, control, answers, train, test = base.load_inputs()
    manifest = json.loads((source / "manifest.json").read_text())
    for relative in ("fewshot-skills.json", "protocol.json"):
        if digest(source / relative) != manifest[relative]: raise ValueError("T65 source changed")
    for relative in ("fixtures/planning/gdpevo-procurement-v3.json", "fixtures/planning/gdpevo-procurement-v3-training.json", "fixtures/planning/private/gdpevo-procurement-v3-control.json"):
        frozen = "frozen/" + Path(relative).name
        if digest(ROOT / relative) != manifest[frozen] or digest(source / frozen) != manifest[frozen]:
            raise ValueError("taskset differs from T65")
    bank = json.loads((source / "fewshot-skills.json").read_text())
    # Compare current gold examples to T65's actual frozen Curator inputs.
    by_id = {t["task_id"]: t for t in train}
    for group in bank:
        relative = "fewshot-curator-input/" + group + ".json"
        if digest(source / relative) != manifest[relative]: raise ValueError("T65 gold source changed")
        material = json.loads((source / relative).read_text())
        for item in material["train"]:
            if item["input"] != by_id[item["task_id"]]["input"] or item["correct_answer"] != answers["tasks"][item["task_id"]][0]:
                raise ValueError("gold examples differ from T65")
    return base, public, training, control, answers, train, test, bank

def prepare(output=OUTPUT):
    output = Path(output).resolve()
    base, public, training, control, answers, train, test, bank = inputs()
    output.mkdir(parents=True, exist_ok=False)
    (output / "frozen").mkdir()
    for relative in (*base.FROZEN_SOURCES, "fixtures/planning/private/gdpevo-procurement-v3-answers.json", "scripts/planning/fewshot_dynamic_paired.py"):
        shutil.copy2(ROOT / relative, output / "frozen" / Path(relative).name)
    write(output / "fewshot-skills.json", bank)
    cases = []
    for task in train + test:
        skills = base.skill_for_task(bank, task)
        suffix = base.dense_example_suffix(task, train, answers)
        cases.append({"task_id": task["task_id"], "skills": skills, "examples": suffix})
    write(output / "matched-inputs.json", cases)
    write(output / "protocol.json", {"task":"T69", "source":str(SOURCE), "source_manifest_sha256":digest(SOURCE / "manifest.json"),
        "arms":ARMS, "test_count":20, "repeats":3, "train_recheck_count":20,
        "curator_calls":0, "learning":False, "test_feedback":False,
        "production_assignment_changed":False, "promotion":False,
        "test_role":"previously inspected development held-out; not sealed",
        "contrast":"identical fewshot bank and complete same-group train examples; injection mode only",
        "source_hashes":{p.name:digest(p) for p in (output / "frozen").iterdir()}})
    print("Prepared matched knowledge inputs; model_calls=0", flush=True)
    return output

def run(output=OUTPUT):
    from scripts.planning import gdpevo_v5_compute_training as base
    output = Path(output).resolve()
    if not output.exists(): prepare(output)
    if (output / "rows.json").exists(): raise FileExistsError("attempt already executed; preserve it")
    base, public, training, control, answers, train, test, bank = inputs()
    protocol = json.loads((output / "protocol.json").read_text())
    for name, sha in protocol["source_hashes"].items():
        if digest(output / "frozen" / name) != sha: raise ValueError("frozen source corrupted")
    if digest(Path(__file__)) != protocol["source_hashes"][Path(__file__).name]:
        raise ValueError("runner differs from prepared source; prepare new attempt")
    caller = base.UnlimitedCalls()
    # Zero-generation provider identity probe before launching business resources.
    import httpx
    with httpx.Client(timeout=30, trust_env=False) as client:
        response = client.get(caller.model.base_url.rstrip("/") + "/models", headers={"Authorization":"Bearer " + caller.model.api_key})
        write(output / "provider-preflight.json", {"http_status":response.status_code,"generation_calls":0})
        response.raise_for_status()
        identities = {m["id"] for m in response.json().get("data",[])}
        if caller.model.model_id not in identities: raise ValueError("configured model absent from provider")
    os.environ.update({k:v for k,v in caller.env.items() if v is not None})
    os.environ.setdefault("JAVA_HOME", "/tmp/demo-jdk/usr/lib/jvm/java-21-openjdk-amd64")
    base.agent_protocol_service.ENV_DIR = Path("/mnt/c/dev/rush-harness/.venv-agent-protocol-linux")
    port = int(os.environ.get("T69_SANDBOX_PORT", "18085"))
    base.sandbox_service.CONTROL_PORT = port
    protocol.update(model=caller.model.redacted(), sandbox_port=port, episode_limits={"actor_calls":30,"tools":36,"recursion":120}, selector_cost="additional, reported separately", model_policy=base.policy())
    write(output / "protocol.json", protocol)
    rows=[]
    write(output / "rows.json", rows)
    write(output / "status.json", {"status":"starting"})
    try:
        with (output / "model_calls.jsonl").open("x") as log:
            with base.gateway(caller.model, base.policy(), log, {"thinking":{"type":"disabled"}}) as (url,budget):
                configured=dataclasses.replace(caller.model,base_url=url,api_key="evaluation-proxy",max_tokens=4096)
                model=configured.create_chat_model()
                with base.sandbox_control(ROOT,output / "services/sandbox-control",port):
                    with base.running_stack(model_config=configured,run_dir=output / "services/stack",database_name="t69-"+uuid.uuid4().hex[:10],preserve_data=True,warm_pool_size=0,planning_only=True) as stack:
                        ctx=base.Context(public,training,control,stack,model,caller.secrets)
                        consecutive_failures=0
                        phases=[("train-recheck",0,train)] + [("development-test",r,test) for r in range(1,4)]
                        for phase,repeat,tasks in phases:
                            for index,task in enumerate(tasks):
                                # Alternating first arm reduces systematic ordering bias.
                                order=ARMS if (index+repeat)%2 == 0 else tuple(reversed(ARMS))
                                for arm in order:
                                    directory=output / phase / str(repeat) / arm / task["task_id"]
                                    knowledge=injection(arm,base.skill_for_task(bank,task),base.dense_example_suffix(task,train,answers))
                                    directory.mkdir(parents=True,exist_ok=False)
                                    write(directory / "knowledge.json",knowledge)
                                    print(f"Starting {phase}/{repeat}/{arm}/{task['task_id']}",flush=True)
                                    row=base.compute_episode(ctx,directory / "episode",task,control,arm,phase="t69",repeat=repeat,**knowledge)
                                    row.update(phase=phase,repeat=repeat,evidence=str(directory.relative_to(output)))
                                    rows.append(row); write(output / "rows.json",rows)
                                    write(output / "usage.json",budget.summary())
                                    print(f"Finished {arm}/{task['task_id']}: {row['status']} score={row['grade']['score']:.4f}",flush=True)
                                    consecutive_failures=consecutive_failures+1 if row["status"]=="environment_failed" else 0
                                    if consecutive_failures>=2: raise RuntimeError("two consecutive execution failures; stop and discuss")
        write(output / "report.json",{"train_recheck":summarize([r for r in rows if r['phase']=='train-recheck']),"development_test":summarize([r for r in rows if r['phase']=='development-test']),"promotion":False,"learning_gain_proven":False})
        write(output / "status.json",{"status":"completed"})
    except Exception as exc:
        write(output / "status.json",{"status":"stopped","error_type":type(exc).__name__})
        raise
    finally:
        base.write_manifest(output)


def verify(output=OUTPUT):
    output=Path(output).resolve()
    verify_manifest(output)
    base, public, training, control, answers, train, test, bank=inputs()
    rows=json.loads((output / "rows.json").read_text())
    actual={(r['repeat'],r['arm'],r['task_id']) for r in rows if r['phase']=='development-test'}
    if actual!=expected_keys(test,3) or len([r for r in rows if r['phase']=='development-test'])!=120:
        return {"status":"blocked","reason":"incomplete development pair matrix","rows":len(rows)}
    if len([r for r in rows if r['phase']=='train-recheck'])!=40:
        return {"status":"blocked","reason":"incomplete train recheck matrix"}
    tasks={t['task_id']:t for t in train+test}
    for row in rows:
        task=tasks[row['task_id']]; directory=output / row['evidence']
        expected=injection(row['arm'],base.skill_for_task(bank,task),base.dense_example_suffix(task,train,answers))
        if json.loads((directory / "knowledge.json").read_text())!=expected: raise ValueError("knowledge mismatch")
        if row['status']=='scored':
            grade=base.grade_task(task,row['decision'],control['rubrics'][task['task_id']])
            if grade!=row['grade']: raise ValueError("grade mismatch")
            trace=json.loads((directory / "episode/compute-trace.json").read_text())
            if not any(e.get('status')=='completed' for e in trace['executions']):
                return {"status":"blocked","reason":"scored row lacks real computation"}
            if row['arm']=='dynamic':
                events=trace.get('episode_export',{}).get('events',[])
                selected=[e for e in events if e['kind']=='turn_grounded']
                if not selected: raise ValueError("dynamic selector evidence missing")
    return {"status":"passed","rows":len(rows),"test_rows":120,"learning_gain_proven":False,"production_assignment_changed":False}

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['prepare','run','verify'])
    parser.add_argument('--output',type=Path,default=OUTPUT)
    args=parser.parse_args()
    if args.command=='prepare': prepare(args.output)
    elif args.command=='run': run(args.output)
    else:
        result=verify(args.output); print(json.dumps(result))
        if result['status']!='passed': raise SystemExit(2)
