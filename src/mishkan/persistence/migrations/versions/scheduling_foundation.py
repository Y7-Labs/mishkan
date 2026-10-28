"""Add persistent schedules and idempotent trigger occurrences.

Revision ID: scheduling_foundation_v1
Revises: knowledge_foundation_v1
"""

import sqlalchemy as sa
from alembic import op

revision = "scheduling_foundation_v1"
down_revision = "knowledge_foundation_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(256), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("trigger_kind", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(128), nullable=True),
        sa.Column("overlap_key", sa.String(256), nullable=False),
        sa.Column("overlap_mode", sa.String(16), nullable=False),
        sa.Column("next_fire_at", sa.String(40), nullable=True),
        sa.Column("last_evaluated_at", sa.String(40), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(256), nullable=False),
        sa.Column("policy_fingerprint", sa.String(64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_schedules_due",
        "schedules",
        ["state", "next_fire_at"],
    )
    op.create_index(
        "ix_schedules_project_updated",
        "schedules",
        ["project_id", "updated_at"],
    )
    op.create_table(
        "schedule_occurrences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("occurrence_key", sa.String(256), nullable=False),
        sa.Column("schedule_id", sa.String(36), nullable=False),
        sa.Column("schedule_revision", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("scheduled_for", sa.String(40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("run_command_id", sa.String(36), nullable=True),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("next_attempt_at", sa.String(40), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedules.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.UniqueConstraint(
            "schedule_id",
            "occurrence_key",
            name="uq_schedule_occurrence_identity",
        ),
    )
    op.create_index(
        "ix_schedule_occurrences_history",
        "schedule_occurrences",
        ["schedule_id", "scheduled_for"],
    )
    op.create_index(
        "ix_schedule_occurrences_retry",
        "schedule_occurrences",
        ["state", "next_attempt_at"],
    )
    op.create_index(
        "ix_schedule_occurrences_overlap",
        "schedule_occurrences",
        ["state", "run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_occurrences_overlap", table_name="schedule_occurrences")
    op.drop_index("ix_schedule_occurrences_retry", table_name="schedule_occurrences")
    op.drop_index("ix_schedule_occurrences_history", table_name="schedule_occurrences")
    op.drop_table("schedule_occurrences")
    op.drop_index("ix_schedules_project_updated", table_name="schedules")
    op.drop_index("ix_schedules_due", table_name="schedules")
    op.drop_table("schedules")
