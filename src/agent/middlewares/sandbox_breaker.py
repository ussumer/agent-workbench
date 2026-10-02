"""A breaker for the sandbox, and only for the sandbox.

The distinction this module exists to make: **a business rejection is not an infrastructure
failure.** ``UNSUPPORTED_PART`` means the ERP said no; it says nothing about whether the
container is healthy. Counting it would let a user trip the sandbox breaker by repeatedly
asking for something unsupported — an outage caused by their own input.

So errors are classified first, and only the sandbox class feeds the failure counter. The
other classes are still counted, separately, because "why did this run stop" needs an answer
and "not the breaker" is a real answer.

The three states are the usual ones, with one property that matters for a long task:

``closed`` → ``open``
    Consecutive sandbox failures reach the threshold. Calls are refused immediately instead
    of each paying the timeout again.
``open`` → ``half_open``
    After a cooldown, exactly one probe is allowed through. Not "the next call" — *one*
    call, so a burst of concurrent work cannot all probe at once and fail together.
``half_open`` → ``closed`` / ``open``
    The probe decides. Success closes the breaker; failure reopens it with the cooldown
    restarted.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

LOGGER = logging.getLogger("rush_harness.middleware.breaker")

BreakerState = Literal["closed", "open", "half_open"]

#: Failures that mean the sandbox itself is unwell.
SANDBOX_ERROR_CODES: frozenset[str] = frozenset(
    {
        "SANDBOX_UNAVAILABLE",
        "SANDBOX_TIMEOUT",
        "CONTAINER_GONE",
        "CONTAINER_RECOVERY_FAILED",
        "TRANSPORT_ERROR",
        "COMMAND_TIMEOUT",
        "EXEC_TIMEOUT",
        "SANDBOX_NOT_REGISTERED",
    }
)

#: Failures that are answers, not outages. None of these may trip the breaker.
BUSINESS_ERROR_CODES: frozenset[str] = frozenset(
    {
        "UNSUPPORTED_PART",
        "INACTIVE_SUPPLIER",
        "INACTIVE_PART",
        "PART_NOT_FOUND",
        "ORDER_NOT_FOUND",
        "VERSION_CONFLICT",
        "IDEMPOTENCY_CONFLICT",
        "INVALID_ARGUMENT",
        "APPROVAL_REQUIRED",
        "APPROVAL_REJECTED",
        "APPROVAL_EXPIRED",
        "PARAMETERS_CHANGED",
        "ALREADY_DECIDED",
        "GRANT_MISMATCH",
        "FORBIDDEN",
        "UNAUTHORIZED",
    }
)


def classify(code: str) -> Literal["sandbox", "business", "unknown"]:
    """Which kind of failure a code is. Unknown codes count as neither."""
    normalised = (code or "").strip().upper()
    if normalised in SANDBOX_ERROR_CODES:
        return "sandbox"
    if normalised in BUSINESS_ERROR_CODES:
        return "business"
    return "unknown"


@dataclass(frozen=True)
class BreakerConfig:
    """Thresholds, all explicit so a test can lower them without touching the code."""

    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    half_open_probes: int = 1


@dataclass
class BreakerSnapshot:
    state: BreakerState
    consecutive_failures: int
    sandbox_failures: int
    business_failures: int
    unknown_failures: int
    probes_allowed: int
    opened_count: int
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "sandbox_failures": self.sandbox_failures,
            "business_failures": self.business_failures,
            "unknown_failures": self.unknown_failures,
            "probes_allowed": self.probes_allowed,
            "opened_count": self.opened_count,
            "reason": self.reason,
        }


class SandboxBreaker:
    """Tracks sandbox health and decides whether the next call may proceed."""

    def __init__(
        self,
        config: BreakerConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config or BreakerConfig()
        self._lock = threading.RLock()
        self._clock = clock
        self._state: BreakerState = "closed"
        self._consecutive = 0
        self._opened_at = 0.0
        self._probes_left = 0
        self._opened_count = 0
        self._counts = {"sandbox": 0, "business": 0, "unknown": 0}
        self._reason = ""

    # ------------------------------------------------------------------ queries

    @property
    def state(self) -> BreakerState:
        """Current state, advancing ``open`` → ``half_open`` when the cooldown has passed."""
        with self._lock:
            if self._state == "open" and self._cooldown_elapsed():
                self._state = "half_open"
                self._probes_left = self._config.half_open_probes
                self._reason = "冷却结束，允许一次探测"
                LOGGER.info("sandbox breaker half-open; one probe allowed")
            return self._state

    def allows(self) -> bool:
        """Whether a call may proceed, consuming a probe slot when half-open."""
        with self._lock:
            current = self.state
            if current == "closed":
                return True
            if current == "half_open" and self._probes_left > 0:
                self._probes_left -= 1
                LOGGER.info("sandbox breaker probe dispatched (%d left)", self._probes_left)
                return True
            return False

    def snapshot(self) -> BreakerSnapshot:
        with self._lock:
            return BreakerSnapshot(
                state=self.state,
                consecutive_failures=self._consecutive,
                sandbox_failures=self._counts["sandbox"],
                business_failures=self._counts["business"],
                unknown_failures=self._counts["unknown"],
                probes_allowed=self._probes_left,
                opened_count=self._opened_count,
                reason=self._reason,
            )

    def admit(self) -> int | None:
        """Atomically reserve a call and identify the circuit epoch it belongs to."""
        with self._lock:
            return self._opened_count if self.allows() else None

    # ------------------------------------------------------------------ updates

    def record_failure(self, code: str, *, expected_opened_count: int | None = None) -> BreakerSnapshot:
        """Record one failure. Only the sandbox class moves the breaker."""
        with self._lock:
            kind = classify(code)
            self._counts[kind] += 1
            if expected_opened_count is not None and expected_opened_count != self._opened_count:
                return self.snapshot()

            if kind != "sandbox":
                self._reason = f"{code} 属于 {kind} 失败，不计入沙箱熔断"
                LOGGER.info("failure %s classified as %s; breaker untouched", code, kind)
                return self.snapshot()

            self._consecutive += 1
            if self.state == "half_open" or self._consecutive >= self._config.failure_threshold:
                self._trip(f"连续 {self._consecutive} 次沙箱失败（最后：{code}）")
            else:
                self._reason = f"沙箱失败 {self._consecutive}/{self._config.failure_threshold}：{code}"
            return self.snapshot()

    def record_success(self, *, expected_opened_count: int | None = None) -> BreakerSnapshot:
        """One successful call clears the counter and closes the breaker."""
        with self._lock:
            if expected_opened_count is not None and expected_opened_count != self._opened_count:
                return self.snapshot()
            was = self._state
            self._consecutive = 0
            self._probes_left = 0
            self._state = "closed"
            self._reason = "调用成功"
            if was != "closed":
                LOGGER.info("sandbox breaker closed after a successful call")
            return self.snapshot()

    def reset(self) -> BreakerSnapshot:
        """Clear the counters outright. For a deliberate recovery, not for a retry."""
        with self._lock:
            self._consecutive = 0
            self._probes_left = 0
            self._state = "closed"
            self._reason = "已重置"
            return self.snapshot()

    def record_ignored(self, *, expected_opened_count: int | None = None) -> None:
        """A local/path refusal neither proves health nor consumes the only probe forever."""
        with self._lock:
            if expected_opened_count is not None and expected_opened_count != self._opened_count:
                return
            if self._state == "half_open":
                self._probes_left = min(self._probes_left + 1, self._config.half_open_probes)

    # ------------------------------------------------------------------ helpers

    def _cooldown_elapsed(self) -> bool:
        return (self._clock() - self._opened_at) >= self._config.cooldown_seconds

    def _trip(self, reason: str) -> None:
        if self._state != "open":
            self._opened_count += 1
        self._state = "open"
        self._opened_at = self._clock()
        self._probes_left = 0
        self._reason = reason
        LOGGER.warning("sandbox breaker opened: %s", reason)


@dataclass
class BreakerRegistry:
    """One breaker per user.

    Per user, not per process: one user's broken container must not stop another user's
    work, and the registry is the place that decision is expressed.
    """

    config: BreakerConfig = field(default_factory=BreakerConfig)
    clock: Callable[[], float] = time.monotonic
    _breakers: dict[str, SandboxBreaker] = field(default_factory=dict)
    _lock: Any = field(default_factory=threading.RLock, repr=False)

    def for_user(self, owner_user_id: str) -> SandboxBreaker:
        with self._lock:
            breaker = self._breakers.get(owner_user_id)
            if breaker is None:
                breaker = SandboxBreaker(self.config, clock=self.clock)
                self._breakers[owner_user_id] = breaker
            return breaker

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {owner: breaker.snapshot().as_dict() for owner, breaker in self._breakers.items()}


__all__ = [
    "BUSINESS_ERROR_CODES",
    "SANDBOX_ERROR_CODES",
    "BreakerConfig",
    "BreakerRegistry",
    "BreakerSnapshot",
    "BreakerState",
    "SandboxBreaker",
    "classify",
]
