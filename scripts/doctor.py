#!/usr/bin/env python3
"""Environment diagnosis for the Harness demo.

Reports tool versions, resolved toolchain locations and which capabilities are
configured. It prints **names only** for configuration, never values, so its
output is safe to attach as evidence.

Example::

    python scripts/doctor.py
    python scripts/doctor.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import config  # noqa: E402


def _version(argv: list[str], env: dict[str, str], cwd: Path | None = None) -> str:
    """Return the first line of a version command, or a reason it failed."""
    executable = config.resolve_tool(argv[0], env)
    if executable is None:
        return "not found"
    try:
        completed = subprocess.run(
            [executable, *argv[1:]],
            capture_output=True,
            timeout=30,
            check=False,
            env=env,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError:
        return "not found"
    except subprocess.TimeoutExpired:
        return "timed out"
    except OSError as exc:
        return f"error: {exc}"
    raw = (completed.stdout or b"") + (completed.stderr or b"")
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return f"exit {completed.returncode} (no output)"
    return text.splitlines()[0]


def collect(root: Path) -> dict:
    env = config.build_env(config.load_dotenv())

    tools = {
        "python": _version([sys.executable, "--version"], env),
        "uv": _version(["uv", "--version"], env),
        "node": _version(["node", "--version"], env),
        "npm": _version(["npm", "--version"], env),
        "java": _version(["java", "--version"], env),
        "javac": _version(["javac", "--version"], env),
        "git": _version(["git", "--version"], env),
        "docker": _version(["docker", "--version"], env),
    }

    java_home = config.resolve_java_home(env)
    node_home = config.resolve_node_home(env)

    capabilities = {}
    for name in config.CAPABILITIES:
        satisfied, missing = config.capability_status(env, name)
        capabilities[name] = {
            "configured": satisfied,
            "missing": missing,
            "label": config.CAPABILITY_LABELS.get(name, name),
        }

    env_file = root / ".env"
    return {
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "repo_root": str(root),
        "tools": tools,
        "toolchain": {
            "java_home": str(java_home) if java_home else None,
            "node_home": str(node_home) if node_home else None,
            "maven_wrapper": (root / "erp" / "mvnw.cmd").is_file()
            or (root / "erp" / "mvnw").is_file(),
        },
        "capabilities": capabilities,
        "env_file_present": env_file.is_file(),
        "secret_names_in_env_file": _env_file_keys(env_file),
    }


def _env_file_keys(path: Path) -> list[str]:
    """List configuration key names declared in .env, never their values."""
    if not path.is_file():
        return []
    names: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        names.append(line.partition("=")[0].strip())
    return sorted(names)


def render(report: dict) -> str:
    lines = ["Harness environment doctor", "=" * 28, f"os: {report['os']}"]
    lines.append("tools:")
    for name, value in report["tools"].items():
        marker = "" if value != "not found" else "   <-- missing"
        lines.append(f"  {name:<8} {value}{marker}")
    toolchain = report["toolchain"]
    lines.append("toolchain:")
    lines.append(f"  JAVA_HOME  {toolchain['java_home'] or 'not resolved'}")
    lines.append(f"  NODE_HOME  {toolchain['node_home'] or 'not resolved'}")
    lines.append(f"  mvnw       {'present' if toolchain['maven_wrapper'] else 'missing'}")
    lines.append("capabilities (values are never printed):")
    for name, info in report["capabilities"].items():
        if info["configured"]:
            state = "configured"
        elif config.capability_is_credential(name):
            state = "MISSING " + "; ".join(info["missing"])
        else:
            # A local service address has a documented localhost fallback, so this is not a
            # blocker the way a missing credential is.
            state = "not set (local default in use): " + "; ".join(info["missing"])
        lines.append(f"  {name:<8} {state}")
    lines.append(f".env present: {report['env_file_present']}")
    if report["secret_names_in_env_file"]:
        lines.append("keys declared in .env: " + ", ".join(report["secret_names_in_env_file"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--root", default=str(config.ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    report = collect(root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))

    essential = ("python", "uv", "java", "javac", "node", "npm")
    missing = [name for name in essential if report["tools"][name] == "not found"]
    if missing:
        print(f"\nmissing essential tools: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
