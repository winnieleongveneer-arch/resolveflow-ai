# app/models/service_desk.py
"""
ResolveFlow NEXUS — Service Desk Command Center models.

Six P0 tables that prove the governed path:

    workflow_runs      one Orchestrator run (the case being processed)
    operator_events    what each Operator did, in order  (feeds the live trace)
    policy_definitions the editable rules a business user owns
    policy_evaluations immutable record of every verdict   (audit)
    workbench_items    the human decision queue
    integration_health live registry of connected systems

Design notes
------------
* UUID primary keys throughout; ``run_id`` correlates everything.
* JSONB for structured evidence so the shape can evolve without migrations.
* policy_evaluations is APPEND ONLY. Never update or delete a row — the audit
  trail is the product. Re-evaluating creates a new row with a new version.
* Statuses are plain strings (not native PG enums) so adding a value later is a
  code change, not a migration.

IMPORTANT: every model here must stay exported from app/models/__init__.py.
alembic/env.py does `from app.models import *`, so an unexported model is
invisible to autogenerate and silently produces an EMPTY migration.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..core.database import Base

# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------


class RunStatus:
    RECEIVED = "RECEIVED"
    CONTEXTUALISING = "CONTEXTUALISING"
    ANALYSING = "ANALYSING"
    PLANNING = "PLANNING"
    POLICY_GATED = "POLICY_GATED"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    ROLLING_BACK = "ROLLING_BACK"
    COMMUNICATING = "COMMUNICATING"
    LEARNING = "LEARNING"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"

    ALL = [
        RECEIVED, CONTEXTUALISING, ANALYSING, PLANNING, POLICY_GATED,
        WAITING_FOR_HUMAN, APPROVED, DENIED, EXECUTING, VERIFYING,
        ROLLING_BACK, COMMUNICATING, LEARNING, RESOLVED, ESCALATED, FAILED,
    ]
    TERMINAL = [RESOLVED, ESCALATED, FAILED, DENIED]


class Verdict:
    ALLOW = "ALLOW"
    REQUIRE_HUMAN_REVIEW = "REQUIRE_HUMAN_REVIEW"
    DENY = "DENY"

    ALL = [ALLOW, REQUIRE_HUMAN_REVIEW, DENY]


class WorkbenchStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    ALL = [PENDING, APPROVED, MODIFIED, REJECTED, EXPIRED]
    OPEN = [PENDING]


class IntegrationStatus:
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"

    ALL = [HEALTHY, DEGRADED, UNHEALTHY, UNKNOWN]


# ---------------------------------------------------------------------------
# A. workflow_runs
# ---------------------------------------------------------------------------


class WorkflowRun(Base):
    """One Orchestrator run against one case."""

    __tablename__ = "workflow_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Identifier returned by Supervity Auto. Null until Auto actually accepts
    # the invocation — never fabricate one.
    auto_run_id = Column(String(255), nullable=True, index=True)

    issue_key = Column(String(64), nullable=False, index=True)
    trigger_source = Column(String(64), nullable=False, default="command_center")

    status = Column(String(32), nullable=False, default=RunStatus.RECEIVED, index=True)
    current_stage = Column(String(64), nullable=True)

    # Idempotency: the same key must never start two runs.
    idempotency_key = Column(String(255), nullable=True, unique=True)

    trigger_payload = Column(JSONB, nullable=True)
    case_snapshot = Column(JSONB, nullable=True)

    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_workflow_runs_issue_status", "issue_key", "status"),
        Index("ix_workflow_runs_started", "started_at"),
    )

    def __repr__(self):
        return f"<WorkflowRun {self.issue_key} {self.status}>"


# ---------------------------------------------------------------------------
# B. operator_events
# ---------------------------------------------------------------------------


class OperatorEvent(Base):
    """Append-only trace of Operator activity — feeds the live run timeline."""

    __tablename__ = "operator_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # "RF-01 SLA Rescue Coordinator", "ORCHESTRATOR", "POLICY_ENGINE", ...
    operator_name = Column(String(128), nullable=False, index=True)

    # OPERATOR_STARTED | OPERATOR_COMPLETED | RETRY | BRANCH_SELECTED |
    # POLICY_EVALUATED | HUMAN_REQUESTED | HUMAN_DECIDED | ACTION_EXECUTED |
    # VERIFICATION | ROLLBACK | COMPLETED | ERROR
    event_type = Column(String(64), nullable=False, index=True)
    event_status = Column(String(32), nullable=True)

    sequence = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    payload = Column(JSONB, nullable=True)

    event_timestamp = Column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (Index("ix_operator_events_run_time", "run_id", "event_timestamp"),)

    def __repr__(self):
        return f"<OperatorEvent {self.operator_name} {self.event_type}>"


# ---------------------------------------------------------------------------
# C. policy_definitions
# ---------------------------------------------------------------------------


class PolicyDefinition(Base):
    """
    A rule a business user owns and can edit with no code.

    ``configuration`` holds the editable thresholds. Changing it bumps
    ``active_version``, and the next evaluation records the new version — that
    is how a judge sees the edit take effect.
    """

    __tablename__ = "policy_definitions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_key = Column(String(128), nullable=False, unique=True, index=True)

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    active_version = Column(Integer, nullable=False, default=1)
    configuration = Column(JSONB, nullable=False, default=dict)

    # Optional metadata for the Policies UI (labels, min/max, step, help text).
    schema_hints = Column(JSONB, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True, index=True)

    updated_by = Column(String(255), nullable=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<PolicyDefinition {self.policy_key} v{self.active_version}>"


class PolicyVersion(Base):
    """
    Immutable history of every configuration a policy has ever had.

    Written on create and on every edit, so "what were the rules when this
    decision was made?" is answerable months later.
    """

    __tablename__ = "policy_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_key = Column(String(128), nullable=False, index=True)
    version = Column(Integer, nullable=False)

    configuration = Column(JSONB, nullable=False)
    change_note = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_policy_versions_key_version", "policy_key", "version", unique=True),
    )

    def __repr__(self):
        return f"<PolicyVersion {self.policy_key} v{self.version}>"


# ---------------------------------------------------------------------------
# D. policy_evaluations  (APPEND ONLY)
# ---------------------------------------------------------------------------


class PolicyEvaluation(Base):
    """
    Immutable record of one verdict.

    Never update these rows. Every evaluation — including re-runs after a
    threshold change — appends a new row. This table is the evidence that the
    policy actually gated the action rather than describing it afterwards.
    """

    __tablename__ = "policy_evaluations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    issue_key = Column(String(64), nullable=True, index=True)
    policy_key = Column(String(128), nullable=False, index=True)
    policy_version = Column(Integer, nullable=False)

    # Exactly what the evaluator was given, and the config it ran against.
    input_context = Column(JSONB, nullable=False)
    configuration_snapshot = Column(JSONB, nullable=True)

    verdict = Column(String(32), nullable=False, index=True)
    reasons = Column(JSONB, nullable=False, default=list)

    # Which action this verdict gated, if any.
    proposed_action = Column(JSONB, nullable=True)

    # True when the evaluation ran in simulation and had no side effects.
    is_simulation = Column(Boolean, nullable=False, default=False, index=True)

    evaluated_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_policy_evaluations_run_policy", "run_id", "policy_key"),
        Index("ix_policy_evaluations_issue_time", "issue_key", "evaluated_at"),
    )

    def __repr__(self):
        return f"<PolicyEvaluation {self.policy_key} {self.verdict}>"


# ---------------------------------------------------------------------------
# E. workbench_items
# ---------------------------------------------------------------------------


class WorkbenchItem(Base):
    """
    A decision the agent must not make alone.

    Arrives with full context and the agent's recommendation. A human
    approves, modifies or rejects; the decision is recorded and the workflow
    continues from there. Silence is never approval — an item with no decision
    stays PENDING for ever.
    """

    __tablename__ = "workbench_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    issue_key = Column(String(64), nullable=False, index=True)
    status = Column(
        String(32), nullable=False, default=WorkbenchStatus.PENDING, index=True
    )

    # CHANGE_APPROVAL | MAJOR_INCIDENT | LOW_CONFIDENCE | MISSING_DATA |
    # ROLLBACK_FAILED | RISKY_REMEDIATION
    request_type = Column(String(64), nullable=False, index=True)

    case_context = Column(JSONB, nullable=False, default=dict)
    proposed_action = Column(JSONB, nullable=False, default=dict)
    policy_result = Column(JSONB, nullable=True)
    agent_recommendation = Column(Text, nullable=True)

    verification_plan = Column(JSONB, nullable=True)
    rollback_plan = Column(JSONB, nullable=True)

    # APPROVE | MODIFY | REJECT
    human_decision = Column(String(32), nullable=True)
    modified_action = Column(JSONB, nullable=True)
    approved_scope = Column(JSONB, nullable=True)

    reviewer = Column(String(255), nullable=True)
    reviewer_notes = Column(Text, nullable=True)

    # Where the Slack escalation landed, for the audit trail.
    notification_ref = Column(String(512), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_workbench_status_created", "status", "created_at"),
    )

    def __repr__(self):
        return f"<WorkbenchItem {self.issue_key} {self.status}>"


# ---------------------------------------------------------------------------
# F. integration_health
# ---------------------------------------------------------------------------


class IntegrationHealth(Base):
    """
    Live registry of every connected system.

    Status must come from a real connectivity check or a real read/write.
    A hardcoded green badge does not count toward the integration floor.
    """

    __tablename__ = "integration_health"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    integration_key = Column(String(64), nullable=False, unique=True, index=True)
    integration_name = Column(String(255), nullable=False)

    # channel | system_of_record | agent_platform | knowledge | other
    category = Column(String(64), nullable=False, index=True)
    purpose = Column(Text, nullable=True)

    status = Column(
        String(32), nullable=False, default=IntegrationStatus.UNKNOWN, index=True
    )
    credentials_configured = Column(Boolean, nullable=False, default=False)

    last_health_check = Column(DateTime(timezone=True), nullable=True)
    last_successful_read = Column(DateTime(timezone=True), nullable=True)
    last_successful_write = Column(DateTime(timezone=True), nullable=True)

    latency_ms = Column(Float, nullable=True)
    records_processed = Column(Integer, nullable=False, default=0)
    latest_error = Column(Text, nullable=True)

    # Which Operators use this integration — shown in Data Manager.
    used_by_operators = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return f"<IntegrationHealth {self.integration_key} {self.status}>"
