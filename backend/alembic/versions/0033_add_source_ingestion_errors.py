"""add errors array column to source_ingestions

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-17

Adds ``source_ingestions.errors`` (``TEXT[]``, NOT NULL, default ``{}``) so a
failed pipeline run's error message(s) are persisted alongside its status
instead of only appearing in worker logs. Appended to (never overwritten)
each time a stage transitions to "failed" — see
``SourceIngestionRepository.add_error``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_ingestions",
        sa.Column(
            "errors",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("source_ingestions", "errors")
