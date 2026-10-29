"""Add durable collaboration execution lineage.

Revision ID: mission_collaboration_lineage_v1
Revises: mission_team_effectiveness_v1
"""

import sqlalchemy as sa
from alembic import op

revision = "mission_collaboration_lineage_v1"
down_revision = "mission_team_effectiveness_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mission_collaboration_executions",
        sa.Column("interaction_id", sa.String(36), primary_key=True),
        sa.Column("mission_id", sa.String(36), nullable=False),
        sa.Column("collaboration_plan_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("causal_reference", sa.String(64), nullable=False, unique=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"]),
    )
    op.create_index(
        "ix_collaboration_execution_mission_kind",
        "mission_collaboration_executions",
        ["mission_id", "kind"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_collaboration_execution_mission_kind",
        table_name="mission_collaboration_executions",
    )
    op.drop_table("mission_collaboration_executions")
