"""Database table initialisation.

Environment behaviour
─────────────────────
default (all environments)
    ``create_all`` is skipped. Schema changes are managed via Alembic
    migrations (``alembic upgrade head``).

opt-in bootstrap mode
    Set ``DB_AUTO_CREATE=true`` to run ``Base.metadata.create_all`` for
    local-first bootstrap scenarios only. This is intentionally disabled by
    default to avoid migration drift.

In all environments the seed data (roles + permissions) is applied after the
schema check because it is fully idempotent.
"""

from __future__ import annotations

from app.core.config import settings
from app.db.base import Base
from app.db.session import engine
import app.models.postgres.fragment_embedding_model  # noqa: F401
import app.models.postgres.incremental_history_model  # noqa: F401

# ---------------------------------------------------------------------------
# Model imports — must happen before create_all so that every Table object is
# registered with Base.metadata.  The noqa markers suppress "imported but
# unused" warnings; these imports are intentional side-effects.
# ---------------------------------------------------------------------------
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
import app.models.postgres.tenant_model  # noqa: F401
import app.models.postgres.user_model  # noqa: F401
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_pgvector(conn) -> None:  # type: ignore[type-arg]
    """Create the pgvector extension if it is not already present.

    Must be called inside an open connection/transaction so the extension is
    visible to subsequent DDL statements in the same session.
    """
    from sqlalchemy import text  # local import — sqlalchemy is always present

    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    logger.info("pgvector extension ensured")


def _ensure_source_ingestion_run_code_seq(conn) -> None:  # type: ignore[type-arg]
    """Create the project_run_code_seq sequence if it is not already present.

    Backs ``source_ingestions.run_code``. Must be called inside an open
    connection/transaction. This is a failsafe for environments where
    Alembic migrations may not have applied
    ``0001_source_ingestion_run_code`` yet.
    """
    from sqlalchemy import text  # local import — sqlalchemy is always present

    conn.execute(text("CREATE SEQUENCE IF NOT EXISTS project_run_code_seq START WITH 1001"))
    logger.info("project_run_code_seq ensured")


def _ensure_project_untenanted_code_seq(conn) -> None:  # type: ignore[type-arg]
    """Create the project_untenanted_code_seq sequence if not already present.

    Backs the ``PRJ-####`` fallback for ``projects.code`` when a project has
    no ``tenant_id``. Must be called inside an open connection/transaction —
    a failsafe for environments where Alembic migrations may not have
    applied ``0038_add_tenant_project_code`` yet.
    """
    from sqlalchemy import text  # local import — sqlalchemy is always present

    conn.execute(text("CREATE SEQUENCE IF NOT EXISTS project_untenanted_code_seq"))
    logger.info("project_untenanted_code_seq ensured")


def _create_all_tables() -> None:
    """Run SQLAlchemy create_all inside a proper transaction block.

    Uses ``engine.begin()`` (SQLAlchemy 2.x style) instead of the deprecated
    ``create_all(bind=engine)`` form, which was removed in SQLAlchemy 2.0.

    ``checkfirst=True`` makes every CREATE TABLE a no-op when the table
    already exists, so this is safe to call on a live database.
    """
    with engine.begin() as conn:
        _ensure_pgvector(conn)
        _ensure_source_ingestion_run_code_seq(conn)
        _ensure_project_untenanted_code_seq(conn)
        Base.metadata.create_all(bind=conn, checkfirst=True)
    logger.info("create_all completed — all tables are present")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_db() -> None:
    """Initialise the database schema and seed reference data.

    By default this function does not create tables — schema management is
    owned by Alembic.  The caller (migrate_and_init.py) is responsible for
    running ``alembic upgrade head`` before invoking this function so that
    seed code always runs against the correct, up-to-date schema.

    Set ``DB_AUTO_CREATE=true`` only for local bootstrap scenarios where
    running Alembic is inconvenient (e.g. a fresh dev machine with no
    migration history).
    """
    if settings.DB_AUTO_CREATE:
        app_env = getattr(settings, "APP_ENV", "unknown")
        logger.info(
            "DB_AUTO_CREATE=true — running create_all bootstrap (env=%s).",
            app_env,
        )
        _create_all_tables()
    else:
        logger.info(
            "DB_AUTO_CREATE=false — skipping create_all. "
            "Schema is managed exclusively by Alembic migrations."
        )

    # Ensure critical sequences exist (failsafe for Alembic-managed schemas).
    with engine.begin() as conn:
        _ensure_source_ingestion_run_code_seq(conn)

    # Seed default roles and permissions — always runs, fully idempotent.
    # Imported here to break the potential circular import chain:
    #   init_db → seed_db → models → session → init_db
    from app.db.seed_db import seed_db  # noqa: PLC0415

    seed_db()
