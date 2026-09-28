"""add tap integration tables

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-22

Adds four tables for RIP ↔ TAP (Test Automation Platform) sync:
- tap_integrations:   per-project connection config (auth scheme TBD)
- tap_sync_mappings:  RIP entity UUID ↔ TAP entity bridge (flag flips on ack)
- tap_sync_history:   append-only audit of RIP → TAP push runs (id == sync_id)
- tap_ack_history:    append-only audit of inbound TAP → RIP acknowledgements
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tap_integrations",
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
        sa.Column("tap_base_url", sa.String(512), nullable=False),
        sa.Column("tap_project_ref", sa.String(128), nullable=True),
        sa.Column("auth_config", JSONB, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ack_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_tap_integrations_project_id", "tap_integrations", ["project_id"], unique=True
    )

    op.create_table(
        "tap_sync_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "integration_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tap_integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rip_entity_type", sa.String(32), nullable=False),
        sa.Column("rip_entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("rip_entity_code", sa.String(128), nullable=True),
        sa.Column("rip_parent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("tap_entity_id", sa.String(128), nullable=True),
        sa.Column("rip_content_hash", sa.String(128), nullable=True),
        sa.Column("rip_version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("last_push_sync_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "sync_status", sa.String(32), nullable=False, server_default=sa.text("'pending_ack'")
        ),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
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
            "integration_id", "rip_entity_type", "rip_entity_id", name="uq_tap_sync_mapping_entity"
        ),
    )
    op.create_index(
        "ix_tap_sync_mappings_integration_type",
        "tap_sync_mappings",
        ["integration_id", "rip_entity_type"],
    )
    op.create_index("ix_tap_sync_mappings_push_sync_id", "tap_sync_mappings", ["last_push_sync_id"])

    op.create_table(
        "tap_sync_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "integration_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tap_integrations.id", ondelete="CASCADE"),
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
        sa.Column("payload", JSONB, nullable=True),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default=sa.text("'pending_pull'")
        ),
        sa.Column("summary", JSONB, nullable=True),
        sa.Column("items_released", JSONB, nullable=True),
        sa.Column("items_held", JSONB, nullable=True),
        sa.Column("error_details", JSONB, nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_tap_sync_history_project_created", "tap_sync_history", ["project_id", "created_at"]
    )

    op.create_table(
        "tap_ack_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "integration_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tap_integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sync_id", UUID(as_uuid=True), nullable=True),
        sa.Column("acked_entities", JSONB, nullable=True),
        sa.Column("summary", JSONB, nullable=True),
        sa.Column("error_details", JSONB, nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default=sa.text("'tap'")),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_tap_ack_history_project_created", "tap_ack_history", ["project_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("tap_ack_history")
    op.drop_table("tap_sync_history")
    op.drop_table("tap_sync_mappings")
    op.drop_table("tap_integrations")
