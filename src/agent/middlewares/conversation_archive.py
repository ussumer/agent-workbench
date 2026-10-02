"""Durability hooks around the locked SDK's unchanged summarization policy."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from typing import Any

from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    SummarizationToolMiddleware,
    compute_summarization_defaults,
)
from langgraph.config import get_config

from agent.middlewares.tools_summarization import (
    PRESERVED_KEYS,
    _serialise_message,
    archive_thread_history,
)
from agent.persistence.scoped_store import UserScopedStore
from agent.runtime_context import runtime_owner


class ArchivePersistenceError(RuntimeError):
    """No summary can replace history whose durable copy could not be confirmed."""


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported archive state type: {type(value).__name__}")


class ArchivedSummarizationMiddleware(SummarizationMiddleware):
    @property
    def name(self) -> str:
        # The SDK replaces its default middleware by name, preserving stack order.
        return "SummarizationMiddleware"

    def __init__(self, model: Any, *, backend: Any, scope: str = "main", **kwargs: Any) -> None:
        super().__init__(model, backend=backend, **kwargs)
        self.archive_scope = scope
        self._archive_context: ContextVar[Any] = ContextVar(f"archive-{id(self)}", default=None)

    @contextmanager
    def archive_context(self, state: Mapping[str, Any], runtime: Any) -> Iterator[None]:
        # Capture before the SDK truncates old arguments or applies earlier summaries.
        captured = ([_serialise_message(message) for message in state.get("messages", [])],
                    json.loads(json.dumps({key: state[key] for key in PRESERVED_KEYS if key in state},
                                          default=_json_value)), runtime)
        token = self._archive_context.set(captured)
        try:
            yield
        finally:
            self._archive_context.reset(token)

    def _archive(self) -> None:
        captured = self._archive_context.get()
        if captured is None:
            raise ArchivePersistenceError("missing conversation archive context")
        messages, preserved, runtime = captured
        owner = runtime_owner(runtime)
        config = getattr(runtime, "config", None) or get_config()
        configurable = config.get("configurable") or {}
        thread = configurable.get("thread_id")
        store = getattr(runtime, "store", None)
        if not owner or not thread or store is None:
            raise ArchivePersistenceError("conversation archive requires owner, thread and durable store")
        scoped = store if isinstance(store, UserScopedStore) else UserScopedStore(store, owner)
        if scoped.user_id != owner:
            raise ArchivePersistenceError("conversation archive owner mismatch")
        try:
            archive_thread_history(scoped, thread_id=str(thread), messages=messages,
                run_id=str(configurable.get("application_run_id") or ""),
                checkpoint_ns=str(configurable.get("checkpoint_ns") or ""),
                scope=self.archive_scope, preserved_state=preserved)
        except Exception as failure:
            raise ArchivePersistenceError("conversation archive persistence failed; no summary generated") from failure

    def _create_summary(self, messages_to_summarize: Any) -> str:
        self._archive()
        return super()._create_summary(messages_to_summarize)

    async def _acreate_summary(self, messages_to_summarize: Any) -> str:
        await asyncio.to_thread(self._archive)
        return await super()._acreate_summary(messages_to_summarize)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        with self.archive_context(request.state, request.runtime):
            return super().wrap_model_call(request, handler)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        with self.archive_context(request.state, request.runtime):
            return await super().awrap_model_call(request, handler)


class ArchivedCompactionMiddleware(SummarizationToolMiddleware):
    _summarization: ArchivedSummarizationMiddleware

    def _run_compact(self, runtime: Any) -> Any:
        with self._summarization.archive_context(runtime.state, runtime):
            return super()._run_compact(runtime)

    async def _arun_compact(self, runtime: Any) -> Any:
        with self._summarization.archive_context(runtime.state, runtime):
            return await super()._arun_compact(runtime)


def build_archived_summarization(model: Any, backend: Any, *, scope: str = "main") -> ArchivedSummarizationMiddleware:
    """Use exactly the installed factory defaults, including its unbounded trim setting."""
    if isinstance(model, str):
        from deepagents._models import resolve_model

        model = resolve_model(model)
    defaults = compute_summarization_defaults(model)
    return ArchivedSummarizationMiddleware(model, backend=backend, scope=scope,
        trigger=defaults["trigger"], keep=defaults["keep"],
        truncate_args_settings=defaults["truncate_args_settings"], trim_tokens_to_summarize=None)


def build_archived_compaction(model: Any, backend: Any) -> ArchivedCompactionMiddleware:
    return ArchivedCompactionMiddleware(build_archived_summarization(model, backend))
