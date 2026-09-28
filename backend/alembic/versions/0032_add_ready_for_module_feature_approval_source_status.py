"""add 'ready_for_module_feature_approval' value to source_processing_status_enum

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-17

Adds ``ready_for_module_feature_approval`` to the native Postgres enum
backing ``sources.status`` so a Source's status can match its
SourceIngestion's status exactly at the module/feature-generation-complete
checkpoint, instead of the two rows reporting different strings
(``ready_for_review`` on the Source vs ``ready_for_module_feature_approval``
on the SourceIngestion) for the same event. See
``app/workers/document_task_stages.py``'s ``generate_modules_and_features_task``.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside the same transaction that
    # later uses the new value, so it's run in its own autocommit block —
    # this migration only adds the value and never uses it.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE source_processing_status_enum "
            "ADD VALUE IF NOT EXISTS 'ready_for_module_feature_approval'"
        )


def downgrade() -> None:
    """No-op: Postgres cannot drop a value from an existing enum type.

    Reverting would require recreating the enum without this value and
    remapping every dependent column/row, which is unsafe to do
    unconditionally in a downgrade.
    """
    pass
