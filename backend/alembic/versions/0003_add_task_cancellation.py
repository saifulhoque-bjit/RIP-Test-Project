"""add request_id and parent_task_id to project_tasks for cancellation

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-13

Adds the columns needed to cancel a whole request (all tasks spawned by one
API call) or a whole subtree (a task and its descendants), not just a single
Celery task:

    request_id      — shared by every task/subtask spawned from one API call.
                       For ``sources/upload/bulk`` this is the pre-existing
                       ``batch_id`` minted in ``SourceService.upload_bulk``;
                       for the single-task endpoints it equals the task's own
                       ``id``. Backfilled to ``id`` for existing rows.
    parent_task_id   — the task that dispatched this one, if any. Lets a
                       cancellation of one task also resolve its descendants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "project_tasks",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "project_tasks",
        sa.Column("parent_task_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Existing rows predate request grouping — each is its own request.
    op.execute("UPDATE project_tasks SET request_id = id WHERE request_id IS NULL")
    op.alter_column("project_tasks", "request_id", nullable=False)

    op.create_foreign_key(
        "project_tasks_parent_task_id_fkey",
        "project_tasks",
        "project_tasks",
        ["parent_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_project_tasks_request_id", "project_tasks", ["request_id"], unique=False)
    op.create_index(
        "ix_project_tasks_parent_task_id", "project_tasks", ["parent_task_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_project_tasks_parent_task_id", table_name="project_tasks")
    op.drop_index("ix_project_tasks_request_id", table_name="project_tasks")
    op.drop_constraint("project_tasks_parent_task_id_fkey", "project_tasks", type_="foreignkey")
    op.drop_column("project_tasks", "parent_task_id")
    op.drop_column("project_tasks", "request_id")
