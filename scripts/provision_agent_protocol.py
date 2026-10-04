"""Provision the standalone Agent Protocol service environment.

The official server cannot be a dependency of this project: ``langgraph-api`` 0.14.x
pins ``grpcio>=1.81,<1.82`` while ``opensandbox-server`` requires ``grpcio>=1.83``, so
no single resolution contains both. It gets its own virtual environment instead, which
also mirrors how the async agent service is actually deployed (a separate process).

Two Windows specifics this script exists to get right:

* ``uv venv`` produces a layout that points at uv's managed-python junction, which this
  host refuses to traverse (``WinError 448``). The environment must be created with the
  concrete interpreter via ``python -m venv``.
* ``colorama`` is required, because ``langgraph_api`` builds a coloured structlog
  renderer at import time and otherwise dies with a confusing uvicorn
  ``Unable to configure formatter 'simple'`` error.

Re-running is safe: the environment is recreated in place.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV_DIR = REPO / (".venv-agent-protocol" if os.name == "nt" else ".venv-agent-protocol-linux")

#: Pinned so the service is reproducible. Kept out of uv.lock on purpose.
SERVICE_REQUIREMENTS: tuple[str, ...] = (
    "langgraph-cli==0.4.31",
    "langgraph-api==0.14.1",
    "langgraph-runtime-inmem==0.34.1",
    # Required by structlog's ConsoleRenderer(colors=True) on Windows.
    "colorama>=0.4.6",
)


def find_uv() -> str | None:
    """Locate uv without relying on the inherited PATH."""
    candidates = [
        Path.home() / ".local" / "bin" / "uv.exe",
        Path.home() / ".local" / "bin" / "uv",
        Path.home() / ".cargo" / "bin" / "uv.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("uv")


def find_concrete_python(uv: str | None) -> str:
    """A real interpreter path, never a junction-based shim.

    ``uv python find`` returns the junction path on some hosts, which is exactly what
    fails here, so the versioned directory is preferred and only then falls back.
    """
    if os.name != "nt":
        return sys.executable

    versioned = (
        Path.home()
        / "AppData"
        / "Roaming"
        / "uv"
        / "python"
        / "cpython-3.12.14-windows-x86_64-none"
        / "python.exe"
    )
    if versioned.is_file():
        return str(versioned)

    if uv:
        completed = subprocess.run(
            [uv, "python", "find", "3.12"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        found = completed.stdout.strip()
        if found and Path(found).is_file() and "windows-x86_64-none" in found:
            candidate = Path(found).parent / "python.exe"
            if candidate.is_file():
                return str(candidate)

    # Last resort: the interpreter running this script.
    return sys.executable


def create_environment(python: str, env_dir: Path) -> None:
    """Create (or recreate) the isolated environment."""
    subprocess.run(
        [python, "-m", "venv", "--clear", str(env_dir)],
        check=True,
        timeout=600,
    )


def install_requirements(uv: str | None, env_dir: Path) -> None:
    """Install the service requirements into the isolated environment."""
    env_python = env_dir / "Scripts" / "python.exe"
    if not env_python.is_file():
        env_python = env_dir / "bin" / "python"

    if uv:
        lock = REPO / "infra/agent-protocol/requirements-linux.lock"
        if os.name != "nt" and lock.is_file():
            argv = [uv, "pip", "sync", "--python", str(env_python), str(lock)]
        else:
            argv = [uv, "pip", "install", "--python", str(env_python), *SERVICE_REQUIREMENTS]
    else:
        argv = [str(env_python), "-m", "pip", "install", *SERVICE_REQUIREMENTS]

    subprocess.run(argv, check=True, timeout=3600)


def installed_versions(env_dir: Path) -> dict[str, str]:
    """Report what actually landed, so the run can be recorded."""
    script = (
        "import json, importlib.metadata as md\n"
        "names = ['langgraph-cli', 'langgraph-api', 'langgraph-runtime-inmem', 'langgraph', 'grpcio']\n"
        "out = {}\n"
        "for n in names:\n"
        "    try:\n"
        "        out[n] = md.version(n)\n"
        "    except Exception:\n"
        "        out[n] = '<absent>'\n"
        "print(json.dumps(out))\n"
    )
    env_python = env_dir / "Scripts" / "python.exe"
    if not env_python.is_file():
        env_python = env_dir / "bin" / "python"
    completed = subprocess.run(
        [str(env_python), "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    import json

    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": completed.stderr.strip()[:200]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-dir", default=str(ENV_DIR))
    parser.add_argument("--reuse", action="store_true", help="keep an existing environment")
    args = parser.parse_args()

    env_dir = Path(args.env_dir)
    uv = find_uv()
    python = find_concrete_python(uv)
    print(f"uv: {uv or '<not found, falling back to pip>'}")
    print(f"interpreter: {python}")

    if env_dir.is_dir() and args.reuse:
        print(f"reusing {env_dir}")
    else:
        print(f"creating {env_dir}")
        create_environment(python, env_dir)

    print("installing:", ", ".join(SERVICE_REQUIREMENTS))
    install_requirements(uv, env_dir)

    print("installed:", installed_versions(env_dir))
    print("done. start it with:")
    launcher = env_dir / ("Scripts/langgraph.exe" if os.name == "nt" else "bin/langgraph")
    print(
        f'  "{launcher}" dev --config infra/agent-protocol/langgraph.json '
        "--port 8123 --host 127.0.0.1 --no-browser --no-reload"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
