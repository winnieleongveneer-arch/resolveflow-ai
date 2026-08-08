"""
An over-long issue key must be refused, not crash the request.

The column is String(64). Before this, a longer value passed schema validation,
reached Postgres, and raised a DataError that surfaced as HTTP 500 -
indistinguishable, to anyone watching, from the application falling over.

These assert against the schema rather than through an HTTP client: the schema
is where the constraint lives, and FastAPI turns a ValidationError into 422 on
its own. It also keeps the suite free of a test-only HTTP dependency.
"""
import pytest
from pydantic import ValidationError

from app.schemas.service_desk import RunCreate


def test_overlong_issue_key_is_rejected():
    with pytest.raises(ValidationError) as exc:
        RunCreate(issue_key="A" * 5000)
    assert "issue_key" in str(exc.value)


def test_issue_key_at_the_column_limit_is_accepted():
    # 64 is the column width; the boundary itself must not be rejected.
    assert RunCreate(issue_key="A" * 64).issue_key == "A" * 64


def test_issue_key_one_over_the_limit_is_rejected():
    with pytest.raises(ValidationError):
        RunCreate(issue_key="A" * 65)


def test_empty_issue_key_is_rejected():
    with pytest.raises(ValidationError):
        RunCreate(issue_key="")


def test_overlong_trigger_source_is_rejected():
    with pytest.raises(ValidationError):
        RunCreate(issue_key="ITSM-2231", trigger_source="T" * 500)


def test_overlong_idempotency_key_is_rejected():
    with pytest.raises(ValidationError):
        RunCreate(issue_key="ITSM-2231", idempotency_key="K" * 500)


def test_a_normal_key_still_works():
    run = RunCreate(issue_key="ITSM-2231", trigger_source="manual")
    assert run.issue_key == "ITSM-2231"
    assert run.trigger_source == "manual"
