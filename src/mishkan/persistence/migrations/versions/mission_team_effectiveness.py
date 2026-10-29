"""Add durable Mission Crew collaboration and effectiveness evidence.

Revision ID: mission_team_effectiveness_v1
Revises: distributed_rotation_recovery_v1
"""

import sqlalchemy as sa
from alembic import op

revision = "mission_team_effectiveness_v1"
down_revision = "distributed_rotation_recovery_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mission_collaboration_plans",
        sa.Column("plan_id", sa.String(36), primary_key=True),
        sa.Column("mission_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("brief_version", sa.Integer(), nullable=False),
        sa.Column("crew_version", sa.Integer(), nullable=False),
        sa.Column("production_shape", sa.String(32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"]),
        sa.UniqueConstraint("mission_id", "version"),
    )
    op.create_table(
        "team_health_evaluations",
        sa.Column("evaluation_id", sa.String(36), primary_key=True),
        sa.Column("mission_id", sa.String(36), nullable=False),
        sa.Column("collaboration_plan_version", sa.Integer(), nullable=False),
        sa.Column("evidence_cursor", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("evaluator_identity", sa.String(128), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"]),
    )
    op.create_index(
        "ix_team_health_mission_cursor",
        "team_health_evaluations",
        ["mission_id", "evidence_cursor"],
    )
    op.create_table(
        "crew_adaptation_decisions",
        sa.Column("decision_id", sa.String(36), primary_key=True),
        sa.Column("mission_id", sa.String(36), nullable=False),
        sa.Column("before_plan_version", sa.Integer(), nullable=False),
        sa.Column("after_plan_version", sa.Integer(), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"]),
    )
    op.create_table(
        "mission_retrospectives",
        sa.Column("retrospective_id", sa.String(36), primary_key=True),
        sa.Column("mission_id", sa.String(36), nullable=False),
        sa.Column("collaboration_plan_version", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"]),
    )
    op.create_table(
        "team_signals",
        sa.Column("signal_id", sa.String(36), primary_key=True),
        sa.Column("mission_id", sa.String(36), nullable=False),
        sa.Column("collaboration_plan_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"]),
    )
    op.create_index("ix_team_signals_mission_kind", "team_signals", ["mission_id", "kind"])
    op.create_table(
        "mission_handoffs",
        sa.Column("handoff_id", sa.String(36), primary_key=True),
        sa.Column("mission_id", sa.String(36), nullable=False),
        sa.Column("collaboration_plan_version", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(256), nullable=False),
        sa.Column("from_identity", sa.String(128), nullable=False),
        sa.Column("to_identity", sa.String(128), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"]),
    )


def downgrade() -> None:
    op.drop_table("mission_handoffs")
    op.drop_index("ix_team_signals_mission_kind", table_name="team_signals")
    op.drop_table("team_signals")
    op.drop_table("mission_retrospectives")
    op.drop_table("crew_adaptation_decisions")
    op.drop_index("ix_team_health_mission_cursor", table_name="team_health_evaluations")
    op.drop_table("team_health_evaluations")
    op.drop_table("mission_collaboration_plans")
