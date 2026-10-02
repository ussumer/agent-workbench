"""Every application collection and index, declared in one place.

The plan asks for an explicit collection/index list rather than whatever happens
to be created at runtime, so this module is the single definition and
``ensure_application_indexes`` is idempotent (``create_index`` is a no-op when the
index already exists with the same specification).

Collections owned by the official plugins — ``checkpoints``,
``checkpoint_writes`` and ``persistent-store`` — are deliberately *not* managed
here: their indexes belong to LangGraph, and duplicating them would risk drift.
"""

from __future__ import annotations

from dataclasses import dataclass

from pymongo.database import Database

COLLECTION_THREADS = "threads"
COLLECTION_DISPLAY_MESSAGES = "display_messages"
COLLECTION_RUNS = "runs"
COLLECTION_PENDING_ACTIONS = "pending_actions"
COLLECTION_SANDBOX_REGISTRY = "sandbox_registry"

APPLICATION_COLLECTIONS: tuple[str, ...] = (
    COLLECTION_THREADS,
    COLLECTION_DISPLAY_MESSAGES,
    COLLECTION_RUNS,
    COLLECTION_PENDING_ACTIONS,
    COLLECTION_SANDBOX_REGISTRY,
)

# Collections created and indexed by langgraph-checkpoint-mongodb / langgraph-store-mongodb.
PLUGIN_COLLECTIONS: tuple[str, ...] = ("checkpoints", "checkpoint_writes", "persistent-store")

ASCENDING = 1
DESCENDING = -1


@dataclass(frozen=True)
class IndexSpec:
    """One index we depend on, with the reason it exists."""

    collection: str
    name: str
    keys: tuple[tuple[str, int], ...]
    unique: bool
    purpose: str


INDEX_SPECS: tuple[IndexSpec, ...] = (
    IndexSpec(
        collection=COLLECTION_THREADS,
        name="uk_threads_thread_id",
        keys=(("thread_id", ASCENDING),),
        unique=True,
        purpose="一个 thread 只有一条归属记录",
    ),
    IndexSpec(
        collection=COLLECTION_THREADS,
        name="ix_threads_owner_updated",
        keys=(("owner_user_id", ASCENDING), ("updated_at", DESCENDING)),
        unique=False,
        purpose="历史列表按 owner 分页、按更新时间倒序",
    ),
    IndexSpec(
        collection=COLLECTION_DISPLAY_MESSAGES,
        name="uk_display_messages_thread_message",
        keys=(("thread_id", ASCENDING), ("message_id", ASCENDING)),
        unique=True,
        purpose="展示消息按 message_id upsert，重复投递不产生重复记录",
    ),
    IndexSpec(
        collection=COLLECTION_DISPLAY_MESSAGES,
        name="ix_display_messages_thread_seq",
        keys=(("thread_id", ASCENDING), ("seq", ASCENDING)),
        unique=False,
        purpose="按显示顺序读取会话历史",
    ),
    IndexSpec(
        collection=COLLECTION_RUNS,
        name="uk_runs_run_id",
        keys=(("run_id", ASCENDING),),
        unique=True,
        purpose="run_id 全局唯一",
    ),
    IndexSpec(
        collection=COLLECTION_RUNS,
        name="uk_runs_owner_request",
        keys=(("owner_user_id", ASCENDING), ("request_id", ASCENDING)),
        unique=True,
        purpose="同一 owner 的 request_id 只能对应一个 run（请求去重）",
    ),
    IndexSpec(
        collection=COLLECTION_RUNS,
        name="ix_runs_thread_status",
        keys=(("thread_id", ASCENDING), ("status", ASCENDING)),
        unique=False,
        purpose="对账一个 thread 的进行中 run",
    ),
    IndexSpec(
        collection=COLLECTION_PENDING_ACTIONS,
        name="uk_pending_actions_interrupt_call",
        keys=(("interrupt_id", ASCENDING), ("tool_call_id", ASCENDING)),
        unique=True,
        purpose="一个待审动作只能有一条记录",
    ),
    IndexSpec(
        collection=COLLECTION_PENDING_ACTIONS,
        name="ix_pending_actions_owner_thread",
        keys=(("owner_user_id", ASCENDING), ("thread_id", ASCENDING), ("status", ASCENDING)),
        unique=False,
        purpose="按 owner 列出待审批动作",
    ),
    IndexSpec(
        collection=COLLECTION_SANDBOX_REGISTRY,
        name="uk_sandbox_registry_user",
        keys=(("user_id", ASCENDING),),
        unique=True,
        purpose="一个用户同时只有一个登记沙箱（契约要求 user_id 唯一）",
    ),
    IndexSpec(
        collection=COLLECTION_SANDBOX_REGISTRY,
        name="uk_sandbox_registry_sandbox",
        keys=(("sandbox_id", ASCENDING),),
        unique=True,
        purpose="一个沙箱只能登记给一个用户（契约要求 sandbox_id 唯一）",
    ),
    IndexSpec(
        collection=COLLECTION_SANDBOX_REGISTRY,
        name="ix_sandbox_registry_status",
        keys=(("status", ASCENDING), ("last_seen", DESCENDING)),
        unique=False,
        purpose="服务重启后按状态找回可重连的登记，并清理失效项",
    ),
)


def ensure_application_indexes(database: Database) -> list[str]:
    """Create every declared index. Returns the ``collection.index`` names touched."""
    created: list[str] = []
    for spec in INDEX_SPECS:
        database[spec.collection].create_index(
            list(spec.keys), unique=spec.unique, name=spec.name
        )
        created.append(f"{spec.collection}.{spec.name}")
    return created


def describe_indexes(database: Database) -> dict[str, list[str]]:
    """Current index names per application collection, for evidence."""
    return {
        collection: sorted(database[collection].index_information().keys())
        for collection in APPLICATION_COLLECTIONS
    }
