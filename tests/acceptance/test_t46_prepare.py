"""Read-only verification of a real zero-generation preparation attempt."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from scripts.planning.evidence import audit


def test_real_prepare_is_frozen_and_zero_generation():
    location = os.environ.get("PLANNING_PREPARE_SESSION")
    if not location:
        pytest.fail("PLANNING_PREPARE_SESSION required; no fake prepare fallback")
    report = audit(Path(location))
    assert report["status"] == "prepared-verified"
    assert report["model_calls"] == 0
    assert report["runtime_freeze"] is not None
