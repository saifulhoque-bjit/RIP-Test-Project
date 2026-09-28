"""allow multiple roles per project member

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-23

Widens ``project_members``' primary key from ``(project_id, user_id)`` to
``(project_id, user_id, role)`` so a user can hold more than one role
(e.g. both "member" and "approver") on the same project — one row per
role granted instead of a single row overwritten in place.

Downgrade is lossy for any user who ended up with more than one role: it
keeps only the most-recently-assigned role per (project_id, user_id) before
restoring the narrower PK, since the old schema has no way to represent two
roles for the same pair.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("project_members_pkey", "project_members", type_="primary")
    op.create_primary_key(
        "project_members_pkey", "project_members", ["project_id", "user_id", "role"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
        DELETE FROM project_members a
        USING project_members b
        WHERE a.project_id = b.project_id
          AND a.user_id = b.user_id
          AND (a.assigned_at, a.role) < (b.assigned_at, b.role)
        """
    )
    op.drop_constraint("project_members_pkey", "project_members", type_="primary")
    op.create_primary_key("project_members_pkey", "project_members", ["project_id", "user_id"])
