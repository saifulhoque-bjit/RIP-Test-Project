"""drop tap_integrations.app_client_name

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-12

RIP always identifies itself to TAP as the app client ``RIP``. That is a
property of the product, not of a project, so collecting it per project was
wrong twice over: it let an operator save a value that would never be
correct, and it stored the same literal on every row.

The name now lives in ``app.core.constants.TAP_APP_CLIENT_NAME`` and is sent
straight to TAP's verification call, so the column has no remaining reader.

The downgrade backfills the constant rather than guessing, which is lossless:
every existing row already holds it (or a value that never worked).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_CLIENT_NAME = "RIP"


def upgrade() -> None:
    op.drop_column("tap_integrations", "app_client_name")


def downgrade() -> None:
    # Added with a server_default so existing rows satisfy NOT NULL, then
    # dropped so the column matches its original definition.
    op.add_column(
        "tap_integrations",
        sa.Column(
            "app_client_name",
            sa.String(128),
            nullable=False,
            server_default=_APP_CLIENT_NAME,
        ),
    )
    op.alter_column("tap_integrations", "app_client_name", server_default=None)
