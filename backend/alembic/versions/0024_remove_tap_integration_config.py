"""remove per-project tap integration config

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-29

TAP is a single shared platform-wide deployment, not configured per RIP
project — the ``tap_integrations`` table (and the ``integration_id`` FK on
every other TAP table) existed only to let each project point at a different
TAP instance, which was never actually needed. The base URL/auth token now
live as plain settings (``TAP_BASE_URL``/``TAP_AUTH_TOKEN``), so this drops
the whole per-project config layer:

- tap_integrations: dropped entirely.
- tap_sync_mappings: drop ``integration_id`` (+ its FK/unique constraint/index);
  unique constraint becomes ``(rip_entity_type, rip_entity_id)`` alone.
- tap_sync_history / tap_ack_history: drop ``integration_id`` (+ its FK).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── tap_sync_mappings ───────────────────────────────────────────────────
    op.drop_index("ix_tap_sync_mappings_integration_type", table_name="tap_sync_mappings")
    op.drop_constraint("uq_tap_sync_mapping_entity", "tap_sync_mappings", type_="unique")
    op.drop_constraint(
        "tap_sync_mappings_integration_id_fkey", "tap_sync_mappings", type_="foreignkey"
    )
    op.drop_column("tap_sync_mappings", "integration_id")
    op.create_unique_constraint(
        "uq_tap_sync_mapping_entity",
        "tap_sync_mappings",
        ["rip_entity_type", "rip_entity_id"],
    )

    # ── tap_sync_history ────────────────────────────────────────────────────
    op.drop_constraint(
        "tap_sync_history_integration_id_fkey", "tap_sync_history", type_="foreignkey"
    )
    op.drop_column("tap_sync_history", "integration_id")

    # ── tap_ack_history ─────────────────────────────────────────────────────
    op.drop_constraint("tap_ack_history_integration_id_fkey", "tap_ack_history", type_="foreignkey")
    op.drop_column("tap_ack_history", "integration_id")

    # ── tap_integrations ────────────────────────────────────────────────────
    op.drop_table("tap_integrations")


def downgrade() -> None:
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

    op.add_column(
        "tap_sync_mappings",
        sa.Column("integration_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "tap_sync_mappings_integration_id_fkey",
        "tap_sync_mappings",
        "tap_integrations",
        ["integration_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_tap_sync_mapping_entity", "tap_sync_mappings", type_="unique")
    op.create_unique_constraint(
        "uq_tap_sync_mapping_entity",
        "tap_sync_mappings",
        ["integration_id", "rip_entity_type", "rip_entity_id"],
    )
    op.create_index(
        "ix_tap_sync_mappings_integration_type",
        "tap_sync_mappings",
        ["integration_id", "rip_entity_type"],
    )

    op.add_column(
        "tap_sync_history",
        sa.Column("integration_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "tap_sync_history_integration_id_fkey",
        "tap_sync_history",
        "tap_integrations",
        ["integration_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.add_column(
        "tap_ack_history",
        sa.Column("integration_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "tap_ack_history_integration_id_fkey",
        "tap_ack_history",
        "tap_integrations",
        ["integration_id"],
        ["id"],
        ondelete="CASCADE",
    )
