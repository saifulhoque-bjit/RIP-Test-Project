"""rename SourceCodeStage values persisted in source_ingestions.stages

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-19

``app.core.enums.source_code_stage.SourceCodeStage`` member values were
renamed to match new display labels: ``ingest_sources`` ->
``ingesting_sources``, ``code_dependency_graph`` -> ``code_dependency_analysis``,
``module_discovery`` -> ``discovering_modules``, ``requirement_extraction`` ->
``extracting_requirements`` (``ready_for_review`` is unchanged). ``stages`` is a
plain ``VARCHAR(40)[]`` column (not a native Postgres enum), so this is a data
backfill, not a schema change — rewrites any already-persisted old values in
place so existing rows keep reporting the correct stage.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RENAMES = [
    ("ingest_sources", "ingesting_sources"),
    ("code_dependency_graph", "code_dependency_analysis"),
    ("module_discovery", "discovering_modules"),
    ("requirement_extraction", "extracting_requirements"),
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
