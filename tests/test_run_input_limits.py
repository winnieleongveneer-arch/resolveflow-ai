"""
An over-long issue key must be refused, not crash the request.

The column is String(64). Before this, any longer value passed schema
validation, reached Postgres, and raised a DataError that surfaced as HTTP 500
- indistinguishable, to anyone watching, from the application falling over.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_overlong_issue_key_is_rejected_not_crashed():
    r = client.post("/api/agent/runs",
                    json={"issue_key": "A" * 5000, "trigger_source": "test"})
    assert r.status_code == 422, f"expected 422, got {r.status_code}"
    assert r.status_code < 500


def test_empty_issue_key_is_rejected():
    r = client.post("/api/agent/runs", json={"issue_key": ""})
    assert r.status_code == 422


def test_overlong_trigger_source_is_rejected():
    r = client.post("/api/agent/runs",
                    json={"issue_key": "ITSM-2231", "trigger_source": "T" * 500})
    assert r.status_code == 422


def test_a_normal_key_still_works():
    r = client.post("/api/agent/runs",
                    json={"issue_key": "ITSM-2231", "trigger_source": "test"})
    assert r.status_code in (200, 201), r.text
