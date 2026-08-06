# tests/test_policies_autonomy.py
"""
Deterministic tests for the two autonomy policies.

Same purpose as the major-incident tests: prove that behaviour is decided by
editable configuration, not by the model and not by which ticket it is.
"""

import pytest

from app.models.service_desk import Verdict
from app.services.policy_engine import (
    MissingEvidence,
    evaluate_change_and_cab_control as change_policy,
    evaluate_safe_auto_remediation as auto_policy,
)

AUTO_CONFIG = {
    "minimum_confidence": 0.85,
    "require_kb_auto_safe": True,
    "require_reversible": True,
    "block_if_reopened": True,
    "block_if_major_incident": True,
    "block_if_production_impact": True,
}

CHANGE_CONFIG = {
    "require_approval_if_production": True,
    "risk_levels_requiring_approval": ["Medium", "High"],
    "max_blast_radius": 25,
    "restricted_categories": ["access", "infrastructure", "critical_service"],
    "deny_if_previously_rolled_back": True,
}


def auto_ctx(**over):
    base = {
        "confidence": 0.93,
        "kb_auto_safe": True,
        "reversible": True,
        "is_reopened": False,
        "in_major_incident": False,
        "production_impact": False,
        "required_fields_complete": True,
    }
    base.update(over)
    return base


def change_ctx(**over):
    base = {
        "production_impact": False,
        "risk": "Low",
        "blast_radius": 4,
        "cab_approval_required": False,
        "action_category": "routine",
        "previous_rollback": False,
    }
    base.update(over)
    return base


# ------------------------------------------------- safe auto-remediation


def test_clean_known_error_is_auto_resolved():
    r = auto_policy(auto_ctx(), AUTO_CONFIG)
    assert r.verdict == Verdict.ALLOW
    assert any("0.93" in x for x in r.reasons)


def test_kb_not_auto_safe_is_denied():
    r = auto_policy(auto_ctx(kb_auto_safe=False), AUTO_CONFIG)
    assert r.verdict == Verdict.DENY
    assert any("auto-safe" in x for x in r.reasons)


def test_irreversible_action_is_denied_regardless_of_confidence():
    r = auto_policy(auto_ctx(confidence=0.99, reversible=False), AUTO_CONFIG)
    assert r.verdict == Verdict.DENY


def test_reopened_ticket_goes_to_a_human():
    r = auto_policy(auto_ctx(is_reopened=True), AUTO_CONFIG)
    assert r.verdict == Verdict.REQUIRE_HUMAN_REVIEW
    assert any("reopened" in x for x in r.reasons)


def test_major_incident_member_goes_to_a_human():
    r = auto_policy(auto_ctx(in_major_incident=True), AUTO_CONFIG)
    assert r.verdict == Verdict.REQUIRE_HUMAN_REVIEW


def test_incomplete_evidence_goes_to_a_human():
    r = auto_policy(auto_ctx(required_fields_complete=False), AUTO_CONFIG)
    assert r.verdict == Verdict.REQUIRE_HUMAN_REVIEW


def test_confidence_below_threshold_goes_to_a_human():
    r = auto_policy(auto_ctx(confidence=0.71), AUTO_CONFIG)
    assert r.verdict == Verdict.REQUIRE_HUMAN_REVIEW


def test_lowering_the_confidence_dial_automates_more():
    """THE AUTONOMY DIAL: same case, one number, different behaviour."""
    case = auto_ctx(confidence=0.78)
    assert auto_policy(case, AUTO_CONFIG).verdict == Verdict.REQUIRE_HUMAN_REVIEW
    relaxed = {**AUTO_CONFIG, "minimum_confidence": 0.70}
    assert auto_policy(case, relaxed).verdict == Verdict.ALLOW


def test_missing_confidence_is_not_invented():
    with pytest.raises(MissingEvidence):
        auto_policy(auto_ctx(confidence=None), AUTO_CONFIG)


# ------------------------------------------------ change and CAB control


def test_low_risk_routine_change_proceeds():
    assert change_policy(change_ctx(), CHANGE_CONFIG).verdict == Verdict.ALLOW


def test_cab_flag_forces_approval():
    r = change_policy(change_ctx(cab_approval_required=True,
                                change_status="Pending CAB Approval"),
                      CHANGE_CONFIG)
    assert r.verdict == Verdict.REQUIRE_HUMAN_REVIEW
    assert any("cab_approval_required" in x for x in r.reasons)


def test_production_impact_forces_approval():
    r = change_policy(change_ctx(production_impact=True), CHANGE_CONFIG)
    assert r.verdict == Verdict.REQUIRE_HUMAN_REVIEW


def test_high_risk_forces_approval():
    r = change_policy(change_ctx(risk="High"), CHANGE_CONFIG)
    assert r.verdict == Verdict.REQUIRE_HUMAN_REVIEW
    assert any("High" in x for x in r.reasons)


def test_wide_blast_radius_forces_approval():
    r = change_policy(change_ctx(blast_radius=140), CHANGE_CONFIG)
    assert r.verdict == Verdict.REQUIRE_HUMAN_REVIEW
    assert any("140" in x for x in r.reasons)


def test_restricted_category_forces_approval():
    r = change_policy(change_ctx(action_category="access"), CHANGE_CONFIG)
    assert r.verdict == Verdict.REQUIRE_HUMAN_REVIEW


def test_previously_rolled_back_change_is_denied():
    r = change_policy(change_ctx(previous_rollback=True), CHANGE_CONFIG)
    assert r.verdict == Verdict.DENY
    assert any("rolled back" in x for x in r.reasons)


def test_rejected_change_is_denied():
    r = change_policy(change_ctx(change_status="Rejected"), CHANGE_CONFIG)
    assert r.verdict == Verdict.DENY


def test_raising_the_blast_radius_limit_permits_a_wider_change():
    case = change_ctx(blast_radius=40)
    assert change_policy(case, CHANGE_CONFIG).verdict == Verdict.REQUIRE_HUMAN_REVIEW
    relaxed = {**CHANGE_CONFIG, "max_blast_radius": 100}
    assert change_policy(case, relaxed).verdict == Verdict.ALLOW


def test_neither_evaluator_sees_an_issue_key():
    import inspect
    for fn in (auto_policy, change_policy):
        assert list(inspect.signature(fn).parameters) == ["context", "config"]
