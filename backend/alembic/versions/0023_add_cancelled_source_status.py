"""add 'cancelled' value to source_processing_status_enum

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-29

Adds ``cancelled`` to the native Postgres enum backing ``sources.status``
so a cancelled Module/Feature generation or Incremental Update run can stamp
its Source rows accordingly (see ``app/workers/_task_helpers.py``'s
``mark_sources_and_ingestion_cancelled``). ``source_ingestions.status`` is a
plain ``String`` column (not a native enum), so no migration is needed there
— only the Python-level ``SourceIngestionStatus`` enum gained the value.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside the same transaction that
    # later uses the new value, so it's run in its own autocommit block —
    # this migration only adds the value and never uses it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE source_processing_status_enum ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    """No-op: Postgres cannot drop a value from an existing enum type.

    Reverting would require recreating the enum without 'cancelled' and
    remapping every dependent column/row, which is unsafe to do
    unconditionally in a downgrade.
    """
    pass
