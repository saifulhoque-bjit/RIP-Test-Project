"""add tenant contact fields and status

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-21

Adds contact_email, contact_phone_number, address to ``tenants``, and
replaces the boolean ``is_active`` flag with a four-value ``status``
(pending_invitation | active | inactive | suspended) column. Existing rows
are backfilled: is_active=True -> active, is_active=False -> inactive.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("tenants", sa.Column("contact_email", sa.String(length=320), nullable=True))
    op.add_column("tenants", sa.Column("contact_phone_number", sa.String(length=30), nullable=True))
    op.add_column("tenants", sa.Column("address", sa.Text(), nullable=True))

    op.add_column("tenants", sa.Column("status", sa.String(length=30), nullable=True))
    op.execute("UPDATE tenants SET status = CASE WHEN is_active THEN 'active' ELSE 'inactive' END")
    op.alter_column("tenants", "status", nullable=False, server_default="active")
    op.drop_column("tenants", "is_active")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("tenants", sa.Column("is_active", sa.Boolean(), nullable=True))
    op.execute("UPDATE tenants SET is_active = (status = 'active')")
    op.alter_column("tenants", "is_active", nullable=False, server_default=sa.true())

    op.drop_column("tenants", "status")
    op.drop_column("tenants", "address")
    op.drop_column("tenants", "contact_phone_number")
    op.drop_column("tenants", "contact_email")
