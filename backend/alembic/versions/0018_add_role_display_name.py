"""add display_name to roles

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-23

Adds a human-friendly ``display_name`` to ``roles``, distinct from the
stable ``name`` identifier used in authorization checks. Existing rows are
backfilled from ``name`` before the column is made NOT NULL, so no data
migration script is needed outside this file.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("roles", sa.Column("display_name", sa.String(length=100), nullable=True))
    roles = sa.table("roles", sa.column("display_name", sa.String), sa.column("name", sa.String))
    op.execute(
        roles.update().where(roles.c.display_name.is_(None)).values(display_name=roles.c.name)
    )
    op.alter_column("roles", "display_name", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("roles", "display_name")
