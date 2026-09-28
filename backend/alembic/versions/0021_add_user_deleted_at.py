"""add deleted_at to users, make email/cognito_sub uniqueness partial

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-24

Adds ``users.deleted_at`` (soft-delete timestamp, same convention as
``projects.deleted_at``/``sources.deleted_at``) and replaces the two plain
unconditional unique indexes on ``email``/``cognito_sub`` with partial
unique indexes scoped to ``WHERE deleted_at IS NULL``.

This lets an admin remove a user (soft-delete, see
``UserService.remove_user``) and immediately reuse that same email/
cognito_sub for a brand-new invite or registration — a soft-deleted row no
longer counts toward the uniqueness check, while the row itself (and
everything referencing it via FK) is preserved.

Downgrade is lossy if any email/cognito_sub is currently duplicated across
a soft-deleted row and a live row (the exact scenario this migration
enables) — restoring the plain unique index will fail in that case; resolve
duplicates manually before downgrading.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_users_deleted_at"), "users", ["deleted_at"])

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_cognito_sub"), table_name="users")
    op.create_index(
        "ix_users_email_active",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_users_cognito_sub_active",
        "users",
        ["cognito_sub"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_users_cognito_sub_active", table_name="users")
    op.drop_index("ix_users_email_active", table_name="users")
    op.create_index(op.f("ix_users_cognito_sub"), "users", ["cognito_sub"], unique=True)
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.drop_index(op.f("ix_users_deleted_at"), table_name="users")
    op.drop_column("users", "deleted_at")
