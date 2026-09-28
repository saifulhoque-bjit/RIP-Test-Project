"""widen source_ingestions.status and stages column lengths

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-13

Widens ``source_ingestions.status`` and the ``stages`` array element type
from VARCHAR(32) to VARCHAR(40) to fit the new
``ready_for_module_feature_approval`` value (33 chars) — the module/feature
generation completion status/stage marker, replacing ``ready_for_review``
for that specific transition so it's distinguishable from the user-story
generation completion (which still uses ``ready_for_review``).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE source_ingestions ALTER COLUMN status TYPE VARCHAR(40)")
    op.execute("ALTER TABLE source_ingestions ALTER COLUMN stages TYPE VARCHAR(40)[]")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE source_ingestions ALTER COLUMN status TYPE VARCHAR(32)")
    op.execute("ALTER TABLE source_ingestions ALTER COLUMN stages TYPE VARCHAR(32)[]")
