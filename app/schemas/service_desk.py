# app/schemas/service_desk.py
"""Pydantic schemas for the ResolveFlow AI service desk API."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------- runs


class RunCreate(BaseModel):
    # These lengths mirror the columns exactly: issue_key and trigger_source are
    # String(64), idempotency_key is String(255). Without them an over-long value
    # passes validation, reaches Postgres, and raises a DataError nothing catches
    # - so the caller gets a 500 for what is plainly a bad request. Rejecting it
    # here returns 422 and says which field was wrong.
    issue_key: str = Field(..., min_length=1, max_length=64, examples=["ITSM-2180"])
    trigger_source: str = Field("command_center", max_length=64,
                                examples=["command_center"])
    trigger_payload: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = Field(None, max_length=255)


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    auto_run_id: Optional[str] = None
    issue_key: str
    trigger_source: str
    status: str
    current_stage: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class OperatorEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    operator_name: str
    event_type: str
    event_status: Optional[str] = None
    sequence: Optional[int] = None
    duration_ms: Optional[int] = None
    payload: Optional[Dict[str, Any]] = None
    event_timestamp: Optional[datetime] = None


class OperatorEventCreate(BaseModel):
    operator_name: str
    event_type: str
    event_status: Optional[str] = None
    sequence: Optional[int] = None
    duration_ms: Optional[int] = None
    payload: Optional[Dict[str, Any]] = None


# ----------------------------------------------------------------- policies


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    policy_key: str
    name: str
    description: Optional[str] = None
    active_version: int
    configuration: Dict[str, Any]
    schema_hints: Optional[Dict[str, Any]] = None
    is_active: bool
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None


class PolicyUpdate(BaseModel):
    """Edit a policy's thresholds. Bumps active_version and records history."""

    configuration: Dict[str, Any]
    change_note: Optional[str] = None
    updated_by: Optional[str] = None
    is_active: Optional[bool] = None


class PolicyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    policy_key: str
    version: int
    configuration: Dict[str, Any]
    change_note: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None


class EvaluateRequest(BaseModel):
    policy_key: str = Field(..., examples=["major_incident_declaration"])
    context: Dict[str, Any] = Field(
        ...,
        examples=[{
            "correlated_ticket_count": 6,
            "detection_window_minutes": 18,
            "correlation_confidence": 0.91,
            "shared_system": "Payroll Portal",
        }],
    )
    run_id: Optional[UUID] = None
    issue_key: Optional[str] = None
    proposed_action: Optional[Dict[str, Any]] = None
    simulate: bool = False


class EvaluateResponse(BaseModel):
    verdict: str
    policy_key: str
    policy_version: int
    reasons: List[str]
    missing_fields: List[str] = []
    is_simulation: bool = False


class PolicyEvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: Optional[UUID] = None
    issue_key: Optional[str] = None
    policy_key: str
    policy_version: int
    input_context: Dict[str, Any]
    configuration_snapshot: Optional[Dict[str, Any]] = None
    verdict: str
    reasons: List[str]
    proposed_action: Optional[Dict[str, Any]] = None
    is_simulation: bool
    evaluated_at: Optional[datetime] = None


# ---------------------------------------------------------------- workbench


class WorkbenchItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    issue_key: str
    status: str
    request_type: str
    case_context: Dict[str, Any]
    proposed_action: Dict[str, Any]
    policy_result: Optional[Dict[str, Any]] = None
    agent_recommendation: Optional[str] = None
    verification_plan: Optional[Dict[str, Any]] = None
    rollback_plan: Optional[Dict[str, Any]] = None
    human_decision: Optional[str] = None
    modified_action: Optional[Dict[str, Any]] = None
    approved_scope: Optional[Dict[str, Any]] = None
    reviewer: Optional[str] = None
    reviewer_notes: Optional[str] = None
    notification_ref: Optional[str] = None
    auto_activity_run_id: Optional[str] = None
    auto_form_id: Optional[str] = None
    auto_resume_result: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None


class WorkbenchItemCreate(BaseModel):
    run_id: UUID
    issue_key: str
    request_type: str = Field(..., examples=["MAJOR_INCIDENT"])
    case_context: Dict[str, Any] = {}
    proposed_action: Dict[str, Any] = {}
    policy_result: Optional[Dict[str, Any]] = None
    agent_recommendation: Optional[str] = None
    verification_plan: Optional[Dict[str, Any]] = None
    rollback_plan: Optional[Dict[str, Any]] = None
    notify: bool = True


class DecisionRequest(BaseModel):
    """A human's Approve / Modify / Reject."""

    decision: str = Field(..., examples=["APPROVE", "MODIFY", "REJECT"])
    reviewer: str = Field(..., examples=["winnie"])
    reviewer_notes: Optional[str] = None
    # Required when decision == MODIFY.
    modified_action: Optional[Dict[str, Any]] = None


class DecisionResponse(BaseModel):
    item_id: UUID
    run_id: UUID
    issue_key: str
    status: str
    human_decision: str
    approved_scope: Optional[Dict[str, Any]] = None
    run_status: str
    message: str


# --------------------------------------------------------------- policy gate


class GateRequest(BaseModel):
    """
    The call an Operator makes before performing an external action.

    Evaluates the policy, records the verdict, and — when the verdict is
    REQUIRE_HUMAN_REVIEW — creates a Workbench item and sends the Slack
    escalation in one step.
    """

    run_id: UUID
    policy_key: str = Field(..., examples=["major_incident_declaration"])
    issue_key: str
    context: Dict[str, Any]
    proposed_action: Dict[str, Any] = {}
    case_context: Dict[str, Any] = {}
    agent_recommendation: Optional[str] = None
    verification_plan: Optional[Dict[str, Any]] = None
    rollback_plan: Optional[Dict[str, Any]] = None
    request_type: str = "RISKY_REMEDIATION"
    notify: bool = True


class GateResponse(BaseModel):
    verdict: str
    policy_key: str
    policy_version: int
    reasons: List[str]
    missing_fields: List[str] = []
    may_execute: bool
    workbench_item_id: Optional[UUID] = None
    notification: Optional[Dict[str, Any]] = None


# -------------------------------------------------------------- integrations


class IntegrationUseReport(BaseModel):
    """An Operator's report of a real round trip against an integration."""

    operator_name: str
    direction: str = "write"          # "read" or "write"
    succeeded: bool = True
    records: int = 1
    detail: Optional[str] = None


class IntegrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    integration_key: str
    integration_name: str
    category: str
    purpose: Optional[str] = None
    status: str
    credentials_configured: bool
    last_health_check: Optional[datetime] = None
    last_successful_read: Optional[datetime] = None
    last_successful_write: Optional[datetime] = None
    latency_ms: Optional[float] = None
    records_processed: int
    latest_error: Optional[str] = None
    used_by_operators: Optional[Any] = None
