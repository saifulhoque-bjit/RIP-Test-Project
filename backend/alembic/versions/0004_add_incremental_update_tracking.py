"""add pipeline run timestamps and change counts to source_ingestions

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-14

Adds generic pipeline-run timestamps to SourceIngestion, mirroring the
existing mod_fea_gen_*/user_story_gen_* pattern but not tied to one stage —
populated by the incremental-update pipeline, feedback-driven regeneration,
or any future pipeline that (re)runs against an existing ingestion:

    started_at   — pipeline run start timestamp
    completed_at — pipeline run completion timestamp

Also adds per-level updated/deleted change counts. tot_modules/tot_features/
tot_user_stories already exist and cover "added" counts; these six columns
cover the other two categories, populated by the same set of pipelines:

    tot_modules_updated / tot_features_updated / tot_user_stories_updated
    tot_modules_deleted / tot_features_deleted / tot_user_stories_deleted
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "source_ingestions",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "source_ingestions",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column_name in (
        "tot_modules_updated",
        "tot_features_updated",
        "tot_user_stories_updated",
        "tot_modules_deleted",
        "tot_features_deleted",
        "tot_user_stories_deleted",
    ):
        op.add_column(
            "source_ingestions",
            sa.Column(column_name, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    """Downgrade schema."""
    for column_name in (
        "tot_user_stories_deleted",
        "tot_features_deleted",
        "tot_modules_deleted",
        "tot_user_stories_updated",
        "tot_features_updated",
        "tot_modules_updated",
    ):
        op.drop_column("source_ingestions", column_name)
    op.drop_column("source_ingestions", "completed_at")
    op.drop_column("source_ingestions", "started_at")
