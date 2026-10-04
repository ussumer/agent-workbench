"""Uploaded to OpenSandbox; executed ONLY there, never as a host fallback.

Linux subreaper supervision includes children that create new process sessions.
The worker stages JSON; only the API's fenced Mongo CAS can publish it.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

MAX_BYTES = 4 * 1024 * 1024
_root: Path
_metadata: dict
_saved: dict = {}


def atomic_json(path: Path, data: dict) -> None:
    text = json.dumps(data, allow_nan=False)
    if len(text.encode()) > MAX_BYTES + 1024:
        raise ValueError("STATE_TOO_LARGE")
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def valid_name(name: str) -> None:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) or name.startswith("__rh_"):
        raise ValueError("INVALID_STATE_NAME")


def load_state(name: str):
    valid_name(name)
    if name not in _metadata["read_names"]:
        raise KeyError("state name was not requested for this step: " + name)
    return json.loads((_root / ("input-" + name + ".json")).read_text(encoding="utf-8"))


def save_state(name: str, value) -> None:
    valid_name(name)
    # Round-trip rejects nonfinite/non-JSON objects before touching the staged set.
    encoded = json.dumps(value, allow_nan=False)
    candidate = {**_saved, name: json.loads(encoded)}
    if len(candidate) > 32:
        raise ValueError("TOO_MANY_STATE_NAMES")
    atomic_json(_root / "staged.json", {
        "operation_id": _metadata["operation_id"], "base_version": _metadata["base_version"],
        "values": candidate,
    })
    _saved.clear()
    _saved.update(candidate)


def worker(root: Path, metadata: dict) -> None:
    global _root, _metadata
    _root, _metadata = root, metadata
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BYTES, MAX_BYTES))
    source = (root / "code.py").read_text(encoding="utf-8")
    namespace = {"__name__": "__main__", "load_state": load_state, "save_state": save_state}
    exec(compile(source, str(root / "code.py"), "exec"), namespace)
    atomic_json(root / "staged.json", {
        "operation_id": metadata["operation_id"], "base_version": metadata["base_version"],
        "values": _saved,
    })
    atomic_json(root / "worker-done.json", {"operation_id": metadata["operation_id"]})


def descendants(pid: int) -> set[int]:
    parents = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                parents[int(entry.name)] = int(stat[1])
            except (OSError, ValueError, IndexError):
                continue  # a process exited during the scan
    found = {pid}
    while True:
        expanded = found | {child for child, parent in parents.items() if parent in found}
        if expanded == found:
            return found - {pid}
        found = expanded


def supervise(root: Path, metadata: dict) -> None:
    # PR_SET_CHILD_SUBREAPER: orphaned double-forks reparent to us, including setsid.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "subreaper unavailable")
    with (root / "stdout").open("wb") as stdout, (root / "stderr").open("wb") as stderr:
        process = subprocess.Popen([sys.executable, "-u", __file__, str(root), "worker"],
                                   stdout=stdout, stderr=stderr, start_new_session=True)
        atomic_json(root / "started.json", {"supervisor_pid": os.getpid(), "worker_pid": process.pid})
        deadline = time.monotonic() + metadata["timeout"]
        status = "completed"
        while process.poll() is None:
            if (root / "cancel").exists():
                status = "cancelled"
                break
            if time.monotonic() >= deadline:
                status = "timed_out"
                break
            time.sleep(0.04)
        if status == "completed" and process.returncode != 0:
            status = "failed"
        # Kill lingering children even on success. No live process objects persist.
        seen = set()
        cleanup_deadline = time.monotonic() + 5
        clean = False
        while time.monotonic() < cleanup_deadline:
            children = descendants(os.getpid())
            seen.update(children)
            for pid in children:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.poll()
            no_children = False
            while True:
                try:
                    reaped, _ = os.waitpid(-1, os.WNOHANG)
                    if reaped == 0:
                        break
                except ChildProcessError:
                    no_children = True
                    break
            if no_children and not descendants(os.getpid()):
                clean = True
                break
            time.sleep(0.04)
        if status == "completed" and not (root / "worker-done.json").exists():
            status = "failed"
        atomic_json(root / "report.json", {
            "operation_id": metadata["operation_id"], "status": status,
            "clean": clean, "worker_pid": process.pid, "terminated_pids": sorted(seen),
        })


if __name__ == "__main__":
    execution_root = Path(sys.argv[1])
    execution_metadata = json.loads((execution_root / "metadata.json").read_text(encoding="utf-8"))
    if len(sys.argv) > 2 and sys.argv[2] == "worker":
        worker(execution_root, execution_metadata)
    else:
        supervise(execution_root, execution_metadata)
