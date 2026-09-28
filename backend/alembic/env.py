"""Alembic migration environment.

Database URL
────────────
The URL is sourced directly from the application settings
(``app.core.config.settings.DATABASE_URL``) so that the same ``.env`` file
drives both the FastAPI process and the Alembic CLI.  The ``sqlalchemy.url``
key in ``alembic.ini`` is intentionally left empty.

Autogenerate
────────────
All ORM model modules are imported here so that ``Base.metadata`` is fully
populated.  This enables ``alembic revision --autogenerate`` to detect new
tables, columns, and indexes automatically.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ── App imports ────────────────────────────────────────────────────────────
# Import settings and Base before any model so the metadata object is ready.
from app.core.config import settings
from app.db.base import Base

# Import every model module so SQLAlchemy registers the table definitions
# with Base.metadata.  Autogenerate will miss any table whose model is not
# imported here.
import app.models.postgres.activity_log_model  # noqa: F401
import app.models.postgres.fragment_embedding_model  # noqa: F401
import app.models.postgres.incremental_history_model  # noqa: F401
import app.models.postgres.jira_integration_model  # noqa: F401
import app.models.postgres.jira_sync_history_model  # noqa: F401
import app.models.postgres.jira_sync_mapping_model  # noqa: F401
import app.models.postgres.notification_model  # noqa: F401
import app.models.postgres.permission_model  # noqa: F401
import app.models.postgres.project_member_model  # noqa: F401
import app.models.postgres.project_model  # noqa: F401
import app.models.postgres.project_task_event_model  # noqa: F401
import app.models.postgres.project_task_model  # noqa: F401
import app.models.postgres.role_model  # noqa: F401
import app.models.postgres.source_ingestion_model  # noqa: F401
import app.models.postgres.source_model  # noqa: F401
import app.models.postgres.story_feedback_history_model  # noqa: F401
import app.models.postgres.tap_ack_history_model  # noqa: F401
import app.models.postgres.tap_sync_history_model  # noqa: F401
import app.models.postgres.tap_sync_mapping_model  # noqa: F401
import app.models.postgres.tenant_model  # noqa: F401
import app.models.postgres.user_model  # noqa: F401

# ── Alembic config ─────────────────────────────────────────────────────────
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url with the value built by the app settings so that
# URL construction (URL-encoding of special characters, port handling, etc.)
# stays in one place.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

target_metadata = Base.metadata


# ── Offline mode ───────────────────────────────────────────────────────────


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to the database.

    Useful for generating a migration script for review before applying it.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode ────────────────────────────────────────────────────────────


def run_migrations_online() -> None:
    """Run migrations against a live database connection.

    ``NullPool`` is used so that no connection is held between migration
    steps, which is important when running as a one-shot init container or
    pre-start hook.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
