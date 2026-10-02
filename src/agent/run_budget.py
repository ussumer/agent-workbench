"""One invocation-local budget shared by parent and delegated LangChain callbacks."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any
from uuid import UUID

from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.callbacks import BaseCallbackHandler

from agent.middlewares.tools_summarization import BudgetConfig, RunBudget


class BudgetExceeded(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SharedRunBudget(BaseCallbackHandler):
    """Count starts before execution, including nested agents and invalid tool arguments.

    Callbacks propagate into task delegation. Graph-global mutable counters would leak
    across simultaneous threads; SDK private graph state cannot aggregate across children.
    """

    raise_error = True
    run_inline = True

    def __init__(self, *, config: BudgetConfig, repository: Any, owner: str, thread_id: str) -> None:
        self.config = replace(config)
        self.clock = RunBudget(limit_seconds=self.config.run_seconds)
        self._repository = repository
        self._owner = owner
        self._thread = thread_id
        self._lock = threading.Lock()
        self._counts = {"model": 0, "tool": 0}
        self._seen: set[tuple[str, UUID]] = set()
        self.failure: BudgetExceeded | None = None

    def expired(self) -> BudgetExceeded:
        self.failure = BudgetExceeded("RUN_TIME_BUDGET_EXCEEDED", "本次运行超过时长预算，已停止；已完成的操作不会回滚。")
        return self.failure

    def _reserve(self, kind: str, run_id: UUID) -> None:
        with self._lock:
            if self.failure is not None:
                raise self.failure
            identity = (kind, run_id)
            if identity in self._seen:
                return
            if self.clock.exhausted():
                raise self.expired()
            label = "模型" if kind == "model" else "工具"
            run_limit = getattr(self.config, f"{kind}_calls_per_run")
            if self._counts[kind] >= run_limit:
                self.failure = BudgetExceeded(f"{kind.upper()}_RUN_BUDGET_EXCEEDED", f"本次运行的{label}调用预算已用尽，已停止。")
                raise self.failure
            thread_limit = getattr(self.config, f"{kind}_calls_per_thread")
            if not self._repository.try_consume_budget_call(
                owner_user_id=self._owner, thread_id=self._thread, kind=kind, limit=thread_limit
            ):
                self.failure = BudgetExceeded(f"{kind.upper()}_THREAD_BUDGET_EXCEEDED", f"本会话的{label}调用预算已用尽，请开启新的会话。")
                raise self.failure
            self._seen.add(identity)
            self._counts[kind] += 1

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, *, run_id: UUID,
                            **kwargs: Any) -> None:
        self._reserve("model", run_id)

    def on_llm_start(self, serialized: dict[str, Any], prompts: Any, *, run_id: UUID,
                     **kwargs: Any) -> None:
        self._reserve("model", run_id)

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, *, run_id: UUID,
                      **kwargs: Any) -> None:
        self._reserve("tool", run_id)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        # A child SDK guard can raise before the model callback fires; Task ToolNode can
        # catch it. Keep an authoritative failure flag so the parent cannot report success.
        if isinstance(error, (ModelCallLimitExceededError, ToolCallLimitExceededError)):
            with self._lock:
                if self.failure is None:
                    kind = "MODEL" if isinstance(error, ModelCallLimitExceededError) else "TOOL"
                    self.failure = BudgetExceeded(f"{kind}_CALL_BUDGET_EXCEEDED", "代理调用预算已用尽，本次任务已停止。")

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return {"counts": dict(self._counts), "limits": self.config.as_dict(),
                    "elapsed_seconds": self.clock.elapsed(), "failure_code": self.failure.code if self.failure else None}
