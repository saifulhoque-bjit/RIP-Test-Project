"""add version to projects

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-15

Adds a ``version`` integer column to ``projects``, mirroring the existing
version-counter convention used for Module/Feature/UserStory nodes in Neo4j
(see app.core.constants.INITIAL_ENTITY_VERSION). This is a plain change
counter bumped on every update, not a history/audit table.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "projects",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("projects", "version")
