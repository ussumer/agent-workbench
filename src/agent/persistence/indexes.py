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
#: T16: artifact metadata and artifact bytes, deliberately separate collections so that a
#: row whose bytes are gone is a state the system can report rather than conceal.
COLLECTION_ARTIFACTS = "artifacts"
COLLECTION_ARTIFACT_BLOBS = "artifact_blobs"
#: T17: published skill versions and their assignment pointers, as named by
#: docs/plan/contracts/storage-sandbox.md. The files live in the Store; these collections hold
#: the publish metadata, because moving the current pointer is a conditional update and the
#: Store has no compare-and-swap.
COLLECTION_SKILL_VERSIONS = "skill_versions"
COLLECTION_SKILL_ASSIGNMENTS = "skill_assignments"
#: T23: how many smoke runs a conversation has spent repairing one skill. It needs to outlive
#: a single tool call because the repair is the model's turn — it edits the files and calls
#: again — so the budget cannot live in the call's own stack.
COLLECTION_SKILL_SMOKE_ATTEMPTS = "skill_smoke_attempts"
#: T20: the local mapping from a background task to the Agent Protocol thread/run performing
#: it, plus the owner the Protocol service knows nothing about.
COLLECTION_ASYNC_TASKS = "async_tasks"

APPLICATION_COLLECTIONS: tuple[str, ...] = (
    COLLECTION_THREADS,
    COLLECTION_DISPLAY_MESSAGES,
    COLLECTION_RUNS,
    COLLECTION_PENDING_ACTIONS,
    COLLECTION_SANDBOX_REGISTRY,
    COLLECTION_ARTIFACTS,
    COLLECTION_ARTIFACT_BLOBS,
    COLLECTION_SKILL_VERSIONS,
    COLLECTION_SKILL_ASSIGNMENTS,
    COLLECTION_SKILL_SMOKE_ATTEMPTS,
    COLLECTION_ASYNC_TASKS,
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
    # CORRECTED in T12. The original key here was `(interrupt_id, tool_call_id)`, written
    # before the framework's HITL shape was known. Two things make it wrong: the interrupt
    # does not carry a `tool_call_id` at all (only name/args/description), so that field is
    # always empty; and an interrupt id is unique only *within* a thread. Together they made
    # every pending action collide on one key. The key that actually identifies an action is
    # the thread it was raised in plus the interrupt the user answered.
    IndexSpec(
        collection=COLLECTION_PENDING_ACTIONS,
        name="uk_pending_actions_interrupt",
        keys=(
            ("owner_user_id", ASCENDING),
            ("thread_id", ASCENDING),
            ("interrupt_id", ASCENDING),
        ),
        unique=True,
        purpose="一次中断只能有一条待审动作（双击不产生第二条），且按 thread 隔离",
    ),
    IndexSpec(
        collection=COLLECTION_PENDING_ACTIONS,
        name="ix_pending_actions_owner_thread",
        keys=(("owner_user_id", ASCENDING), ("thread_id", ASCENDING), ("status", ASCENDING)),
        unique=False,
        purpose="按 owner 列出待审批动作",
    ),
    # These two carry the T12 concurrency guarantees rather than merely speeding queries:
    # the unique index on `operation_id` is what makes a replayed node unable to mint a
    # second operation. (The interrupt uniqueness is declared just above.)
    IndexSpec(
        collection=COLLECTION_PENDING_ACTIONS,
        name="uk_pending_actions_operation",
        keys=(("operation_id", ASCENDING),),
        unique=True,
        purpose="一个待审动作只有一个稳定操作 ID（重放不生成新操作）",
    ),
    IndexSpec(
        collection=COLLECTION_PENDING_ACTIONS,
        name="ix_pending_actions_tool_call",
        keys=(("owner_user_id", ASCENDING), ("thread_id", ASCENDING), ("tool_call_id", ASCENDING)),
        unique=False,
        purpose="按 tool_call_id 找回重放的工具调用对应的审批记录",
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
    IndexSpec(
        collection=COLLECTION_ARTIFACTS,
        name="uk_artifacts_owner_artifact",
        keys=(("owner_user_id", ASCENDING), ("artifact_id", ASCENDING)),
        unique=True,
        purpose="产件按拥有者定位；重复登记同一 ID 是冲突而非更新",
    ),
    IndexSpec(
        collection=COLLECTION_ARTIFACTS,
        name="ix_artifacts_owner_thread",
        keys=(("owner_user_id", ASCENDING), ("thread_id", ASCENDING), ("created_at", ASCENDING)),
        unique=False,
        purpose="按会话列出该会话产出的产件，保持创建顺序",
    ),
    IndexSpec(
        collection=COLLECTION_ARTIFACT_BLOBS,
        name="uk_artifact_blobs_owner_artifact",
        keys=(("owner_user_id", ASCENDING), ("artifact_id", ASCENDING)),
        unique=True,
        purpose="一个产件一份字节；拥有者进键，读取时无法只凭 ID 取到别人的文件",
    ),
    IndexSpec(
        collection=COLLECTION_SKILL_VERSIONS,
        name="uk_skill_versions_identity",
        keys=(
            ("owner_user_id", ASCENDING),
            ("scope", ASCENDING),
            ("slug", ASCENDING),
            ("version", ASCENDING),
        ),
        unique=True,
        purpose="一个 owner/scope/slug 下的版本号唯一；版本不可变，重复写入是冲突",
    ),
    IndexSpec(
        collection=COLLECTION_SKILL_VERSIONS,
        name="ix_skill_versions_content",
        keys=(
            ("owner_user_id", ASCENDING),
            ("scope", ASCENDING),
            ("slug", ASCENDING),
            ("content_sha256", ASCENDING),
        ),
        unique=False,
        purpose="按内容摘要找回已有版本，实现「内容相同返回原版本」",
    ),
    IndexSpec(
        collection=COLLECTION_SKILL_ASSIGNMENTS,
        name="uk_skill_assignments_target",
        keys=(
            ("owner_user_id", ASCENDING),
            ("scope", ASCENDING),
            ("slug", ASCENDING),
        ),
        unique=True,
        purpose="一个 owner/scope/slug 只有一条当前指针；唯一索引是并发首次发布的安全网",
    ),
    IndexSpec(
        collection=COLLECTION_SKILL_SMOKE_ATTEMPTS,
        name="uk_skill_smoke_attempts_conversation",
        keys=(
            ("owner_user_id", ASCENDING),
            ("thread_id", ASCENDING),
            ("slug", ASCENDING),
        ),
        unique=True,
        purpose="一个会话里一个 slug 一行计数；唯一索引让并发的 $inc upsert 不会多插一行",
    ),
    IndexSpec(
        collection=COLLECTION_ASYNC_TASKS,
        name="uk_async_tasks_id",
        keys=(("task_id", ASCENDING),),
        unique=True,
        purpose="任务 ID 全局唯一，前端拿到的 task_id 不会串到别人的任务上",
    ),
    IndexSpec(
        collection=COLLECTION_ASYNC_TASKS,
        name="uk_async_tasks_owner_request",
        keys=(("owner_user_id", ASCENDING), ("request_id", ASCENDING)),
        unique=True,
        purpose="同一次启动请求只产生一个后台任务（双击表单不会分析两次）",
    ),
    IndexSpec(
        collection=COLLECTION_ASYNC_TASKS,
        name="ix_async_tasks_owner_parent",
        keys=(
            ("owner_user_id", ASCENDING),
            ("parent_thread_id", ASCENDING),
            ("created_at", ASCENDING),
        ),
        unique=False,
        purpose="按父会话列出该会话发起的后台任务，保持创建顺序",
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


def drop_undeclared_application_indexes(database: Database) -> list[str]:
    """Remove indexes on application collections that the plan no longer declares.

    Needed because a unique index that no longer matches the data model does not merely
    become redundant — it actively rejects valid writes. T12 corrected the key for
    ``pending_actions``; without this, a database created before the correction keeps the
    old constraint and every second pending action fails.

    Scope is limited to ``APPLICATION_COLLECTIONS``; plugin and checkpoint collections are
    left alone, and the mandatory ``_id_`` index is never touched.
    """
    declared = {(spec.collection, spec.name) for spec in INDEX_SPECS}
    removed: list[str] = []
    for collection in APPLICATION_COLLECTIONS:
        for name in database[collection].index_information():
            if name == "_id_" or (collection, name) in declared:
                continue
            database[collection].drop_index(name)
            removed.append(f"{collection}.{name}")
    return removed


def describe_indexes(database: Database) -> dict[str, list[str]]:
    """Current index names per application collection, for evidence."""
    return {
        collection: sorted(database[collection].index_information().keys())
        for collection in APPLICATION_COLLECTIONS
    }
