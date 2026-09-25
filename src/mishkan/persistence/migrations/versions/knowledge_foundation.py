"""Add attributed knowledge queries, corpora, operations, and promotions.

Revision ID: knowledge_foundation_v1
Revises: organization_concurrency_v1
"""

import sqlalchemy as sa
from alembic import op

revision = "knowledge_foundation_v1"
down_revision = "organization_concurrency_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_queries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("knowledge_class", sa.String(32), nullable=False),
        sa.Column("project_id", sa.String(256), nullable=False),
        sa.Column("repository_id", sa.String(256), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("bundle_artifact_id", sa.String(36), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("completed_at", sa.String(40), nullable=True),
        sa.ForeignKeyConstraint(["bundle_artifact_id"], ["artifacts.id"]),
    )
    op.create_index(
        "ix_knowledge_queries_project_created",
        "knowledge_queries",
        ["project_id", "created_at"],
    )
    op.create_table(
        "knowledge_source_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("query_id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["query_id"], ["knowledge_queries.id"]),
    )
    op.create_index(
        "ix_knowledge_attempts_query",
        "knowledge_source_attempts",
        ["query_id", "recorded_at"],
    )
    op.create_table(
        "knowledge_corpora",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(256), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("knowledge_class", sa.String(32), nullable=False),
        sa.Column("external_identity", sa.String(1024), nullable=False),
        sa.Column("indexed_revision", sa.String(512), nullable=True),
        sa.Column("snapshot_artifact_id", sa.String(36), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_artifact_id"], ["artifacts.id"]),
        sa.UniqueConstraint("project_id", "source_id", name="uq_knowledge_corpus_source"),
    )
    op.create_table(
        "knowledge_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("project_id", sa.String(256), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("corpus_id", sa.String(36), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("request_fingerprint", sa.String(71), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["corpus_id"], ["knowledge_corpora.id"]),
    )
    op.create_index(
        "ix_knowledge_operations_project_updated",
        "knowledge_operations",
        ["project_id", "updated_at"],
    )
    op.create_table(
        "knowledge_promotions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("source_project_id", sa.String(256), nullable=False),
        sa.Column("target_scope", sa.String(512), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("decided_at", sa.String(40), nullable=True),
    )
    op.create_index(
        "ix_knowledge_promotions_source",
        "knowledge_promotions",
        ["source_project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_promotions_source", table_name="knowledge_promotions")
    op.drop_table("knowledge_promotions")
    op.drop_index("ix_knowledge_operations_project_updated", table_name="knowledge_operations")
    op.drop_table("knowledge_operations")
    op.drop_table("knowledge_corpora")
    op.drop_index("ix_knowledge_attempts_query", table_name="knowledge_source_attempts")
    op.drop_table("knowledge_source_attempts")
    op.drop_index("ix_knowledge_queries_project_created", table_name="knowledge_queries")
    op.drop_table("knowledge_queries")
