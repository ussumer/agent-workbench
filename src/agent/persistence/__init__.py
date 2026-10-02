"""MongoDB-backed persistence: application records, namespaces and scoped access."""

from agent.persistence.indexes import (
    APPLICATION_COLLECTIONS,
    INDEX_SPECS,
    describe_indexes,
    ensure_application_indexes,
)
from agent.persistence.namespaces import (
    NamespaceViolation,
    memories_namespace,
    memory_key,
    skills_namespace,
    user_skill_key,
    user_skill_prefix,
)
from agent.persistence.repository import (
    ApplicationRepository,
    OwnershipViolation,
    RequestConflict,
    RunReservation,
    ThreadRecord,
)
from agent.persistence.scoped_store import UserScopedStore

__all__ = [
    "APPLICATION_COLLECTIONS",
    "INDEX_SPECS",
    "ApplicationRepository",
    "NamespaceViolation",
    "OwnershipViolation",
    "RequestConflict",
    "RunReservation",
    "ThreadRecord",
    "UserScopedStore",
    "describe_indexes",
    "ensure_application_indexes",
    "memories_namespace",
    "memory_key",
    "skills_namespace",
    "user_skill_key",
    "user_skill_prefix",
]
