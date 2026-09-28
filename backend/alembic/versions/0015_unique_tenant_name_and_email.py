"""unique tenant name and contact_email

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-22

Adds unique indexes on ``tenants.name`` and ``tenants.contact_email`` so two
tenants can no longer be created with the same name or contact email.
``contact_email`` stays nullable — Postgres unique indexes allow any number
of NULLs, so legacy rows without one are unaffected.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(op.f("ix_tenants_name"), "tenants", ["name"], unique=True)
    op.create_index(op.f("ix_tenants_contact_email"), "tenants", ["contact_email"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_tenants_contact_email"), table_name="tenants")
    op.drop_index(op.f("ix_tenants_name"), table_name="tenants")
