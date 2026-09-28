"""rename sc_architecture_type to source_layout_type

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-20

Renames ``source_ingestions.sc_architecture_type`` to
``source_ingestions.source_layout_type`` — same column (modular |
non_modular | unknown; NULL unless source_type is "source_code"), just a
clearer name that isn't source-code-specific in wording.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "source_ingestions", "sc_architecture_type", new_column_name="source_layout_type"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "source_ingestions", "source_layout_type", new_column_name="sc_architecture_type"
    )
