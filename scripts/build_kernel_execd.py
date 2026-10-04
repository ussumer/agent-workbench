#!/usr/bin/env python3
"""Build the pinned official execd with the minimal Jupyter reader repair (Linux/WSL)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = "4a9db411879601610843af9c8e03563694325b2a"
SOURCE_SHA256 = "934b517e10e20defd4d3bada8f07082632db3a5f701ed9302b27977c8d3de605"
BASE = "opensandbox/execd@sha256:0d8f44cf4194732719aa79999d4b120c98bdab02bc61e9ad13f75f83af4c2684"
IMAGE = "rush-harness/execd:1.0.22-kernel1"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", required=True, help="Go 1.25.9 Linux compiler executable")
    parser.add_argument("--source", type=Path, help="Optional exact upstream tar.gz, verified by hash")
    args = parser.parse_args()
    compiler = str(Path(args.go).resolve())
    version = subprocess.check_output([compiler, "version"], text=True).strip()
    if "go1.25.9 linux/amd64" not in version:
        raise RuntimeError("requires pinned Go 1.25.9 linux/amd64")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = ROOT / "artifacts/tasks/T38/native-builds" / stamp
    output.mkdir(parents=True)
    source = args.source or output / "source.tar.gz"
    if args.source is None:
        urllib.request.urlretrieve(
            f"https://codeload.github.com/opensandbox-group/OpenSandbox/tar.gz/{REVISION}", source)
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("upstream source hash mismatch")
    with tarfile.open(source) as archive:
        for member in archive.getmembers():
            name = member.name.split("/", 1)
            if len(name) == 2 and name[1].startswith(("components/execd/", "components/internal/")):
                member.name = name[1]
                archive.extract(member, output, filter="data")
    patch = ROOT / "infra/sandbox/patches/execd-v1.0.22-jupyter-readers.patch"
    patch_hash = hashlib.sha256(patch.read_bytes()).hexdigest()
    subprocess.run(["patch", "--batch", "--forward", "-p1", "-i", str(patch)], cwd=output, check=True)
    test = ROOT / "infra/sandbox/patches/jupyter_reader_test.go"
    test_hash = hashlib.sha256(test.read_bytes()).hexdigest()
    shutil.copyfile(test, output / "components/execd/pkg/jupyter/execute/jupyter_reader_test.go")
    env = dict(os.environ)
    env.setdefault("GOCACHE", str(output / "go-cache"))
    env.setdefault("GOPATH", str(output / "go-path"))
    module = output / "components/execd"
    with (output / "native-tests.jsonl").open("w") as report, (output / "native-tests.stderr").open("w") as error:
        subprocess.run([compiler, "test", "-race", "-json", "./pkg/jupyter/execute", "./pkg/jupyter/auth", "./pkg/jupyter/session", "-count=1", "-timeout=120s"],
                       cwd=module, env=env, stdout=report, stderr=error, check=True)
    events = [json.loads(line) for line in (output / "native-tests.jsonl").read_text().splitlines()]
    passed = sum(item.get("Action") == "pass" and bool(item.get("Test")) for item in events)
    if passed == 0 or any(item.get("Action") in {"fail", "skip"} and item.get("Test") for item in events):
        raise RuntimeError("native tests require nonzero collected tests and no failures/skips")
    env["CGO_ENABLED"] = "0"
    subprocess.run([compiler, "build", "-trimpath", "-buildvcs=false", "-o", str(output / "execd-t38"), "./main.go"],
                   cwd=module, env=env, check=True)
    (output / "Dockerfile").write_text(f"FROM {BASE}\nCOPY execd-t38 /execd\n")
    docker = shutil.which("docker") or shutil.which("docker.exe")
    if docker is None:
        raise RuntimeError("Docker CLI unavailable")
    relative = output.relative_to(ROOT).as_posix()
    subprocess.run([docker, "build", "-t", IMAGE, "-f", relative + "/Dockerfile", relative], cwd=ROOT, check=True)
    image_id = subprocess.check_output([docker, "image", "inspect", IMAGE, "--format", "{{.Id}}"], text=True).strip()
    if hashlib.sha256(patch.read_bytes()).hexdigest() != patch_hash or hashlib.sha256(test.read_bytes()).hexdigest() != test_hash:
        raise RuntimeError("patch or native tests changed during build; no provenance published")
    provenance = {"source_revision": REVISION, "source_sha256": SOURCE_SHA256, "base_image": BASE,
                  "go": version, "patch_sha256": patch_hash, "native_test_sha256": test_hash,
                  "binary_sha256": hashlib.sha256((output / "execd-t38").read_bytes()).hexdigest(),
                  "image": IMAGE, "image_id": image_id, "native_tests_passed": passed}
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
