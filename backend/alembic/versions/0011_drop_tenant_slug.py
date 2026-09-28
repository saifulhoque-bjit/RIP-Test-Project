"""drop tenant slug

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-21

Drops ``tenants.slug`` — it was never used for anything except an internal
uniqueness/lookup key, which the default-tenant seed now does by name
instead.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_column("tenants", "slug")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("tenants", sa.Column("slug", sa.String(length=100), nullable=True))
    op.execute(
        "UPDATE tenants SET slug = lower(regexp_replace(name, '[^a-zA-Z0-9]+', '-', 'g')) || '-' || substr(id::text, 1, 8)"
    )
    op.alter_column("tenants", "slug", nullable=False)
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)
