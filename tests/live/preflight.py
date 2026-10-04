"""Refuse to call a round "live" unless it really is.

The acceptance for this task is a *live* mode: the real model against the real services. A
round that quietly fell back to a scripted model, an in-memory store or a stubbed gateway would
produce a report that reads identically and proves nothing, so the check runs before the first
turn rather than after the last one — a round that cannot be trusted must not become evidence,
and the cheapest moment to notice is before it has cost half an hour.

The record it returns is written beside the trials, and it is checked as a *record* rather than
only as a set of fields: a credential that reached it through a field nobody thought of would
still be a credential in ``artifacts/``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from agent.config import ModelConfig
from agent.env_utils import load_env, secret_values

#: Substrings that mark a model *double*. Refused in live mode (``verification.md``). Matched
#: against the configured id, so a provider that happens to use one of these words in a real
#: model name would have to be renamed here deliberately rather than silently allowed.
DOUBLE_MARKERS: tuple[str, ...] = (
    "scripted",
    "fake",
    "stub",
    "dummy",
    "mock",
    "placeholder",
    "fixture-",
)


class NotLive(RuntimeError):
    """Something about this round makes it not a live run."""


@dataclass(frozen=True)
class Preflight:
    """What was checked, so the evidence can be read without rerunning the round."""

    model_id: str
    model_host: str
    protocol: str
    temperature: float
    max_tokens: int | None
    services: dict[str, str] = field(default_factory=dict)
    secrets_checked: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "live": True,
            "model_id": self.model_id,
            "model_host": self.model_host,
            "protocol": self.protocol,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "services": dict(self.services),
            "secrets_checked": self.secrets_checked,
        }


def _host_of(address: str) -> str:
    """Scheme and host only.

    A base URL can carry a path, and a query is a place a key ends up by accident; neither
    belongs in a file kept under ``artifacts/``. The host is what makes "this really was the
    service and not a stand-in" checkable.
    """
    parts = urlsplit(address)
    return f"{parts.scheme}://{parts.netloc}" if parts.netloc else address


def assert_live(
    *,
    model_config: ModelConfig,
    addresses: Mapping[str, str],
    env: Mapping[str, str] | None = None,
) -> Preflight:
    """Check that this round is live, or explain why it is not.

    ``addresses`` are the services **this round will actually reach**, supplied by the caller
    because only the caller knows the ports its fixtures bound. Taking them from the
    configuration instead would record what was configured rather than what ran: a round whose
    ERP came up on port 11468 and whose record says 8080 has documented the wrong thing, and
    the whole point of the record is to be checkable afterwards.

    Raises :class:`NotLive` rather than warning: a round that is not live must not be written
    down as one.
    """
    resolved = dict(load_env() if env is None else env)
    problems: list[str] = []

    lowered = model_config.model_id.strip().lower()
    for marker in DOUBLE_MARKERS:
        if marker in lowered:
            problems.append(
                f"模型 ID {model_config.model_id!r} 命中替身标记 {marker!r}；"
                "live 模式不接受替身，换一个真实模型或改跑 unit/integration"
            )
            break
    if not model_config.base_url.strip():
        problems.append("模型没有配置 base_url")
    if not model_config.api_key.strip():
        problems.append("模型没有配置 api key")

    for name, address in addresses.items():
        if not str(address).strip():
            problems.append(f"外部服务 {name} 没有地址；live 轮次需要的服务必须都在")

    record = Preflight(
        model_id=model_config.model_id,
        model_host=_host_of(model_config.base_url),
        protocol=model_config.protocol,
        temperature=model_config.temperature,
        max_tokens=model_config.max_tokens,
        services={name: _host_of(str(address)) for name, address in addresses.items()},
        secrets_checked=len(secret_values(resolved)),
    )

    serialised = json.dumps(record.as_dict(), ensure_ascii=False)
    for secret in secret_values(resolved):
        if secret in serialised:
            problems.append("自检记录里出现了凭据；拒绝写下这份记录")
            break

    if problems:
        raise NotLive("；".join(problems))
    return record


__all__ = ["DOUBLE_MARKERS", "NotLive", "Preflight", "assert_live"]
