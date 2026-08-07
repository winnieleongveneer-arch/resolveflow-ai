# tests/test_integration_reporting.py
"""
An integration's health must reflect an observed round trip, never its
configuration.

These tests pin the one rule the Data Manager rests on: a status only reaches
HEALTHY after something actually happened, and a reported failure is recorded
rather than swallowed. Outlook is the interesting case — its token lives inside
Supervity Auto, so the Command Center cannot probe it and must not pretend to.
"""

import pytest

from app.models.service_desk import IntegrationStatus
from app.services import integrations as integ


def test_outlook_is_registered_as_a_channel():
    keys = {spec["integration_key"]: spec for spec in integ.REGISTRY}
    assert "outlook" in keys, "Outlook must appear in the Data Manager registry."
    assert keys["outlook"]["category"] == "channel"


def test_registry_clears_the_integration_floor():
    """>=3 integrations across >=2 categories, with a channel and a system of record."""
    categories = {spec["category"] for spec in integ.REGISTRY}
    assert len(integ.REGISTRY) >= 3
    assert len(categories) >= 2
    assert "channel" in categories
    assert "system_of_record" in categories


def test_outlook_check_refuses_to_claim_health_it_cannot_observe():
    """
    The Command Center holds no Outlook credential. The honest answer is
    UNKNOWN with a stated reason — not HEALTHY, and not UNHEALTHY either,
    since nothing has been shown to be broken.
    """
    result = integ._check_outlook()
    assert result["status"] == IntegrationStatus.UNKNOWN
    assert result["credentials_configured"] is False
    assert "Supervity Auto" in result["error"]


def test_every_registered_integration_has_a_stated_purpose():
    for spec in integ.REGISTRY:
        assert spec.get("purpose"), f"{spec['integration_key']} has no stated purpose."
        assert spec.get("used_by_operators"), (
            f"{spec['integration_key']} is not claimed by any Operator — an "
            "integration nothing uses does not count toward the floor."
        )


def test_no_check_returns_healthy_without_a_round_trip():
    """
    Guard against the failure mode this whole module exists to prevent: a
    check that reports HEALTHY on the strength of a credential being present.
    """
    for key, check in integ.CHECKS.items():
        result = check()
        if result["status"] == IntegrationStatus.HEALTHY:
            # Only permitted when the check genuinely made a call and measured it.
            assert result.get("latency_ms") is not None, (
                f"{key} reported HEALTHY without a measured round trip."
            )
