"""Add distributed heartbeats, immutable envelopes, leases, and completions.

Revision ID: distributed_execution_v1
Revises: distributed_identity_v1
"""

import sqlalchemy as sa
from alembic import op

revision = "distributed_execution_v1"
down_revision = "distributed_identity_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(36), primary_key=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("advertisement_fingerprint", sa.String(64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["worker_id"], ["worker_identities.id"]),
    )
    op.create_index(
        "ix_worker_heartbeats_expiry",
        "worker_heartbeats",
        ["expires_at", "worker_id"],
    )
    op.create_table(
        "remote_task_envelopes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("task_row_id", sa.String(36), nullable=False),
        sa.Column("task_key", sa.String(64), nullable=False),
        sa.Column("task_attempt", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("stateful_effects", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("deadline", sa.String(40), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["task_row_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["worker_id"], ["worker_identities.id"]),
        sa.UniqueConstraint(
            "task_row_id",
            "task_attempt",
            name="uq_remote_task_envelope_attempt",
        ),
    )
    op.create_index(
        "ix_remote_task_envelopes_worker_state",
        "remote_task_envelopes",
        ["worker_id", "state", "created_at"],
    )
    op.create_index(
        "ix_remote_task_envelopes_run_task",
        "remote_task_envelopes",
        ["run_id", "task_key", "task_attempt"],
    )
    op.create_table(
        "worker_task_leases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("envelope_id", sa.String(36), nullable=False, unique=True),
        sa.Column("worker_id", sa.String(36), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
        sa.Column("last_renewed_at", sa.String(40), nullable=False),
        sa.Column("completed_at", sa.String(40), nullable=True),
        sa.ForeignKeyConstraint(["envelope_id"], ["remote_task_envelopes.id"]),
        sa.ForeignKeyConstraint(["worker_id"], ["worker_identities.id"]),
    )
    op.create_index(
        "ix_worker_task_leases_active",
        "worker_task_leases",
        ["state", "expires_at", "worker_id"],
    )
    op.create_table(
        "worker_task_completions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("envelope_id", sa.String(36), nullable=False),
        sa.Column("lease_id", sa.String(36), nullable=False),
        sa.Column("worker_id", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("received_at", sa.String(40), nullable=False),
        sa.Column("accepted_at", sa.String(40), nullable=True),
        sa.Column("decided_at", sa.String(40), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["envelope_id"], ["remote_task_envelopes.id"]),
        sa.ForeignKeyConstraint(["lease_id"], ["worker_task_leases.id"]),
        sa.ForeignKeyConstraint(["worker_id"], ["worker_identities.id"]),
    )
    op.create_index(
        "ix_worker_task_completions_envelope",
        "worker_task_completions",
        ["envelope_id", "received_at"],
    )
    op.create_table(
        "worker_task_acceptances",
        sa.Column("task_row_id", sa.String(36), primary_key=True),
        sa.Column("completion_id", sa.String(36), nullable=False, unique=True),
        sa.Column("envelope_id", sa.String(36), nullable=False),
        sa.Column("accepted_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["task_row_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["completion_id"], ["worker_task_completions.id"]),
        sa.ForeignKeyConstraint(["envelope_id"], ["remote_task_envelopes.id"]),
    )


def downgrade() -> None:
    op.drop_table("worker_task_acceptances")
    op.drop_index(
        "ix_worker_task_completions_envelope",
        table_name="worker_task_completions",
    )
    op.drop_table("worker_task_completions")
    op.drop_index("ix_worker_task_leases_active", table_name="worker_task_leases")
    op.drop_table("worker_task_leases")
    op.drop_index("ix_remote_task_envelopes_run_task", table_name="remote_task_envelopes")
    op.drop_index("ix_remote_task_envelopes_worker_state", table_name="remote_task_envelopes")
    op.drop_table("remote_task_envelopes")
    op.drop_index("ix_worker_heartbeats_expiry", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
