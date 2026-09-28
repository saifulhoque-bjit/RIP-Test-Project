"""drop tenant contact_phone_number

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-22

Drops the ``contact_phone_number`` column from ``tenants`` — unused, removed
per product decision.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("tenants", "contact_phone_number")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("tenants", sa.Column("contact_phone_number", sa.String(length=30), nullable=True))
