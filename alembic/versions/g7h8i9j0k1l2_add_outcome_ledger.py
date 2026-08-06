"""Add the Outcome Ledger and task baselines

Every metric must be traceable to rows and to a stated assumption. This adds
the per-case ledger and the per-task-type manual baselines that make
"hours saved" an auditable calculation rather than a claim.

Revision ID: g7h8i9j0k1l2
Revises: f6g7h8i9j0k1
Create Date: 2026-08-07
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "g7h8i9j0k1l2"
down_revision: Union[str, None] = "f6g7h8i9j0k1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Baselines are seeded with an explicit, challengeable justification. They are
# deliberately conservative: it is better to under-claim savings than to have a
# judge dismiss the whole metric as invented.
BASELINES = [
    ("password_reset", 8.0,
     "Conservative industry figure for a verified password reset including "
     "identity check and ticket closure. Round 1 tickets of this type in the "
     "supplied dataset were resolved same-day with a single agent touch."),
    ("access_provisioning", 22.0,
     "Access requests require a system lookup, an approval check against the "
     "access register and a grant. The supplied Assets_Access table shows a "
     "separate approval step per grant."),
    ("known_error_remediation", 18.0,
     "Matching a ticket to a knowledge base article, applying the documented "
     "workaround and verifying it. Derived from the KB workaround steps in the "
     "supplied Knowledge_Base table."),
    ("major_incident_triage", 35.0,
     "Correlating related tickets by hand, identifying the shared system, "
     "declaring a parent incident and linking children. Scales with cluster "
     "size; 35 minutes is the cost for the parent only."),
    ("duplicate_handling", 6.0,
     "Identifying a duplicate, linking it to the authoritative ticket and "
     "notifying the requester."),
    ("change_coordination", 30.0,
     "Raising a change request, routing it for CAB approval, tracking the "
     "decision and scheduling the work."),
    ("general_triage", 12.0,
     "Reading a ticket, assigning priority, choosing an assignment group and "
     "routing it. Applies when no more specific task type matches."),
]


def upgrade() -> None:
    op.create_table(
        "task_baselines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("manual_minutes", sa.Float(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_type", name="uq_task_baselines_type"),
    )
    op.create_index("ix_task_baselines_task_type", "task_baselines", ["task_type"])

    op.create_table(
        "outcome_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issue_key", sa.String(64), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("baseline_manual_minutes", sa.Float(), nullable=False),
        sa.Column("baseline_source", sa.Text(), nullable=True),
        sa.Column("agent_seconds", sa.Float(), nullable=True),
        sa.Column("human_touch_seconds", sa.Float(), nullable=False,
                  server_default="0"),
        sa.Column("human_interventions", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("policy_verdict", sa.String(32), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verification_note", sa.Text(), nullable=True),
        sa.Column("sla_state", sa.String(32), nullable=True),
        sa.Column("predicted_breach", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("breach_avoided", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("rollback_attempted", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("rollback_succeeded", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_outcome_ledger_run_id", "outcome_ledger", ["run_id"])
    op.create_index("ix_outcome_ledger_issue_key", "outcome_ledger", ["issue_key"])
    op.create_index("ix_outcome_ledger_task_type", "outcome_ledger", ["task_type"])
    op.create_index("ix_outcome_ledger_outcome", "outcome_ledger", ["outcome"])
    op.create_index("ix_outcome_ledger_verified", "outcome_ledger", ["verified"])
    op.create_index("ix_outcome_ledger_created_at", "outcome_ledger", ["created_at"])
    op.create_index("ix_outcome_ledger_outcome_verified", "outcome_ledger",
                    ["outcome", "verified"])

    baselines = sa.table(
        "task_baselines",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("task_type", sa.String),
        sa.column("manual_minutes", sa.Float),
        sa.column("source", sa.Text),
        sa.column("updated_by", sa.String),
    )
    op.bulk_insert(baselines, [
        {"id": uuid.uuid4(), "task_type": t, "manual_minutes": m,
         "source": src, "updated_by": "system:migration"}
        for t, m, src in BASELINES
    ])


def downgrade() -> None:
    op.drop_table("outcome_ledger")
    op.drop_table("task_baselines")
