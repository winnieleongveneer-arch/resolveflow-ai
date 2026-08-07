# tests/test_passport_external_changes.py
"""
The Decision Passport must report a change only when one actually happened.

This pins the distinction that matters most in the audit record: an Operator
ATTEMPTING a write and an Operator COMPLETING one are different facts, and the
Passport is the document a judge reads to tell them apart.
"""

from types import SimpleNamespace

from app.services.passport import (
    EXTERNAL_CHANGE_EVENTS,
    _is_external_change,
    _is_verification,
)


def ev(event_type, event_status="ok", payload=None):
    return SimpleNamespace(event_type=event_type, event_status=event_status,
                           payload=payload or {})


def test_applied_remediation_is_an_external_change():
    assert _is_external_change(ev("REMEDIATION_APPLIED"))


def test_a_rejected_write_is_not_an_external_change():
    """
    RF-03 reports REMEDIATION_APPLIED even when the system of record refused
    the write, flagging the event 'error'. That must not appear in the Passport
    as a completed change.
    """
    assert not _is_external_change(ev("REMEDIATION_APPLIED", "error"))


def test_a_held_case_changed_nothing():
    assert not _is_external_change(ev("AWAITING_HUMAN", "waiting"))


def test_a_refusal_changed_nothing():
    assert not _is_external_change(ev("REMEDIATION_REFUSED", "denied"))


def test_policy_evaluation_is_not_an_external_change():
    """Evaluating a policy is a decision, not an action on another system."""
    assert not _is_external_change(ev("POLICY_EVALUATED"))


def test_verification_requires_the_operator_to_confirm_it():
    assert _is_verification(ev("REMEDIATION_APPLIED", payload={"verified": True}))
    assert not _is_verification(ev("REMEDIATION_APPLIED", payload={"verified": False}))
    assert not _is_verification(ev("REMEDIATION_APPLIED", payload={}))


def test_a_dedicated_verification_event_counts():
    assert _is_verification(ev("VERIFICATION"))


def test_every_declared_change_event_is_reachable():
    """Guard against a typo silently removing an Operator from the audit trail."""
    for name in EXTERNAL_CHANGE_EVENTS:
        assert _is_external_change(ev(name)), f"{name} is declared but not recognised"
