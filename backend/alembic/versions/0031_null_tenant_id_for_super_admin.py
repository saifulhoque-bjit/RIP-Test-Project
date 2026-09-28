"""null out tenant_id for super_admin users

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-14

Super Admin is a platform-wide role, not scoped to any tenant —
``app/db/seed_super_admin.py`` no longer assigns a tenant to the account it
bootstraps. This is a data-only migration clearing ``users.tenant_id`` for
any user who already holds the ``super_admin`` role, so existing
environments match the new invariant. No-op on a database where no
super_admin row has a tenant assigned.

Downgrade cannot restore the original tenant assignment (it isn't recorded
anywhere) — it is a no-op.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Clear tenant_id for every user holding the super_admin role."""
    op.execute(
        """
        UPDATE users
        SET tenant_id = NULL
        WHERE tenant_id IS NOT NULL
          AND id IN (
            SELECT ur.user_id
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE r.name = 'super_admin'
          )
        """
    )


def downgrade() -> None:
    """No-op — the original tenant assignment is not recoverable."""
