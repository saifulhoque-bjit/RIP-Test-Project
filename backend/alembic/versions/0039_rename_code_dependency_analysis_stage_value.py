"""rename code_dependency_analysis stage value persisted in source_ingestions.stages

Revision ID: 0039
Revises: 0038
Create Date: 2026-08-24

``app.core.enums.source_ingestion_stage.SourceIngestionStage.CODE_DEPENDENCY_ANALYSIS``
was renamed to ``BUILDING_CODE_DEPENDENCY_GRAPH``: ``code_dependency_analysis`` ->
``building_code_dependency_graph``. ``stages`` is a plain ``VARCHAR(40)[]`` column
(not a native Postgres enum), so this is a data backfill, not a schema change —
rewrites any already-persisted old value in place so existing rows keep
reporting the correct stage. Mirrors the pattern in 0034.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0039"
down_revision: str | Sequence[str] | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VALUE = "code_dependency_analysis"
_NEW_VALUE = "building_code_dependency_graph"


def upgrade() -> None:
    op.execute(
        f"UPDATE source_ingestions "
        f"SET stages = array_replace(stages, '{_OLD_VALUE}', '{_NEW_VALUE}') "
        f"WHERE '{_OLD_VALUE}' = ANY(stages)"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE source_ingestions "
        f"SET stages = array_replace(stages, '{_NEW_VALUE}', '{_OLD_VALUE}') "
        f"WHERE '{_NEW_VALUE}' = ANY(stages)"
    )
