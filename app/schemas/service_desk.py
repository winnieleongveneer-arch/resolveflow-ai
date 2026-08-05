# app/schemas/service_desk.py
"""Pydantic schemas for the ResolveFlow NEXUS service desk API."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------- runs


class RunCreate(BaseModel):
    issue_key: str = Field(..., examples=["ITSM-2180"])
    trigger_source: str = Field("command_center", examples=["command_center"])
    trigger_payload: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None


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
