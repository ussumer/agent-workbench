"""Publishing a skill: validation, persistence and assignment.

The pieces:

* :mod:`agent.skills.pipeline` — read a source, refuse it if it is not a skill, smoke-test it
  for real, then persist and assign it.
* :mod:`agent.skills.store` — where the files and the pointers live, and why they live in
  different stores.

Nothing here executes a skill on the host. The smoke test runs inside the owner's sandbox,
which is the same place the skill will run once it is assigned.
"""

from agent.skills.pipeline import (
    DEFAULT_STAGING_ROOT,
    MAX_ARCHIVE_BYTES,
    MAX_EXPANDED_BYTES,
    MAX_FILES,
    PreparedSkill,
    PublishResult,
    SkillPublisher,
    SkillValidationError,
    SmokeFailed,
    SourceBundle,
    ValidatedSkill,
    build_manifest,
    collect_generated,
    collect_package,
    run_smoke,
    validate_bundle,
    write_staging,
)
from agent.skills.store import (
    MANIFEST_FILENAME,
    SkillPointer,
    SkillStore,
    SkillVersion,
    content_digest,
)

__all__ = [
    "DEFAULT_STAGING_ROOT",
    "MANIFEST_FILENAME",
    "MAX_ARCHIVE_BYTES",
    "MAX_EXPANDED_BYTES",
    "MAX_FILES",
    "PreparedSkill",
    "PublishResult",
    "SkillPointer",
    "SkillPublisher",
    "SkillStore",
    "SkillValidationError",
    "SkillVersion",
    "SmokeFailed",
    "SourceBundle",
    "ValidatedSkill",
    "build_manifest",
    "collect_generated",
    "collect_package",
    "content_digest",
    "run_smoke",
    "validate_bundle",
    "write_staging",
]
