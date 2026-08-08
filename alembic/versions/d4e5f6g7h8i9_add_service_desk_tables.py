"""Add ResolveFlow AI service desk tables

Creates the six P0 tables plus policy_versions, and seeds the
Major Incident Declaration policy so a clean clone comes up with a
working, editable policy already present.

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-08-05
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6g7h8i9"
down_revision: Union[str, None] = "c3d4e5f6g7h8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------------- runs
    op.create_table(
        "workflow_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auto_run_id", sa.String(255), nullable=True),
        sa.Column("issue_key", sa.String(64), nullable=False),
        sa.Column("trigger_source", sa.String(64), nullable=False,
                  server_default="command_center"),
        sa.Column("status", sa.String(32), nullable=False, server_default="RECEIVED"),
        sa.Column("current_stage", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("trigger_payload", postgresql.JSONB(), nullable=True),
        sa.Column("case_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_workflow_runs_idempotency"),
    )
    op.create_index("ix_workflow_runs_auto_run_id", "workflow_runs", ["auto_run_id"])
    op.create_index("ix_workflow_runs_issue_key", "workflow_runs", ["issue_key"])
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])
    op.create_index("ix_workflow_runs_issue_status", "workflow_runs",
                    ["issue_key", "status"])
    op.create_index("ix_workflow_runs_started", "workflow_runs", ["started_at"])

    # -------------------------------------------------------------- events
    op.create_table(
        "operator_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operator_name", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_status", sa.String(32), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("event_timestamp", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_operator_events_run_id", "operator_events", ["run_id"])
    op.create_index("ix_operator_events_operator_name", "operator_events",
                    ["operator_name"])
    op.create_index("ix_operator_events_event_type", "operator_events", ["event_type"])
    op.create_index("ix_operator_events_event_timestamp", "operator_events",
                    ["event_timestamp"])
    op.create_index("ix_operator_events_run_time", "operator_events",
                    ["run_id", "event_timestamp"])

    # ------------------------------------------------------------ policies
    op.create_table(
        "policy_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("schema_hints", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_key", name="uq_policy_definitions_key"),
    )
    op.create_index("ix_policy_definitions_policy_key", "policy_definitions",
                    ["policy_key"])
    op.create_index("ix_policy_definitions_is_active", "policy_definitions",
                    ["is_active"])

    op.create_table(
        "policy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_key", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_policy_versions_policy_key", "policy_versions", ["policy_key"])
    op.create_index("ix_policy_versions_key_version", "policy_versions",
                    ["policy_key", "version"], unique=True)

    op.create_table(
        "policy_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("issue_key", sa.String(64), nullable=True),
        sa.Column("policy_key", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("input_context", postgresql.JSONB(), nullable=False),
        sa.Column("configuration_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("proposed_action", postgresql.JSONB(), nullable=True),
        sa.Column("is_simulation", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("evaluated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_policy_evaluations_run_id", "policy_evaluations", ["run_id"])
    op.create_index("ix_policy_evaluations_issue_key", "policy_evaluations",
                    ["issue_key"])
    op.create_index("ix_policy_evaluations_policy_key", "policy_evaluations",
                    ["policy_key"])
    op.create_index("ix_policy_evaluations_verdict", "policy_evaluations", ["verdict"])
    op.create_index("ix_policy_evaluations_is_simulation", "policy_evaluations",
                    ["is_simulation"])
    op.create_index("ix_policy_evaluations_evaluated_at", "policy_evaluations",
                    ["evaluated_at"])
    op.create_index("ix_policy_evaluations_run_policy", "policy_evaluations",
                    ["run_id", "policy_key"])
    op.create_index("ix_policy_evaluations_issue_time", "policy_evaluations",
                    ["issue_key", "evaluated_at"])

    # ----------------------------------------------------------- workbench
    op.create_table(
        "workbench_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issue_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("request_type", sa.String(64), nullable=False),
        sa.Column("case_context", postgresql.JSONB(), nullable=False),
        sa.Column("proposed_action", postgresql.JSONB(), nullable=False),
        sa.Column("policy_result", postgresql.JSONB(), nullable=True),
        sa.Column("agent_recommendation", sa.Text(), nullable=True),
        sa.Column("verification_plan", postgresql.JSONB(), nullable=True),
        sa.Column("rollback_plan", postgresql.JSONB(), nullable=True),
        sa.Column("human_decision", sa.String(32), nullable=True),
        sa.Column("modified_action", postgresql.JSONB(), nullable=True),
        sa.Column("approved_scope", postgresql.JSONB(), nullable=True),
        sa.Column("reviewer", sa.String(255), nullable=True),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("notification_ref", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_workbench_items_run_id", "workbench_items", ["run_id"])
    op.create_index("ix_workbench_items_issue_key", "workbench_items", ["issue_key"])
    op.create_index("ix_workbench_items_status", "workbench_items", ["status"])
    op.create_index("ix_workbench_items_request_type", "workbench_items",
                    ["request_type"])
    op.create_index("ix_workbench_items_created_at", "workbench_items", ["created_at"])
    op.create_index("ix_workbench_status_created", "workbench_items",
                    ["status", "created_at"])

    # -------------------------------------------------------- integrations
    op.create_table(
        "integration_health",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_key", sa.String(64), nullable=False),
        sa.Column("integration_name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="UNKNOWN"),
        sa.Column("credentials_configured", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("last_health_check", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_read", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_write", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("records_processed", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("latest_error", sa.Text(), nullable=True),
        sa.Column("used_by_operators", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("integration_key", name="uq_integration_health_key"),
    )
    op.create_index("ix_integration_health_key", "integration_health",
                    ["integration_key"])
    op.create_index("ix_integration_health_category", "integration_health",
                    ["category"])
    op.create_index("ix_integration_health_status", "integration_health", ["status"])

    # ------------------------------------------------------------- seeding
    # Seed the Major Incident Declaration policy so a clean clone starts with
    # a real, editable policy. No fake runs, evaluations or metrics are seeded.
    mi_config = {
        "minimum_correlated_ticket_count": 5,
        "detection_window_minutes": 20,
        "minimum_correlation_confidence": 0.80,
        "require_shared_system_or_root_cause": True,
    }
    mi_hints = {
        "minimum_correlated_ticket_count": {
            "label": "Minimum correlated tickets",
            "help": "How many related tickets must appear before a major incident may be declared.",
            "type": "integer", "min": 2, "max": 50, "step": 1,
        },
        "detection_window_minutes": {
            "label": "Detection window (minutes)",
            "help": "Tickets must correlate within this rolling window.",
            "type": "integer", "min": 5, "max": 240, "step": 5,
        },
        "minimum_correlation_confidence": {
            "label": "Minimum correlation confidence",
            "help": "Semantic correlation confidence required to treat tickets as one incident.",
            "type": "float", "min": 0.0, "max": 1.0, "step": 0.01,
        },
        "require_shared_system_or_root_cause": {
            "label": "Require shared system or root cause",
            "help": "Refuse to declare on keyword similarity alone.",
            "type": "boolean",
        },
    }

    policies = sa.table(
        "policy_definitions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("policy_key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("active_version", sa.Integer),
        sa.column("configuration", postgresql.JSONB),
        sa.column("schema_hints", postgresql.JSONB),
        sa.column("is_active", sa.Boolean),
        sa.column("updated_by", sa.String),
    )
    versions = sa.table(
        "policy_versions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("policy_key", sa.String),
        sa.column("version", sa.Integer),
        sa.column("configuration", postgresql.JSONB),
        sa.column("change_note", sa.Text),
        sa.column("created_by", sa.String),
    )

    op.bulk_insert(policies, [{
        "id": uuid.uuid4(),
        "policy_key": "major_incident_declaration",
        "name": "Major Incident Declaration",
        "description": (
            "Decides whether a cluster of related tickets may be declared a major "
            "incident automatically. Raise the ticket threshold to make declaration "
            "harder; the change takes effect on the next run."
        ),
        "active_version": 1,
        "configuration": mi_config,
        "schema_hints": mi_hints,
        "is_active": True,
        "updated_by": "system:migration",
    }])
    op.bulk_insert(versions, [{
        "id": uuid.uuid4(),
        "policy_key": "major_incident_declaration",
        "version": 1,
        "configuration": mi_config,
        "change_note": "Initial version seeded by migration d4e5f6g7h8i9.",
        "created_by": "system:migration",
    }])


def downgrade() -> None:
    op.drop_table("integration_health")
    op.drop_table("workbench_items")
    op.drop_table("policy_evaluations")
    op.drop_table("policy_versions")
    op.drop_table("policy_definitions")
    op.drop_table("operator_events")
    op.drop_table("workflow_runs")
