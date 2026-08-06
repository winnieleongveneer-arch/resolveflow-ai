"""Seed the Safe Auto-Remediation and Change & CAB Control policies

Round 2 requires at least three active AI Policies, each editable without
code. Seeding them in a migration means a clean clone comes up compliant.

Revision ID: f6g7h8i9j0k1
Revises: e5f6g7h8i9j0
Create Date: 2026-08-06
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6g7h8i9j0k1"
down_revision: Union[str, None] = "e5f6g7h8i9j0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


POLICIES = [
    {
        "policy_key": "safe_auto_remediation",
        "name": "Safe Auto-Remediation",
        "description": (
            "The autonomy dial. Decides whether the agent may apply a known fix "
            "on its own. Lower the confidence threshold to automate more; raise "
            "it to send more work to a human."
        ),
        "configuration": {
            "minimum_confidence": 0.85,
            "require_kb_auto_safe": True,
            "require_reversible": True,
            "block_if_reopened": True,
            "block_if_major_incident": True,
            "block_if_production_impact": True,
        },
        "schema_hints": {
            "minimum_confidence": {
                "label": "Minimum confidence to act alone",
                "help": "Below this, the fix goes to the Workbench instead.",
                "type": "float", "min": 0.0, "max": 1.0, "step": 0.01,
            },
            "require_kb_auto_safe": {
                "label": "Require an auto-safe KB article",
                "help": "Only fixes a knowledge author cleared may run unattended.",
                "type": "boolean",
            },
            "require_reversible": {
                "label": "Require the action to be reversible",
                "help": "Irreversible actions are never taken autonomously.",
                "type": "boolean",
            },
            "block_if_reopened": {
                "label": "Block reopened tickets",
                "help": "A previous fix did not hold, so do not simply repeat it.",
                "type": "boolean",
            },
            "block_if_major_incident": {
                "label": "Block during a major incident",
                "help": "Individual fixes can mask a live root cause.",
                "type": "boolean",
            },
            "block_if_production_impact": {
                "label": "Block production-affecting actions",
                "help": "Send anything touching production to a human.",
                "type": "boolean",
            },
        },
    },
    {
        "policy_key": "change_and_cab_control",
        "name": "Change and CAB Control",
        "description": (
            "Decides whether an action needs a recorded approver before it "
            "touches anything. Covers CAB approval, production impact, risk "
            "level, blast radius and restricted categories."
        ),
        "configuration": {
            "require_approval_if_production": True,
            "risk_levels_requiring_approval": ["Medium", "High"],
            "max_blast_radius": 25,
            "restricted_categories": ["access", "infrastructure", "critical_service"],
            "deny_if_previously_rolled_back": True,
        },
        "schema_hints": {
            "require_approval_if_production": {
                "label": "Require approval for production changes",
                "type": "boolean",
            },
            "risk_levels_requiring_approval": {
                "label": "Risk levels needing approval",
                "help": "Change risk values that force a human decision.",
                "type": "list", "options": ["Low", "Medium", "High"],
            },
            "max_blast_radius": {
                "label": "Maximum unattended blast radius",
                "help": "Users or systems that may be affected without approval.",
                "type": "integer", "min": 1, "max": 500, "step": 1,
            },
            "restricted_categories": {
                "label": "Restricted action categories",
                "help": "Categories that always need an approver.",
                "type": "list",
                "options": ["access", "infrastructure", "critical_service", "routine"],
            },
            "deny_if_previously_rolled_back": {
                "label": "Block changes that were rolled back before",
                "help": "A failed change needs a new plan, not a retry.",
                "type": "boolean",
            },
        },
    },
]


def upgrade() -> None:
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

    for spec in POLICIES:
        op.bulk_insert(policies, [{
            "id": uuid.uuid4(),
            "policy_key": spec["policy_key"],
            "name": spec["name"],
            "description": spec["description"],
            "active_version": 1,
            "configuration": spec["configuration"],
            "schema_hints": spec["schema_hints"],
            "is_active": True,
            "updated_by": "system:migration",
        }])
        op.bulk_insert(versions, [{
            "id": uuid.uuid4(),
            "policy_key": spec["policy_key"],
            "version": 1,
            "configuration": spec["configuration"],
            "change_note": "Initial version seeded by migration f6g7h8i9j0k1.",
            "created_by": "system:migration",
        }])


def downgrade() -> None:
    keys = tuple(p["policy_key"] for p in POLICIES)
    op.execute(
        sa.text("DELETE FROM policy_versions WHERE policy_key IN :k")
        .bindparams(sa.bindparam("k", value=keys, expanding=True))
    )
    op.execute(
        sa.text("DELETE FROM policy_definitions WHERE policy_key IN :k")
        .bindparams(sa.bindparam("k", value=keys, expanding=True))
    )
