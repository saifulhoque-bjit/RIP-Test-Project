"""drop sources.applied_version

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-14

Drops the ``applied_version`` column from ``sources`` — it backed the
``applied``/``change_set_ready`` legacy statuses, which are no longer
emitted by current code and have already been removed from
``SourceProcessingStatus``; no live row ever set this column.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("sources", "applied_version")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("sources", sa.Column("applied_version", sa.Integer(), nullable=True))
