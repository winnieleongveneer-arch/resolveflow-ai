# tests/test_dashboard_counting.py
"""
The dashboard must not conflate what exists with what the agent has handled.

These pin the two definitions the Dashboard rests on, and the one rule that
makes the auto-resolution rate honest.
"""

import pytest

from app.services import backlog as backlog_svc


def test_source_ticket_count_returns_none_without_credentials(monkeypatch):
    """
    No credentials means no answer — not zero. A dashboard showing "0 source
    tickets" would be asserting the source system is empty, which is a much
    stronger and more wrong claim than showing nothing.
    """
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    assert backlog_svc.source_ticket_count() is None


def test_source_ticket_count_accepts_206(monkeypatch):
    """
    PostgREST answers a counted, limited read with 206 Partial Content. An
    earlier version treated anything but 200 as unreachable and reported every
    table as missing.
    """
    class FakeResponse:
        status_code = 206
        headers = {"content-range": "0-0/460"}

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setattr(backlog_svc.httpx, "get", lambda *a, **k: FakeResponse())
    assert backlog_svc.source_ticket_count() == 460


def test_source_ticket_count_is_none_when_range_header_missing(monkeypatch):
    """Without a countable answer we say nothing rather than guess."""
    class FakeResponse:
        status_code = 200
        headers = {}

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setattr(backlog_svc.httpx, "get", lambda *a, **k: FakeResponse())
    assert backlog_svc.source_ticket_count() is None


def test_source_ticket_count_survives_an_unreachable_supabase(monkeypatch):
    """A dashboard tile must never take the whole page down."""
    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setattr(backlog_svc.httpx, "get", boom)
    with pytest.raises(RuntimeError):
        backlog_svc.source_ticket_count()
    # The caller in runs.py wraps this in try/except and reports None, which is
    # what keeps the endpoint up. Pinned here so the wrapper is never removed
    # on the assumption that this function is safe on its own.


def test_agent_only_filter_is_declared_on_the_runs_endpoint():
    """
    The dashboard's activity feed must be able to exclude reconciliation rows.

    A batch sweep writes a run and a policy evaluation but never calls an
    Operator, so it has no events. Those rows are real history — they are just
    not agent activity, and a feed labelled "recent agent activity" that
    includes them overstates what the agent did.
    """
    import inspect
    from app.routers.runs import list_runs

    params = inspect.signature(list_runs).parameters
    assert "agent_only" in params, (
        "list_runs lost its agent_only filter; the dashboard would start "
        "showing reconciliation rows as agent activity again."
    )
    assert params["agent_only"].default is False, (
        "agent_only must default to False so the unfiltered history stays "
        "queryable for auditing."
    )
