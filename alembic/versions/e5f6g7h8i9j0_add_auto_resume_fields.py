"""Add Supervity Auto resume handles to workbench_items

A paused Auto run resumes when its user form is answered, so each Workbench
item needs to remember which activity run it belongs to.

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-08-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5f6g7h8i9j0"
down_revision: Union[str, None] = "d4e5f6g7h8i9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("workbench_items",
                  sa.Column("auto_activity_run_id", sa.String(255), nullable=True))
    op.add_column("workbench_items",
                  sa.Column("auto_form_id", sa.String(255), nullable=True))
    op.add_column("workbench_items",
                  sa.Column("auto_resume_result", postgresql.JSONB(), nullable=True))
    op.create_index("ix_workbench_items_auto_activity_run_id",
                    "workbench_items", ["auto_activity_run_id"])


def downgrade() -> None:
    op.drop_index("ix_workbench_items_auto_activity_run_id",
                  table_name="workbench_items")
    op.drop_column("workbench_items", "auto_resume_result")
    op.drop_column("workbench_items", "auto_form_id")
    op.drop_column("workbench_items", "auto_activity_run_id")
