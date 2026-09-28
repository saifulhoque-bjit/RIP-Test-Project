"""add tot_modules_failed to source_ingestions

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-02

Adds ``tot_modules_failed`` to ``source_ingestions`` — a running count of
modules that failed processing in the source-code pipeline, updated
alongside ``tot_modules``/``tot_features``/``tot_user_stories`` once every
module has finished. Additive, ``server_default='0'`` so existing rows
backfill to zero.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0042"
down_revision: str | Sequence[str] | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "source_ingestions",
        sa.Column("tot_modules_failed", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("source_ingestions", "tot_modules_failed")
