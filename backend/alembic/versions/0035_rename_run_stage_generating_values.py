"""rename RunStage generating values persisted in source_ingestions.stages

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-19

``app.core.enums.run_stage.RunStage`` member values were renamed:
``module_feature_generating`` -> ``generating_module_feature`` and
``user_story_generating`` -> ``generating_user_story`` (the ``*_READY_FOR_REVIEW``
values are unchanged). ``stages`` is a plain ``VARCHAR(40)[]`` column (not a
native Postgres enum), so this is a data backfill, not a schema change —
rewrites any already-persisted old values in place so existing rows keep
reporting the correct stage.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RENAMES = [
    ("module_feature_generating", "generating_module_feature"),
    ("user_story_generating", "generating_user_story"),
]


def upgrade() -> None:
    for old_value, new_value in _RENAMES:
        op.execute(
            f"UPDATE source_ingestions "
            f"SET stages = array_replace(stages, '{old_value}', '{new_value}') "
            f"WHERE '{old_value}' = ANY(stages)"
        )


def downgrade() -> None:
    for old_value, new_value in _RENAMES:
        op.execute(
            f"UPDATE source_ingestions "
            f"SET stages = array_replace(stages, '{new_value}', '{old_value}') "
            f"WHERE '{new_value}' = ANY(stages)"
        )
