"""Environment loading and secret handling for the agent runtime.

Two rules, both inherited from the plan and enforced by the tests:

* Secrets are read only from the process environment or ``.env``. They are never
  written into receipts, fixtures, logs or version documents.
* A missing credential is an explicit, typed failure. It is never replaced by a
  default that would let a live check pass without a real provider.

Redaction is applied to anything that leaves the process: log lines, recorded
fixtures and diagnostics. Values are replaced wholesale rather than pattern-matched,
so a key cannot leak through an unexpected formatting.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

REDACTION_MARKER = "[redacted]"

#: Any environment variable whose *name* contains one of these is treated as secret.
_SECRET_NAME_MARKERS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "PRIVATE",
)

#: Longest values are replaced first so a short secret cannot partially mask a longer
#: one that contains it.
DEFAULT_DOTENV = ".env"


class MissingConfiguration(RuntimeError):
    """A required credential or address is absent.

    Carries the capability name so callers can report a blocked check rather than a
    failed one: a missing credential is not a defect in the code.
    """

    def __init__(self, capability: str, missing: Sequence[str]) -> None:
        self.capability = capability
        self.missing = list(missing)
        super().__init__(
            f"capability {capability!r} is not configured; missing: {', '.join(self.missing)}"
        )


def repo_root() -> Path:
    """Repository root, derived from this file's location."""
    return Path(__file__).resolve().parents[2]


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` file.

    Deliberately small: quoting and interpolation are not supported on purpose. A
    ``.env`` that needs them is a sign the value should live in the real environment.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name:
            continue
        values[name] = value.strip()
    return values


def load_env(
    *,
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Merge ``.env`` with the real environment, where the environment wins.

    The real environment taking precedence is what lets CI inject a credential
    without editing files, and keeps a stale ``.env`` from overriding a deployment.
    """
    base = dict(os.environ if environ is None else environ)
    dotenv_path = path or (repo_root() / DEFAULT_DOTENV)
    if dotenv_path.is_file():
        for name, value in parse_dotenv(dotenv_path.read_text(encoding="utf-8")).items():
            if value:
                base.setdefault(name, value)
    return base


def is_secret_name(name: str) -> bool:
    """Whether an environment variable name looks like it holds a secret."""
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_NAME_MARKERS)


def secret_values(env: Mapping[str, str]) -> set[str]:
    """Every value that must never be printed."""
    return {
        value
        for name, value in env.items()
        if value and is_secret_name(name)
    }


def redact(text: str, secrets: Sequence[str] | set[str]) -> str:
    """Replace every secret occurrence with the marker."""
    if not text:
        return text
    redacted = text
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        redacted = redacted.replace(secret, REDACTION_MARKER)
    return redacted


def redacted_mapping(
    mapping: Mapping[str, object], secrets: Sequence[str] | set[str]
) -> dict[str, object]:
    """Copy of a mapping with every string value redacted."""
    return {
        key: redact(value, secrets) if isinstance(value, str) else value
        for key, value in mapping.items()
    }


def safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Headers with credential-bearing values masked, for logs and fixtures."""
    masked: dict[str, str] = {}
    for name, value in headers.items():
        masked[name] = REDACTION_MARKER if is_secret_name(name) else value
    return masked


def first_present(env: Mapping[str, str], names: Sequence[str]) -> str | None:
    """First non-empty value among ``names``, or ``None``."""
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


def require(
    env: Mapping[str, str],
    groups: Sequence[Sequence[str]],
    *,
    capability: str,
) -> list[str]:
    """Return the values for one-of-each group, or raise :class:`MissingConfiguration`.

    ``groups`` mirrors the capability table in ``scripts/lib/config.py``: each inner
    sequence is "one of these names".
    """
    missing: list[str] = []
    resolved: list[str] = []
    for group in groups:
        value = first_present(env, group)
        if value is None:
            missing.append(" / ".join(group))
        else:
            resolved.append(value)
    if missing:
        raise MissingConfiguration(capability, missing)
    return resolved


#: Capability -> one-of-each groups. Kept in step with the gate's own table so a
#: capability cannot be "satisfied" for the gate but missing for the runtime.
CAPABILITY_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "model": (
        ("MODEL_API_KEY", "OPENAI_API_KEY"),
        ("MODEL_BASE_URL", "OPENAI_BASE_URL"),
        ("MODEL_ID", "MODEL_NAME"),
    ),
    "search": (("ZHIPU_API_KEY",),),
    "chart": (("MODELSCOPE_MCP_URL",), ("MODELSCOPE_API_TOKEN",)),
    # Mirrors the gate's table exactly, including the api_key group. The local sandbox
    # control service runs without one (see infra/sandbox/README.md), so this
    # capability reports unsatisfied here for the same reason the gate would.
    "sandbox": (("OPENSANDBOX_BASE_URL",), ("OPENSANDBOX_API_KEY",)),
}


def capability_satisfied(env: Mapping[str, str], capability: str) -> tuple[bool, list[str]]:
    """``(satisfied, missing_labels)`` for one capability, without raising."""
    groups = CAPABILITY_GROUPS.get(capability, ())
    missing = [
        " / ".join(group)
        for group in groups
        if first_present(env, group) is None
    ]
    return (not missing, missing)
