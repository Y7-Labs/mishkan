"""Bind worker artifact uploads to exact active leases.

Revision ID: distributed_worker_artifacts_v1
Revises: distributed_placement_v1
"""

import sqlalchemy as sa
from alembic import op

revision = "distributed_worker_artifacts_v1"
down_revision = "distributed_placement_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_artifact_uploads",
        sa.Column("upload_id", sa.String(36), primary_key=True),
        sa.Column("lease_id", sa.String(36), nullable=False),
        sa.Column("worker_id", sa.String(36), nullable=False),
        sa.Column("artifact_id", sa.String(36), nullable=True, unique=True),
        sa.Column("expected_size", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["upload_id"], ["artifact_uploads.id"]),
        sa.ForeignKeyConstraint(["lease_id"], ["worker_task_leases.id"]),
        sa.ForeignKeyConstraint(["worker_id"], ["worker_identities.id"]),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
    )
    op.create_index(
        "ix_worker_artifact_uploads_lease_state",
        "worker_artifact_uploads",
        ["lease_id", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_worker_artifact_uploads_lease_state",
        table_name="worker_artifact_uploads",
    )
    op.drop_table("worker_artifact_uploads")
