"""Configuration, capability checks and secret redaction for the Harness scripts.

Two rules drive this module:

1. Secrets are only ever read from the local environment / `.env`. They are never
   printed, logged or written into evidence; `redact` strips known secret values
   out of captured command output.
2. Toolchain locations are resolved from the environment first and auto-detected
   second. Nothing here rewrites the user's machine configuration.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REDACTION_MARKER = "***REDACTED***"

# Any environment variable whose name contains one of these fragments is treated
# as a secret for redaction purposes.
SECRET_NAME_FRAGMENTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "PWD",
    "API_KEY",
    "APIKEY",
    "CREDENTIAL",
    "PRIVATE_KEY",
)

# Canonical capability -> accepted environment variable names (first match wins).
CAPABILITIES: dict[str, tuple[tuple[str, ...], ...]] = {
    # Each capability is a list of "one of these names must be set" groups.
    "model": (
        ("MODEL_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"),
        ("MODEL_BASE_URL", "OPENAI_BASE_URL"),
        ("MODEL_ID", "MODEL_NAME"),
    ),
    "search": (("ZHIPU_API_KEY",),),
    "chart": (("MODELSCOPE_MCP_URL",), ("MODELSCOPE_API_TOKEN",)),
    "mongo": (("MONGODB_URI",),),
    "erp": (("ERP_BASE_URL",),),
    "mcp": (("MCP_BASE_URL",),),
    "sandbox": (("OPENSANDBOX_BASE_URL",), ("OPENSANDBOX_API_KEY",)),
}

# Human-readable capability descriptions used in blocked/doctor output.
#
# Capabilities are split by consequence. Credentials have no default and their
# absence genuinely blocks a task; local service addresses do have a documented
# localhost fallback, so "unset" there means "using the local default", which must
# not be confused with "cannot run".
CREDENTIAL_CAPABILITIES = frozenset({"model", "search", "chart", "sandbox"})

CAPABILITY_LABELS = {
    "model": "真实模型凭据（MODEL_* 或 OPENAI_*）",
    "search": "智谱搜索凭据（ZHIPU_API_KEY）",
    "chart": "ModelScope 图表凭据（MODELSCOPE_MCP_URL / MODELSCOPE_API_TOKEN）",
    "mongo": "MongoDB（MONGODB_URI；未配置时用本机默认 localhost:27017）",
    "erp": "Java ERP（ERP_BASE_URL；未配置时用本机默认 http://localhost:8080）",
    "mcp": "MCP 网关（MCP_BASE_URL；未配置时用本机默认 http://localhost:8000）",
    "sandbox": "OpenSandbox（OPENSANDBOX_BASE_URL / OPENSANDBOX_API_KEY）",
}


def capability_is_credential(name: str) -> bool:
    """Whether a capability depends on a secret the operator must supply."""
    return name in CREDENTIAL_CAPABILITIES


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Return the merged environment, with `.env` values layered underneath.

    The real process environment wins over `.env` so an operator can override a
    file value for a single run.
    """
    merged: dict[str, str] = {}
    env_file = path or (ROOT / ".env")
    if env_file.is_file():
        try:
            from dotenv import dotenv_values
        except ImportError:  # pragma: no cover - dotenv is a locked dependency
            merged.update(_parse_dotenv(env_file))
        else:
            merged.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    merged.update(os.environ)
    return merged


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser used only when python-dotenv is unavailable."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def is_secret_name(name: str) -> bool:
    upper = name.upper()
    return any(fragment in upper for fragment in SECRET_NAME_FRAGMENTS)


def secret_values(env: dict[str, str]) -> set[str]:
    """Collect secret strings that must never appear in evidence.

    Short values are ignored to avoid redacting ordinary words by accident.
    """
    values: set[str] = set()
    for name, value in env.items():
        if is_secret_name(name) and value and len(value) >= 6:
            values.add(value)
    return values


def redact(text: str, secrets: set[str]) -> str:
    """Replace every known secret occurrence in ``text``."""
    redacted = text
    for secret in sorted(secrets, key=len, reverse=True):
        if secret in redacted:
            redacted = redacted.replace(secret, REDACTION_MARKER)
    return redacted


def capability_status(env: dict[str, str], capability: str) -> tuple[bool, list[str]]:
    """Return ``(satisfied, missing_group_labels)`` for one capability."""
    groups = CAPABILITIES[capability]
    missing: list[str] = []
    for group in groups:
        if not any(env.get(name) for name in group):
            missing.append(" / ".join(group))
    return (not missing, missing)


def missing_config(env: dict[str, str], capabilities: list[str]) -> list[str]:
    """List every unsatisfied capability group across the requested capabilities."""
    problems: list[str] = []
    for capability in capabilities:
        satisfied, missing = capability_status(env, capability)
        if not satisfied:
            label = CAPABILITY_LABELS.get(capability, capability)
            problems.append(f"{label}: missing {'; '.join(missing)}")
    return problems


def _is_java_home(path: Path) -> bool:
    return (path / "bin" / "java.exe").is_file() or (path / "bin" / "java").is_file()


def resolve_java_home(env: dict[str, str]) -> Path | None:
    """Locate a usable JDK home directory.

    ``javac`` (not ``java``) decides usability: a JRE cannot build the ERP.
    """
    candidates: list[Path] = []
    raw = env.get("JAVA_HOME")
    if raw:
        candidates.append(Path(raw))
    javac = shutil.which("javac", path=env.get("PATH"))
    if javac:
        candidates.append(Path(javac).resolve().parent.parent)
    if os.name == "nt":
        candidates.extend(_windows_jdk_candidates())
    for candidate in candidates:
        try:
            if _is_java_home(candidate) and (candidate / "bin" / "javac.exe").is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def _windows_jdk_candidates() -> list[Path]:
    roots = [
        Path("C:/Program Files/Java"),
        Path("C:/Program Files/Eclipse Adoptium"),
        Path("C:/Program Files/Microsoft"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Eclipse Adoptium",
    ]
    found: list[Path] = []
    for root in roots:
        try:
            if root.is_dir():
                found.extend(sorted(p for p in root.iterdir() if p.is_dir()))
        except OSError:
            continue
    return found


def resolve_node_home(env: dict[str, str]) -> Path | None:
    """Locate a Node installation directory (the one containing ``node``)."""
    raw = env.get("NODE_HOME")
    candidates: list[Path] = []
    if raw:
        candidates.append(Path(raw))
    node = shutil.which("node", path=env.get("PATH"))
    if node:
        candidates.append(Path(node).resolve().parent)
    candidates.extend(_nvm_candidates(env))
    for candidate in candidates:
        if (candidate / "node.exe").is_file() or (candidate / "node").is_file():
            return candidate.resolve()
    return None


def _nvm_candidates(env: dict[str, str]) -> list[Path]:
    """Enumerate nvm-managed Node directories, newest version first.

    Installed-but-unlinked Node versions live here when nvm's active symlink is
    missing; we prefer them over downloading anything.
    """
    bases = [
        Path(env.get("NVM_HOME", "")) if env.get("NVM_HOME") else None,
        Path(env.get("LOCALAPPDATA", "")) / "nvm" if os.name == "nt" else None,
        Path.home() / ".nvm" / "versions" / "node",
    ]
    found: list[Path] = []
    for base in bases:
        if base is None:
            continue
        try:
            if not base.is_dir():
                continue
            for child in base.iterdir():
                if not child.is_dir():
                    continue
                if child.name.startswith("v") and child.name[1:2].isdigit():
                    found.append(child)
                elif (child / "bin").is_dir():
                    found.extend(p for p in child.iterdir() if p.is_dir())
        except OSError:
            continue
    return sorted(found, key=_version_key, reverse=True)


def _version_key(path: Path) -> tuple[int, ...]:
    digits = "".join(ch if ch.isdigit() else "." for ch in path.name)
    parts = [int(p) for p in digits.split(".") if p.isdigit()]
    return tuple(parts) if parts else (0,)


def resolve_tool(program: str, env: dict[str, str]) -> str | None:
    """Locate a bare program name using the given environment's PATH.

    ``subprocess`` searches the *parent* process PATH even when a custom
    environment is supplied, so scripts must resolve bare names themselves.
    """
    path = env.get("PATH")
    if path:
        found = shutil.which(program, path=path)
        if found:
            return found
    return shutil.which(program)


def _extra_tool_dirs(env: dict[str, str]) -> list[Path]:
    """Directories that ship tooling the manifest calls by bare name.

    The manifest invokes ``uv`` and ``npm`` unqualified. On Windows those live in
    per-user install locations that are not always on the inherited PATH, so we
    prepend them when present instead of failing the check for an environment
    reason the plan never intended to test.
    """
    dirs: list[Path] = []
    local_bin = Path(env.get("USERPROFILE", str(Path.home()))) / ".local" / "bin"
    if (local_bin / "uv.exe").is_file() or (local_bin / "uv").is_file():
        dirs.append(local_bin)
    return dirs


def build_env(env: dict[str, str]) -> dict[str, str]:
    """Return a subprocess environment with resolved toolchain locations on PATH."""
    result = dict(env)
    java_home = resolve_java_home(env)
    if java_home:
        result["JAVA_HOME"] = str(java_home)

    prepend: list[Path] = []
    node_home = resolve_node_home(env)
    if node_home:
        result["NODE_HOME"] = str(node_home)
        prepend.append(node_home)
    prepend.extend(_extra_tool_dirs(env))

    if prepend:
        parts = [str(p) for p in prepend]
        existing = result.get("PATH", "")
        if existing:
            parts.append(existing)
        result["PATH"] = os.pathsep.join(parts)
    return result
