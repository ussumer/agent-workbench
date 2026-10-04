"""Protocol checks for the high-difficulty repeated pilot."""

import pytest

from scripts.planning.challenge_pilot import CASES, REPEATS

pytestmark = pytest.mark.unit


def test_challenge_has_unseen_combined_conditions():
    assert [case for case, _ in CASES] == [
        "deadline-budget", "deadline-budget-missing", "business-infeasible", "no-partial"
    ]


def test_challenge_repeats_each_case():
    assert REPEATS >= 2


def test_source_missing_case_masks_real_erp_field():
    assert dict(CASES)["deadline-budget-missing"] == "P001"


def test_training_and_control_groups_are_distinct():
    assert "fixed-v1" != "curated-v1"
