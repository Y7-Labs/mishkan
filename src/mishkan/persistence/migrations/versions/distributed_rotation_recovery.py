"""Persist idempotent worker certificate rotation outcomes.

Revision ID: distributed_rotation_recovery_v1
Revises: distributed_worker_artifacts_v1
"""

import sqlalchemy as sa
from alembic import op

revision = "distributed_rotation_recovery_v1"
down_revision = "distributed_worker_artifacts_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_certificate_rotations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("worker_id", sa.String(36), nullable=False),
        sa.Column("prior_serial", sa.String(128), nullable=False),
        sa.Column("certificate_serial", sa.String(128), nullable=False, unique=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("certificate_pem", sa.Text(), nullable=False),
        sa.Column("certificate_authority_pem", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["worker_id"], ["worker_identities.id"]),
        sa.ForeignKeyConstraint(["prior_serial"], ["worker_certificates.serial"]),
        sa.ForeignKeyConstraint(["certificate_serial"], ["worker_certificates.serial"]),
    )
    op.create_index(
        "ix_worker_certificate_rotations_worker_created",
        "worker_certificate_rotations",
        ["worker_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_worker_certificate_rotations_worker_created",
        table_name="worker_certificate_rotations",
    )
    op.drop_table("worker_certificate_rotations")
