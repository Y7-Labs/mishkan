"""Prevent reuse of retained-away event cursors.

Revision ID: event_cursor_highwater_v1
Revises: session_effect_evidence_v1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "event_cursor_highwater_v1"
down_revision = "session_effect_evidence_v1"
branch_labels = None
depends_on = None


def _drop_indexes() -> None:
    for name in (
        "ix_event_outbox_type_cursor",
        "ix_event_outbox_entity_cursor",
        "ix_event_outbox_run_cursor",
        "ix_event_outbox_task_cursor",
        "ix_event_outbox_identity_cursor",
        "ix_event_outbox_team_cursor",
        "ix_event_outbox_security_cursor",
    ):
        op.drop_index(name, table_name="event_outbox")


def _create(*, monotone: bool) -> None:
    op.create_table(
        "event_outbox",
        sa.Column("cursor", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id", sa.String(36), nullable=False, unique=True),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("aggregate_id", sa.String(256), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(256), nullable=True),
        sa.Column("task_id", sa.String(256), nullable=True),
        sa.Column("identity_id", sa.String(256), nullable=True),
        sa.Column("team_id", sa.String(256), nullable=True),
        sa.Column(
            "security_relevant",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.String(40), nullable=False),
        sa.Column("command_id", sa.String(36), nullable=True),
        sa.Column("correlation_id", sa.String(36), nullable=True),
        sa.Column("causation_id", sa.String(36), nullable=True),
        sa.Column("sensitivity", sa.String(32), nullable=False),
        sa.Column("published_at", sa.String(40), nullable=True),
        sqlite_autoincrement=monotone,
    )
    op.create_index("ix_event_outbox_type_cursor", "event_outbox", ["event_type", "cursor"])
    op.create_index(
        "ix_event_outbox_entity_cursor",
        "event_outbox",
        ["entity_type", "aggregate_id", "cursor"],
    )
    op.create_index("ix_event_outbox_run_cursor", "event_outbox", ["run_id", "cursor"])
    op.create_index("ix_event_outbox_task_cursor", "event_outbox", ["task_id", "cursor"])
    op.create_index("ix_event_outbox_identity_cursor", "event_outbox", ["identity_id", "cursor"])
    op.create_index("ix_event_outbox_team_cursor", "event_outbox", ["team_id", "cursor"])
    op.create_index(
        "ix_event_outbox_security_cursor",
        "event_outbox",
        ["security_relevant", "cursor"],
    )


def _copy(source: str) -> None:
    columns = (
        "cursor, id, schema_version, aggregate_id, entity_type, run_id, task_id, "
        "identity_id, team_id, security_relevant, event_type, source, payload, "
        "occurred_at, command_id, correlation_id, causation_id, sensitivity, published_at"
    )
    op.execute(f"INSERT INTO event_outbox ({columns}) SELECT {columns} FROM {source}")


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    with op.batch_alter_table("runs") as batch:
        batch.alter_column(
            "updated_at", existing_type=sa.String(40), existing_nullable=True, nullable=False
        )
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column(
            "updated_at", existing_type=sa.String(40), existing_nullable=True, nullable=False
        )
    _drop_indexes()
    op.rename_table("event_outbox", "event_outbox_reusable")
    _create(monotone=True)
    _copy("event_outbox_reusable")
    op.execute("DROP TABLE event_outbox_reusable")
    if dialect == "sqlite":
        op.execute("DELETE FROM sqlite_sequence WHERE name = 'event_outbox'")
        op.execute(
            "INSERT INTO sqlite_sequence(name, seq) "
            "SELECT 'event_outbox', MAX(cursor) FROM ("
            "SELECT COALESCE(MAX(cursor), 0) AS cursor FROM event_outbox "
            "UNION ALL "
            "SELECT COALESCE(MAX(event_cursor), 0) AS cursor FROM application_commands"
            ")"
        )
    elif dialect == "postgresql":
        op.execute(
            "SELECT setval(pg_get_serial_sequence('event_outbox', 'cursor'), "
            "GREATEST("
            "COALESCE((SELECT MAX(cursor) FROM event_outbox), 0), "
            "COALESCE((SELECT MAX(event_cursor) FROM application_commands), 0), 1), true)"
        )


def downgrade() -> None:
    _drop_indexes()
    op.rename_table("event_outbox", "event_outbox_monotone")
    _create(monotone=False)
    _copy("event_outbox_monotone")
    op.execute("DROP TABLE event_outbox_monotone")
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column(
            "updated_at", existing_type=sa.String(40), existing_nullable=False, nullable=True
        )
    with op.batch_alter_table("runs") as batch:
        batch.alter_column(
            "updated_at", existing_type=sa.String(40), existing_nullable=False, nullable=True
        )
