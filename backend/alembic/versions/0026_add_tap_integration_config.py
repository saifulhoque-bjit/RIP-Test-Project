"""re-add per-project tap integration config

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-12

Re-introduces the ``tap_integrations`` table that was removed in 0024.
This time the table stores named app-client credentials (base_url,
app_client_name, encrypted api_key, client_id) per project, allowing each
project to connect to a different TAP deployment or use different credentials.
The sync service uses these credentials in preference to the global env-var
fallbacks (TAP_BASE_URL / TAP_API_KEY / TAP_APP_CLIENT_ID).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
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
        sa.Column("base_url", sa.String(512), nullable=False),
        sa.Column("app_client_name", sa.String(128), nullable=False),
        sa.Column("api_key_encrypted", sa.String(1024), nullable=False),
        sa.Column("client_id", sa.String(256), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_tap_integrations_project_id", "tap_integrations", ["project_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_tap_integrations_project_id", table_name="tap_integrations")
    op.drop_table("tap_integrations")
