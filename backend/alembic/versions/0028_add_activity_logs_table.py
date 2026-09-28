"""add activity_logs table

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-12

Adds ``activity_logs`` — an append-only, project-scoped audit trail of
human-readable events (RFP/Source-Code pipeline generation & regeneration,
incremental change-set ingestion, and human accept/reject of proposed
changes). Purely additive.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "activity_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("activity_type", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_activity_logs_activity_type", "activity_logs", ["activity_type"])
    op.create_index("ix_activity_logs_created_at", "activity_logs", ["created_at"])
    op.create_index("ix_activity_logs_project_id", "activity_logs", ["project_id"])
    op.create_index(
        "ix_activity_logs_project_id_created_at",
        "activity_logs",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_activity_logs_project_id_created_at", table_name="activity_logs")
    op.drop_index("ix_activity_logs_project_id", table_name="activity_logs")
    op.drop_index("ix_activity_logs_created_at", table_name="activity_logs")
    op.drop_index("ix_activity_logs_activity_type", table_name="activity_logs")
    op.drop_table("activity_logs")
