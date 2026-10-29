"""Add durable placement queue and idempotent worker claim identities.

Revision ID: distributed_placement_v1
Revises: distributed_execution_v1
"""

import sqlalchemy as sa
from alembic import op

revision = "distributed_placement_v1"
down_revision = "distributed_execution_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "remote_task_placements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("task_row_id", sa.String(36), nullable=False, unique=True),
        sa.Column("task_key", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("preferred_worker_id", sa.String(36), nullable=True),
        sa.Column("selected_worker_id", sa.String(36), nullable=True),
        sa.Column("current_envelope_id", sa.String(36), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("request_payload", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["task_row_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["preferred_worker_id"], ["worker_identities.id"]),
        sa.ForeignKeyConstraint(["selected_worker_id"], ["worker_identities.id"]),
        sa.ForeignKeyConstraint(["current_envelope_id"], ["remote_task_envelopes.id"]),
    )
    op.create_index(
        "ix_remote_task_placements_queue",
        "remote_task_placements",
        ["state", "preferred_worker_id", "created_at"],
    )
    with op.batch_alter_table("worker_task_leases") as batch:
        batch.add_column(sa.Column("claim_id", sa.String(36), nullable=True))
        batch.create_unique_constraint("uq_worker_task_lease_claim", ["claim_id"])


def downgrade() -> None:
    with op.batch_alter_table("worker_task_leases") as batch:
        batch.drop_constraint("uq_worker_task_lease_claim", type_="unique")
        batch.drop_column("claim_id")
    op.drop_index("ix_remote_task_placements_queue", table_name="remote_task_placements")
    op.drop_table("remote_task_placements")
