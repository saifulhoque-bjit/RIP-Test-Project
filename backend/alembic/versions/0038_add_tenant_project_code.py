"""add tenant and project codes

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-21

Adds a short, human-readable ``code`` to both ``tenants`` (an alpha slug
derived from the tenant name, e.g. "Acme Corp" -> "ACME") and ``projects``
(``{tenant.code}-{seq:04d}``, or ``PRJ-{seq:04d}`` for untenanted projects —
MVP single-tenant deployments have no tenant assigned). ``tenants.project_sequence``
backs the per-tenant project counter, incremented atomically on every project
creation (see ``TenantRepository.increment_project_sequence``).

Existing rows are backfilled in Python, not pure SQL: the tenant code needs
de-duplication against sibling tenant names, and the project code needs a
stable per-tenant ordinal assigned in creation order.
"""

from collections.abc import Sequence
import re

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


def _base_tenant_code(name: str) -> str:
    """Mirror app.utils.code_generator.generate_tenant_code_candidate.

    Duplicated here (not imported) since migrations must stay independent of
    application code that can change shape after this revision is applied.
    """
    words = [w for w in re.split(r"\s+", name.strip()) if _NON_ALNUM_RE.sub("", w)]
    if len(words) >= 2:
        base = "".join(_NON_ALNUM_RE.sub("", w)[:1] for w in words[:4]).upper()
    elif words:
        base = _NON_ALNUM_RE.sub("", words[0]).upper()[:6]
    else:
        base = ""
    if len(base) < 2:
        base = (base + "XX")[:2] if base else "TN"
    return base


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    op.add_column("tenants", sa.Column("code", sa.String(length=20), nullable=True))
    op.add_column(
        "tenants",
        sa.Column("project_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("projects", sa.Column("code", sa.String(length=30), nullable=True))
    op.execute("CREATE SEQUENCE IF NOT EXISTS project_untenanted_code_seq")

    # ── Backfill tenants.code, de-duplicated with a numeric suffix ─────────
    used_codes: set[str] = set()
    tenant_rows = bind.execute(text("SELECT id, name FROM tenants ORDER BY created_at")).fetchall()
    for tenant_id, name in tenant_rows:
        base = _base_tenant_code(name)
        candidate = base
        attempt = 2
        while candidate in used_codes:
            candidate = f"{base}{attempt}"
            attempt += 1
        used_codes.add(candidate)
        bind.execute(
            text("UPDATE tenants SET code = :code WHERE id = :id"),
            {"code": candidate, "id": tenant_id},
        )

    # ── Backfill projects.code, per-tenant sequence in creation order ──────
    tenant_seq: dict[object, int] = {}
    project_rows = bind.execute(
        text("SELECT id, tenant_id FROM projects ORDER BY tenant_id NULLS FIRST, created_at")
    ).fetchall()
    for project_id, tenant_id in project_rows:
        if tenant_id is None:
            seq = bind.execute(text("SELECT nextval('project_untenanted_code_seq')")).scalar_one()
            code = f"PRJ-{seq:04d}"
        else:
            seq = tenant_seq.get(tenant_id, 0) + 1
            tenant_seq[tenant_id] = seq
            bind.execute(
                text("UPDATE tenants SET project_sequence = :seq WHERE id = :id"),
                {"seq": seq, "id": tenant_id},
            )
            tenant_code = bind.execute(
                text("SELECT code FROM tenants WHERE id = :id"), {"id": tenant_id}
            ).scalar_one()
            code = f"{tenant_code}-{seq:04d}"
        bind.execute(
            text("UPDATE projects SET code = :code WHERE id = :id"),
            {"code": code, "id": project_id},
        )

    op.alter_column("tenants", "code", nullable=False)
    op.alter_column("projects", "code", nullable=False)
    op.create_index(op.f("ix_tenants_code"), "tenants", ["code"], unique=True)
    op.create_index(op.f("ix_projects_code"), "projects", ["code"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_projects_code"), table_name="projects")
    op.drop_index(op.f("ix_tenants_code"), table_name="tenants")
    op.drop_column("projects", "code")
    op.drop_column("tenants", "project_sequence")
    op.drop_column("tenants", "code")
    op.execute("DROP SEQUENCE IF EXISTS project_untenanted_code_seq")
