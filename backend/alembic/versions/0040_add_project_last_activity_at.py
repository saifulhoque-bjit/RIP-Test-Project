"""add last_activity_at to projects

Revision ID: 0040
Revises: 0039
Create Date: 2026-08-27

Adds ``last_activity_at`` to ``projects`` — updated by
``ActivityLogService.record_activity`` every time an ActivityLog row is
recorded for the project, so the API can surface "last activity" without a
separate aggregation query over activity_logs. Nullable; existing rows are
left ``NULL`` until their next activity is logged.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0040"
down_revision: str | Sequence[str] | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "projects",
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("projects", "last_activity_at")
