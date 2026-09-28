"""add deleted_at to tenant_llm_providers, make (tenant_id, provider) uniqueness partial

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-20

Adds soft-delete support to ``tenant_llm_providers`` for the new
``DELETE /tenants/{tenant_id}/llm-providers/{provider}`` endpoint:
  - ``deleted_at``: set instead of hard-deleting the row, so a removed
    provider's stored key/verification history is preserved.
  - The table-level ``uq_tenant_llm_provider`` unique constraint on
    ``(tenant_id, provider)`` is replaced with a partial unique index scoped
    to ``WHERE deleted_at IS NULL`` — a soft-deleted row must not block the
    tenant from configuring that provider again (mirrors migration 0021's
    treatment of ``users.email``/``users.cognito_sub``).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "tenant_llm_providers",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_tenant_llm_providers_deleted_at"), "tenant_llm_providers", ["deleted_at"]
    )

    op.drop_constraint("uq_tenant_llm_provider", "tenant_llm_providers", type_="unique")
    op.create_index(
        "ix_tenant_llm_providers_tenant_provider_active",
        "tenant_llm_providers",
        ["tenant_id", "provider"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_tenant_llm_providers_tenant_provider_active", table_name="tenant_llm_providers"
    )
    op.create_unique_constraint(
        "uq_tenant_llm_provider", "tenant_llm_providers", ["tenant_id", "provider"]
    )

    op.drop_index(op.f("ix_tenant_llm_providers_deleted_at"), table_name="tenant_llm_providers")
    op.drop_column("tenant_llm_providers", "deleted_at")
