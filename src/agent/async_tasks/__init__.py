"""Background analysis runs, driven through the official Agent Protocol SDK.

There is no worker and no queue here. A task *is* a thread and run on a separate service
process; this package holds the mapping between that run and the owner who asked for it, and
the translation between the service's run statuses and what the user is shown.

See :mod:`agent.async_tasks.service` for why an unreachable service must never be reported as
a completed task.
"""

from agent.async_tasks.service import (
    ASYNC_ANALYST_GRAPH_ID,
    AsyncTaskService,
    ProtocolClient,
    ProtocolUnavailable,
    SdkProtocolClient,
    TaskNotFound,
    build_async_task_service,
    protocol_from_settings,
)
from agent.async_tasks.store import (
    RUN_STATUS_MAP,
    TASK_ID_PREFIX,
    TERMINAL,
    AsyncStatus,
    AsyncTaskRecord,
    MongoAsyncTaskStore,
    new_task_id,
)

__all__ = [
    "ASYNC_ANALYST_GRAPH_ID",
    "RUN_STATUS_MAP",
    "TASK_ID_PREFIX",
    "TERMINAL",
    "AsyncStatus",
    "AsyncTaskRecord",
    "AsyncTaskService",
    "MongoAsyncTaskStore",
    "ProtocolClient",
    "ProtocolUnavailable",
    "SdkProtocolClient",
    "TaskNotFound",
    "build_async_task_service",
    "new_task_id",
    "protocol_from_settings",
]
