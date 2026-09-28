"""add run_code to source_ingestions

Revision ID: 0001
Revises: 0000
Create Date: 2026-07-09

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = "0000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Idempotent: this sequence normally already exists (created by
    # app/db/init_db.py at app startup), but migrations must not depend on
    # that having run first.
    op.execute("CREATE SEQUENCE IF NOT EXISTS project_run_code_seq START WITH 1001")

    op.add_column(
        "source_ingestions",
        sa.Column("run_code", sa.String(length=20), nullable=True),
    )
    op.execute(
        "UPDATE source_ingestions SET run_code = 'RUN-' || nextval('project_run_code_seq') "
        "WHERE run_code IS NULL"
    )
    op.alter_column("source_ingestions", "run_code", nullable=False)
    op.create_unique_constraint("uq_source_ingestions_run_code", "source_ingestions", ["run_code"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_source_ingestions_run_code", "source_ingestions", type_="unique")
    op.drop_column("source_ingestions", "run_code")
