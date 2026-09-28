"""add entity_json to source_ingestions

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-18

Adds an optional ``entity_json`` JSONB column to ``source_ingestions``,
used to record feedback-driven regeneration context (e.g. the feedback text
and, when supplied, the module_ids/feature_ids the regeneration targeted)
for ingestion rows that aren't tied to an uploaded Source.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "source_ingestions",
        sa.Column("entity_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("source_ingestions", "entity_json")
