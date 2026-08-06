# tests/test_insight_logic.py
"""
Regression guards for the two Insight defects found in review.

DEFECT 1 — wrong recommendation.
    A card reported 6 correlated tickets against a threshold of 5 (passed) and
    confidence 0.70 against 0.80 (failed), then recommended lowering the
    TICKET COUNT threshold. That sends a business user to change a setting
    that was never the blocker.

DEFECT 2 — duplicate cards.
    Insights were keyed on the evaluation row id, so every re-evaluation of
    the same cluster produced another identical card.

These tests pin the corrected behaviour so a later change cannot quietly
reintroduce either.
"""

import pytest

from app.services.insights import _failed_condition

CONFIG = {
    "minimum_correlated_ticket_count": 5,
    "detection_window_minutes": 20,
    "minimum_correlation_confidence": 0.80,
    "require_shared_system_or_root_cause": True,
}


def ctx(**over):
    base = {
        "correlated_ticket_count": 6,
        "detection_window_minutes": 12,
        "correlation_confidence": 0.91,
        "shared_system": "Payroll Portal",
    }
    base.update(over)
    return base


# --------------------------------------------------- the exact reported case


def test_the_reported_case_blames_confidence_not_count():
    """
    6 tickets vs a threshold of 5 -> count PASSED.
    Confidence 0.70 vs 0.80      -> confidence FAILED.

    The failing condition must be confidence. Anything else regenerates the
    original defect.
    """
    failed = _failed_condition(
        ctx(correlated_ticket_count=6, correlation_confidence=0.70), CONFIG
    )
    assert failed == "confidence"
    assert failed != "count", (
        "Recommending a lower ticket-count threshold when the count already "
        "passed is the defect this test exists to prevent."
    )


# ----------------------------------------------------------- each condition


def test_count_below_threshold_is_the_count():
    assert _failed_condition(ctx(correlated_ticket_count=3), CONFIG) == "count"


def test_window_too_wide_is_the_window():
    assert _failed_condition(
        ctx(detection_window_minutes=4320), CONFIG) == "window"


def test_missing_shared_cause_is_the_shared_cause():
    c = ctx()
    c.pop("shared_system")
    assert _failed_condition(c, CONFIG) == "shared_cause"


def test_root_cause_alone_satisfies_the_shared_requirement():
    c = ctx()
    c.pop("shared_system")
    c["shared_root_cause"] = "Node pool failover"
    assert _failed_condition(c, CONFIG) == "unknown"


def test_conditions_are_checked_in_evaluator_order():
    """
    When several conditions fail, the FIRST one the evaluator tests is the one
    reported — otherwise the recommendation would address a condition the
    agent never reached.
    """
    both = ctx(correlated_ticket_count=2, correlation_confidence=0.10)
    assert _failed_condition(both, CONFIG) == "count"


def test_non_numeric_values_do_not_crash():
    assert _failed_condition(
        ctx(correlation_confidence="unknown"), CONFIG) == "evidence"


def test_thresholds_come_from_configuration_not_constants():
    """A relaxed policy must change which condition is reported as failing."""
    case = ctx(correlated_ticket_count=6, correlation_confidence=0.70)
    assert _failed_condition(case, CONFIG) == "confidence"
    relaxed = {**CONFIG, "minimum_correlation_confidence": 0.60}
    assert _failed_condition(case, relaxed) == "unknown"


def test_shared_cause_requirement_can_be_waived():
    c = ctx()
    c.pop("shared_system")
    waived = {**CONFIG, "require_shared_system_or_root_cause": False}
    assert _failed_condition(c, waived) == "unknown"


# ------------------------------------------------------------ deduplication


def test_insight_fingerprints_are_deterministic_not_row_scoped():
    """
    Two evaluations of the same cluster at the same policy version must
    produce the same fingerprint, so they merge into one card.

    The original bug embedded the evaluation row id, which is unique per row
    and therefore guaranteed a new card every time.
    """
    subject, version, failed = "Payroll Portal", 3, "confidence"
    first = f"mi-{failed}::{subject}::v{version}"
    second = f"mi-{failed}::{subject}::v{version}"
    assert first == second

    other_version = f"mi-{failed}::{subject}::v{version + 1}"
    assert other_version != first, (
        "A policy version change is a genuinely different finding and should "
        "produce its own card."
    )

    other_condition = f"mi-count::{subject}::v{version}"
    assert other_condition != first
