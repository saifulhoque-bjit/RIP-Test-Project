"""add no_changes_explanation to source_ingestions

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-31

Adds ``source_ingestions.no_changes_explanation`` — set only by the
incremental-update pipeline when it proposed no changes at all, holding the
AI's plain-language explanation of why. NULL on any run that produced
changes, and on non-incremental ingestions.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "source_ingestions",
        sa.Column("no_changes_explanation", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("source_ingestions", "no_changes_explanation")
