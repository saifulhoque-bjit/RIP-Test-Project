"""drop project_runs and project_tasks.run_id

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-09

ProjectRun tracking has been superseded by SourceIngestion-level tracking
(``source_ingestions.run_code`` / ``stages``, added in 0001) and the
``/projects/me/pipelines`` endpoint. This drops the now-unused
``project_runs`` table and the ``project_tasks.run_id`` FK column that
pointed at it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # CASCADE drops the column's FK constraint and its index along with it,
    # without needing to know Postgres's auto-generated constraint name.
    op.execute("ALTER TABLE project_tasks DROP COLUMN run_id CASCADE")
    op.drop_table("project_runs")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "project_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("run_code", sa.String(length=20), nullable=False),
        sa.Column("stages", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_code"),
    )
    op.create_index(
        "ix_project_runs_project_id_status", "project_runs", ["project_id", "status"], unique=False
    )

    op.add_column("project_tasks", sa.Column("run_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "project_tasks_run_id_fkey",
        "project_tasks",
        "project_runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_project_tasks_run_id", "project_tasks", ["run_id"], unique=False)
