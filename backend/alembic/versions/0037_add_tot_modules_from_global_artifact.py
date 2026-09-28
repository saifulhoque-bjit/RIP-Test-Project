"""add tot_modules_from_global_artifact to source_ingestions

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-20

Adds a column tracking how many modules the source-code pipeline's
global-artifact discovery phase detected (``len(global_artifacts["filtered_modules"])``),
before per-module processing runs. This is distinct from ``tot_modules``,
which counts modules that actually succeeded through persistence — a module
detected here can still fail during processing and be excluded from
``tot_modules``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "source_ingestions",
        sa.Column(
            "tot_modules_from_global_artifact", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("source_ingestions", "tot_modules_from_global_artifact")
