#!/usr/bin/env python3
"""One bounded diagnostic matrix, not a T38 acceptance test or production protocol."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
CODES = ("route_value = 711; print(route_value)", "print(route_value + 1)")


def child(sandbox_id: str, context_id: str) -> None:
    from code_interpreter.models.code_sync import CodeContextSync
    from opensandbox.models.execd_sync import ExecutionHandlersSync

    from agent.backends.sandbox_manager import OpenSandboxFactory
    from fixtures.sandbox_service import control_settings

    handle = OpenSandboxFactory(control_settings()).reconnect(sandbox_id)
    if handle is None:
        raise RuntimeError("diagnostic container absent")
    context = CodeContextSync(id=context_id, language="python")
    for code in CODES:
        print("sdk_request", flush=True)
        result = handle.backend.kernel_run(
            code,
            context,
            ExecutionHandlersSync(
                on_init=lambda event: print("sdk_init", flush=True),
                on_stdout=lambda event: print("sdk_stdout", event.text.strip(), flush=True),
            ),
        )
        print(
            json.dumps(
                {
                    "complete": result.complete is not None,
                    "error_name": result.error.name if result.error else None,
                }
            ),
            flush=True,
        )
        if result.error is not None or result.complete is None:
            raise SystemExit(1)


CONTAINER_PROBE = r"""
import asyncio, json, os, sys, time, uuid, urllib.request, urllib.parse
from pathlib import Path
route, context = sys.argv[1:]
codes = ("route_value = 711; print(route_value)", "print(route_value + 1)")

def http_probe():
    for code in codes:
        request = urllib.request.Request("http://127.0.0.1:44772/code", data=json.dumps({"code": code,"context":{"id":context,"language":"python"}}).encode(), headers={"Content-Type":"application/json","Accept":"text/event-stream"})
        events=[]; outputs=[]
        try:
            with urllib.request.urlopen(request,timeout=12) as response:
                for line in response:
                    text=line.decode().strip()
                    if text.startswith("data:"): text=text[5:].strip()
                    if not text: continue
                    try: event=json.loads(text)
                    except json.JSONDecodeError: continue
                    events.append(event.get("type"))
                    if event.get("type")=="stdout": outputs.append(event.get("text","").strip())
            print(json.dumps({"events":events,"stdout":outputs,"eof":True}),flush=True)
            if "execution_complete" not in events: raise RuntimeError("completion absent")
        except Exception as error:
            print(json.dumps({"events":events,"stdout":outputs,"exception_type":type(error).__name__}),flush=True); raise

async def jupyter_probe():
    from tornado.websocket import websocket_connect
    settings=dict(os.environ)
    path=Path("/opt/opensandbox/.env")
    if path.exists():
        for line in path.read_text().splitlines():
            key,sep,value=line.partition("=")
            if sep and key in ("JUPYTER_HOST","JUPYTER_TOKEN"): settings.setdefault(key,value.strip().strip("\"'"))
    base=settings["JUPYTER_HOST"].rstrip("/"); token=settings["JUPYTER_TOKEN"]
    query=urllib.parse.urlencode({"token":token})
    with urllib.request.urlopen(base+"/api/sessions?"+query,timeout=8) as response: sessions=json.load(response)
    kernel=next(item["kernel"]["id"] for item in sessions if item["id"]==context)
    session=str(uuid.uuid4()); endpoint=base.replace("http://","ws://")+"/api/kernels/"+kernel+"/channels?"+urllib.parse.urlencode({"token":token,"session_id":session})
    ws=await asyncio.wait_for(websocket_connect(endpoint),8)
    try:
        for code in codes:
            mid=str(uuid.uuid4()); outputs=[]; types=[]; idle=False; reply=False
            await ws.write_message(json.dumps({"header":{"msg_id":mid,"username":"diagnostic","session":session,"msg_type":"execute_request","version":"5.3"},"parent_header":{},"metadata":{},"content":{"code":code,"silent":False,"store_history":True,"user_expressions":{},"allow_stdin":False,"stop_on_error":True},"channel":"shell"}))
            deadline=time.monotonic()+12
            while not (idle and reply):
                raw=await asyncio.wait_for(ws.read_message(),max(.01,deadline-time.monotonic()))
                if raw is None: raise ConnectionError("channel closed")
                data=json.loads(raw)
                if data.get("parent_header",{}).get("msg_id")!=mid: continue
                kind=data["header"]["msg_type"]; types.append(kind)
                if kind=="stream": outputs.append(data["content"].get("text","").strip())
                if kind=="execute_reply": reply=True
                if kind=="status" and data["content"].get("execution_state")=="idle": idle=True
            print(json.dumps({"messages":types,"stdout":outputs,"idle_and_reply":idle and reply}),flush=True)
    finally: ws.close()

try:
    if route=="http": http_probe()
    else: asyncio.run(jupyter_probe())
except Exception as error:
    print(json.dumps({"exception_type":type(error).__name__}),flush=True)
    sys.exit(1)
"""


def main() -> None:
    import httpx

    from agent.backends.sandbox_manager import OpenSandboxFactory
    from fixtures.sandbox_service import control_settings

    output = (
        ROOT / "artifacts/tasks/T38/route-diagnostic" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    output.mkdir(parents=True)
    log = (output / "control.log").open("w", encoding="utf-8")
    env = {**os.environ, "OPENSANDBOX_INSECURE_SERVER": "YES"}
    # Refuse to disturb or reuse a user-owned control service.
    with httpx.Client(trust_env=False, timeout=1) as client:
        try:
            client.get("http://127.0.0.1:18080/health")
        except httpx.HTTPError:
            pass
        else:
            raise RuntimeError("port 18080 already in use; no diagnostic started")
    control = subprocess.Popen(
        [
            sys.executable,
            "-c",
            'import sys;sys.argv=["opensandbox-server","--config","infra/sandbox/sandbox.toml"];from opensandbox_server.cli import main;main()',
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    backend = None
    result = {"kind": "diagnostic_only", "acceptance_pass": False, "routes": []}
    try:
        deadline = time.monotonic() + 40
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                if control.poll() is not None:
                    raise RuntimeError("diagnostic control exited")
                try:
                    if client.get("http://127.0.0.1:18080/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            else:
                raise TimeoutError("control startup deadline")
        settings = replace(control_settings(), timeout_seconds=240, ready_timeout_seconds=60)
        backend = OpenSandboxFactory(settings).create().backend
        result["sandbox_id"] = backend.id
        result["runtime"] = "Windows Python / official execd v1.0.22 / one real container"
        uploads = backend.upload_files(
            [("/workspace/scratch/t38-route-probe.py", CONTAINER_PROBE.encode())]
        )
        if any(item.error is not None for item in uploads):
            raise RuntimeError("diagnostic script upload failed")
        for route in ("windows_sdk", "container_http", "jupyter"):
            context = backend.kernel_create_context()
            print(route, "started", flush=True)
            started = time.monotonic()
            item = {"route": route, "context_id": context.id}
            try:
                if route == "windows_sdk":
                    done = subprocess.run(
                        [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--sdk-child",
                            backend.id,
                            context.id,
                        ],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=35,
                    )
                    item.update(exit_code=done.returncode, stdout=done.stdout)
                    # Do not emit SDK stderr, which can contain HTTP request URLs.
                    item["stderr_present"] = bool(done.stderr)
                else:
                    command = (
                        "python /workspace/scratch/t38-route-probe.py "
                        + ("http" if route == "container_http" else "jupyter")
                        + " "
                        + shlex.quote(context.id)
                    )
                    response = backend.execute(command, timeout=35)
                    item.update(exit_code=response.exit_code, stdout=response.output)
            except subprocess.TimeoutExpired as error:
                partial = error.stdout or b""
                item.update(
                    exception_type="TimeoutExpired",
                    stdout=partial.decode("utf-8", "replace")
                    if isinstance(partial, bytes)
                    else partial,
                )
            except Exception as error:
                item["exception_type"] = type(error).__name__
            finally:
                item["elapsed_seconds"] = round(time.monotonic() - started, 2)
                result["routes"].append(item)
                (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
                print(route, json.dumps(item), flush=True)
                backend.kernel_delete_context(context.id)
    finally:
        if backend is not None:
            try:
                backend.close()
            except Exception as error:
                result["cleanup_exception_type"] = type(error).__name__
        control.terminate()
        try:
            control.wait(timeout=10)
        except subprocess.TimeoutExpired:
            control.kill()
            control.wait(timeout=5)
        log.close()
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print("evidence", output, flush=True)
    if any(item.get("exit_code") != 0 for item in result["routes"]):
        raise SystemExit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--sdk-child":
        child(*sys.argv[2:])
    else:
        main()
