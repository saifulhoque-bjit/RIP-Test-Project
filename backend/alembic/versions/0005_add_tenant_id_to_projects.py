"""add tenant_id to projects

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-15

Adds a nullable ``tenant_id`` FK to ``projects``, mirroring the existing
``users.tenant_id`` column. MVP single-tenant deployments leave this unset;
it is auto-derived from the project owner's tenant at creation time. Not
yet used to scope any queries — see the ``Tenant`` model docstring for the
planned future multi-tenant scoping work.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "projects",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "projects_tenant_id_fkey",
        "projects",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_projects_tenant_id", "projects", ["tenant_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_projects_tenant_id", table_name="projects")
    op.drop_constraint("projects_tenant_id_fkey", "projects", type_="foreignkey")
    op.drop_column("projects", "tenant_id")
