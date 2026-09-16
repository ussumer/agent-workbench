"""Shared pytest configuration.

Environment workaround
----------------------
pytest keeps its temporary directories tidy by creating a ``pytest-current``
style symlink next to the numbered directory, and by resolving such links during
session cleanup. On this Windows host, traversing those reparse points raises
``OSError [WinError 448]`` ("cannot traverse a path because it contains an
untrusted mount point"), which aborts ``pytest_sessionfinish`` and the ``atexit``
cleanup *after the tests have already run*.

That bookkeeping has no bearing on whether a test passed, so it is made tolerant
here rather than allowed to turn a green suite red. Nothing about collection,
execution, assertions, reporting or exit codes is altered.
"""

from __future__ import annotations

import contextlib

import _pytest.pathlib as _pytest_pathlib
import _pytest.tmpdir as _pytest_tmpdir

_original_cleanup_dead_symlinks = _pytest_pathlib.cleanup_dead_symlinks


def _tolerant_cleanup_dead_symlinks(root) -> None:  # noqa: ANN001 - pytest internal
    """Ignore reparse-point errors raised while pruning pytest's temp symlinks."""
    with contextlib.suppress(OSError):
        _original_cleanup_dead_symlinks(root)


# `_pytest.tmpdir` imports the helper directly, so both module globals must be
# replaced for the session hook and the atexit hook to pick up the tolerant one.
_pytest_pathlib.cleanup_dead_symlinks = _tolerant_cleanup_dead_symlinks
_pytest_tmpdir.cleanup_dead_symlinks = _tolerant_cleanup_dead_symlinks
