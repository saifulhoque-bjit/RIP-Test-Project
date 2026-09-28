"""add api key + test status to tenant_llm_providers

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-23

Adds per-provider enable/disable and API key/connection-test tracking to
``tenant_llm_providers``:
  - ``is_active``: explicit enable/disable toggle, independent of whether a
    row/key exists (previously row existence alone meant "enabled").
    Existing rows are backfilled to ``true`` so today's enabled set is
    preserved.
  - ``api_key_encrypted``: Fernet-encrypted tenant-supplied API key (see
    ``app.utils.encryption.encrypt_llm_api_key``).
  - ``is_verified`` / ``last_tested_at`` / ``last_test_error``: outcome of
    the most recent connection test for that key.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "tenant_llm_providers",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "tenant_llm_providers",
        sa.Column("api_key_encrypted", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "tenant_llm_providers",
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "tenant_llm_providers",
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tenant_llm_providers", sa.Column("last_test_error", sa.String(length=500), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tenant_llm_providers", "last_test_error")
    op.drop_column("tenant_llm_providers", "last_tested_at")
    op.drop_column("tenant_llm_providers", "is_verified")
    op.drop_column("tenant_llm_providers", "api_key_encrypted")
    op.drop_column("tenant_llm_providers", "is_active")
