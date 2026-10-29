"""Add individually revocable distributed worker identities.

Revision ID: distributed_identity_v1
Revises: scheduling_foundation_v1
"""

import sqlalchemy as sa
from alembic import op

revision = "distributed_identity_v1"
down_revision = "scheduling_foundation_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("current_certificate_serial", sa.String(128), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.Column("revoked_at", sa.String(40), nullable=True),
    )
    op.create_index(
        "ix_worker_identities_state",
        "worker_identities",
        ["state", "updated_at"],
    )
    op.create_table(
        "worker_enrollment_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(256), nullable=False),
        sa.Column("worker_id", sa.String(36), nullable=True),
        sa.Column("expires_at", sa.String(40), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("consumed_at", sa.String(40), nullable=True),
        sa.Column("revoked_at", sa.String(40), nullable=True),
        sa.ForeignKeyConstraint(["worker_id"], ["worker_identities.id"]),
    )
    op.create_index(
        "ix_worker_enrollment_tokens_state_expiry",
        "worker_enrollment_tokens",
        ["state", "expires_at"],
    )
    op.create_table(
        "worker_certificates",
        sa.Column("serial", sa.String(128), primary_key=True),
        sa.Column("worker_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("not_before", sa.String(40), nullable=False),
        sa.Column("not_after", sa.String(40), nullable=False),
        sa.Column("issued_at", sa.String(40), nullable=False),
        sa.Column("superseded_at", sa.String(40), nullable=True),
        sa.Column("revoked_at", sa.String(40), nullable=True),
        sa.ForeignKeyConstraint(["worker_id"], ["worker_identities.id"]),
    )
    op.create_index(
        "ix_worker_certificates_worker_state",
        "worker_certificates",
        ["worker_id", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_certificates_worker_state", table_name="worker_certificates")
    op.drop_table("worker_certificates")
    op.drop_index(
        "ix_worker_enrollment_tokens_state_expiry",
        table_name="worker_enrollment_tokens",
    )
    op.drop_table("worker_enrollment_tokens")
    op.drop_index("ix_worker_identities_state", table_name="worker_identities")
    op.drop_table("worker_identities")
