"""add jira integration tables

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-15

Adds three tables for RIP → Jira one-way sync:
- jira_integrations:   per-project connection config
- jira_sync_mappings:  RIP entity UUID ↔ Jira issue key bridge
- jira_sync_history:   append-only audit of sync runs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jira_integrations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "created_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("jira_base_url", sa.String(512), nullable=False),
        sa.Column("jira_project_key", sa.String(32), nullable=False),
        sa.Column("jira_board_id", sa.String(32), nullable=True),
        sa.Column("jira_user_email", sa.String(320), nullable=False),
        sa.Column("jira_api_token_encrypted", sa.String(1024), nullable=False),
        sa.Column("issue_type_name", sa.String(64), nullable=False, server_default="Story"),
        sa.Column("epic_issue_type_name", sa.String(64), nullable=False, server_default="Epic"),
        sa.Column("traceability_field_ids", JSONB, nullable=True),
        sa.Column("deprecated_transition_id", sa.String(32), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_jira_integrations_project_id", "jira_integrations", ["project_id"], unique=True
    )

    op.create_table(
        "jira_sync_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "integration_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jira_integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rip_entity_type", sa.String(32), nullable=False),
        sa.Column("rip_entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("rip_entity_code", sa.String(128), nullable=True),
        sa.Column("jira_issue_key", sa.String(64), nullable=False),
        sa.Column("jira_issue_id", sa.String(64), nullable=False),
        sa.Column("rip_content_hash", sa.String(128), nullable=True),
        sa.Column("rip_version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("sync_status", sa.String(32), nullable=False, server_default=sa.text("'synced'")),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "integration_id", "rip_entity_type", "rip_entity_id", name="uq_jira_sync_mapping_entity"
        ),
    )
    op.create_index(
        "ix_jira_sync_mappings_jira_issue_key", "jira_sync_mappings", ["jira_issue_key"]
    )
    op.create_index(
        "ix_jira_sync_mappings_integration_type",
        "jira_sync_mappings",
        ["integration_id", "rip_entity_type"],
    )

    op.create_table(
        "jira_sync_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "integration_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jira_integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "triggered_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'in_progress'")),
        sa.Column("summary", JSONB, nullable=True),
        sa.Column("items_released", JSONB, nullable=True),
        sa.Column("items_held", JSONB, nullable=True),
        sa.Column("error_details", JSONB, nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_jira_sync_history_project_created", "jira_sync_history", ["project_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("jira_sync_history")
    op.drop_table("jira_sync_mappings")
    op.drop_table("jira_integrations")
