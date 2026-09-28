"""add tenant_llm_providers table

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-21

Adds ``tenant_llm_providers`` — the set of LLM providers (anthropic |
deepseek | openai | google) enabled for a tenant. Existence of a row means
the provider is enabled; there is no separate is_enabled flag.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tenant_llm_providers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_tenant_llm_provider"),
    )
    op.create_index(
        op.f("ix_tenant_llm_providers_tenant_id"),
        "tenant_llm_providers",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_tenant_llm_providers_tenant_id"), table_name="tenant_llm_providers")
    op.drop_table("tenant_llm_providers")
