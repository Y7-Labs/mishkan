"""Add cancellation and attempt metadata to durable run graphs.

Revision ID: i03_runtime
Revises: i03_sessions
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "i03_runtime"
down_revision = "i03_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(
            sa.Column(
                "cancellation_requested",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("updated_at", sa.String(40), nullable=True))
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("updated_at", sa.String(40), nullable=True))
    op.execute("UPDATE runs SET updated_at = created_at WHERE updated_at IS NULL")
    op.execute(
        "UPDATE tasks SET updated_at = "
        "(SELECT created_at FROM runs WHERE runs.id = tasks.run_id) "
        "WHERE updated_at IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("updated_at")
        batch.drop_column("attempt_count")
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("updated_at")
        batch.drop_column("cancellation_requested")
