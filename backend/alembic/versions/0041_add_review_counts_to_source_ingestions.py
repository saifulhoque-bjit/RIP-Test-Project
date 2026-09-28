"""add accepted/rejected review counts to source_ingestions

Revision ID: 0041
Revises: 0040
Create Date: 2026-08-27

Adds six counters — ``tot_{modules,features,user_stories}_{accepted,rejected}``
— to ``source_ingestions``. These replace the per-decision ActivityLog entries
previously written on every incremental-update/feedback-driven accept or
reject: instead of one feed row per decision, the tagging ingestion's running
total is bumped. Additive, ``server_default='0'`` so existing rows backfill
to zero.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0041"
down_revision: str | Sequence[str] | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "tot_modules_accepted",
    "tot_modules_rejected",
    "tot_features_accepted",
    "tot_features_rejected",
    "tot_user_stories_accepted",
    "tot_user_stories_rejected",
)


def upgrade() -> None:
    """Upgrade schema."""
    for column_name in _COLUMNS:
        op.add_column(
            "source_ingestions",
            sa.Column(column_name, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    """Downgrade schema."""
    for column_name in _COLUMNS:
        op.drop_column("source_ingestions", column_name)
