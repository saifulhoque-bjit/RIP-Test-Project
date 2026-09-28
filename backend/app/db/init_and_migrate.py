#!/usr/bin/env python
"""Run database migrations, then perform startup initialization.

This script:
1. Loads DATABASE_URL from app settings (built from POSTGRES_* env vars)
2. Waits for the database to be reachable (with retries)
3. Runs Alembic migrations (source of truth) — fails hard on error
4. Runs app-level DB init (optional create_all bootstrap + seed)
5. Returns exit code 0 on success, 1 on failure

Source of truth for the database URL
─────────────────────────────────────
DATABASE_URL is NOT read from the environment directly.  It is assembled by
``Settings.build_database_url()`` from the POSTGRES_* variables:

    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD

Set those five variables in your docker-compose environment block or .env file.
This script then passes the constructed URL to both the readiness probe and
Alembic so all three (probe / Alembic / app) always use the identical DSN.

Optional tuning variables
─────────────────────────
DB_WAIT_RETRIES   Number of connection attempts before giving up (default: 15).
DB_WAIT_INTERVAL  Seconds between attempts (default: 3).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from app.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_database_url() -> str:
    """Return the fully-constructed DATABASE_URL from app settings.

    The URL is built by Pydantic's ``build_database_url`` model-validator from
    POSTGRES_HOST / PORT / DB / USER / PASSWORD.  Reading it from ``settings``
    guarantees the same DSN (including URL-encoded credentials) is used by the
    readiness probe, the Alembic subprocess, and the application engine.

    Exits immediately with a descriptive error if any required POSTGRES_*
    variable is missing, so the problem is surfaced before we ever try to
    connect.
    """
    try:
        from app.core.config import settings  # noqa: PLC0415
    except Exception as exc:
        logger.error(
            "✗ Failed to load application settings: %s\n"
            "  Ensure all required environment variables are set:\n"
            "    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB,\n"
            "    POSTGRES_USER, POSTGRES_PASSWORD, SECRET_KEY",
            exc,  # noqa: PLC0415
        )
        sys.exit(1)

    url = settings.DATABASE_URL.strip()
    if not url:
        # Should not happen if Pydantic validators passed, but guard anyway.
        logger.error(
            "✗ settings.DATABASE_URL is empty after loading config.\n"
            "  Check that POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD,\n"
            "  POSTGRES_DB and POSTGRES_PORT are all present in your\n"
            "  docker-compose environment block or .env file."
        )
        sys.exit(1)

    # Log only the host/db portion — never expose credentials in logs.
    safe = url.split("@")[-1] if "@" in url else url
    logger.info("Settings loaded — database: %s", safe)
    return url


def _find_alembic() -> str:
    """Resolve the alembic binary from the active Python environment.

    Prefers the binary that lives alongside sys.executable so the same
    virtual-env / site-packages are used, avoiding version mismatches when
    multiple Python installations exist in the image.
    """
    candidate = os.path.join(os.path.dirname(sys.executable), "alembic")
    return candidate if os.path.exists(candidate) else "alembic"


# ---------------------------------------------------------------------------
# Step 1 — wait for DB
# ---------------------------------------------------------------------------


def wait_for_db(database_url: str) -> bool:
    """Poll the DB until a connection succeeds or retries are exhausted.

    Re-reads DB_WAIT_RETRIES / DB_WAIT_INTERVAL on each call so environment
    changes made after module import are respected.

    A minimal single-connection probe engine is created, used, and immediately
    disposed so the Alembic subprocess and the app engine each establish their
    own connections cleanly.
    """
    retries = int(os.getenv("DB_WAIT_RETRIES", "15"))
    interval = int(os.getenv("DB_WAIT_INTERVAL", "3"))

    logger.info(
        "Step 1: Waiting for database to be reachable (max %d attempts, %ds apart)…",
        retries,
        interval,
    )

    try:
        from sqlalchemy import create_engine, text  # noqa: PLC0415
    except ImportError:
        logger.warning(
            "SQLAlchemy is not importable — skipping readiness wait. "
            "Ensure dependencies are installed before running this script."
        )
        return True

    # Minimal engine for probe only — pool_size=1, no overflow.
    # connect_timeout caps how long each TCP handshake attempt blocks so the
    # total wait stays predictable: retries × max(interval, connect_timeout).
    probe_engine = create_engine(
        database_url,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={"connect_timeout": interval},
    )

    try:
        for attempt in range(1, retries + 1):
            try:
                with probe_engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                logger.info("✓ Database is reachable (attempt %d/%d)", attempt, retries)
                return True
            except Exception as exc:
                logger.info(
                    "  Not ready yet (attempt %d/%d): %s — retrying in %ds…",
                    attempt,
                    retries,
                    exc,
                    interval,
                )
                time.sleep(interval)
    finally:
        # Dispose unconditionally — do not let the probe pool survive into the
        # Alembic subprocess or the application process.
        probe_engine.dispose()

    logger.error("✗ Database did not become reachable after %d attempts.", retries)
    return False


# ---------------------------------------------------------------------------
# Step 2 — Alembic migrations
# ---------------------------------------------------------------------------


def run_migrations(database_url: str) -> bool:
    """Run ``alembic upgrade head`` and return True on success.

    The constructed DATABASE_URL is injected into the subprocess environment so
    Alembic's env.py (which should call ``config.set_main_option("sqlalchemy.url",
    os.environ["DATABASE_URL"])``) picks up the identical DSN used everywhere
    else in this process — no risk of alembic.ini pointing at a stale or
    different host.

    stdout/stderr are intentionally NOT captured so the full Alembic output
    streams directly to the container log and Jenkins console — this is the
    primary debug surface when a migration fails in CI/CD.
    """
    project_root = os.getcwd()
    alembic_cmd = _find_alembic()

    logger.info(
        "Step 2: Running Alembic migrations…  (binary=%s  cwd=%s)",
        alembic_cmd,
        project_root,
    )

    # Merge the current environment and override DATABASE_URL with the value
    # built by settings so alembic/env.py always receives the correct DSN.
    env = {**os.environ, "DATABASE_URL": database_url}

    result = subprocess.run(
        [alembic_cmd, "upgrade", "head"],
        cwd=project_root,  # ensures alembic.ini is discovered
        env=env,  # inject settings-built DATABASE_URL
        check=False,  # we inspect returncode ourselves below
        capture_output=False,  # stream Alembic output straight to logs
    )

    if result.returncode != 0:
        logger.error(
            "✗ Alembic exited with code %d — see output above for details.\n"
            "  Common causes:\n"
            "    • A revision file references a column/table that does not exist yet\n"
            "    • alembic/env.py is not reading DATABASE_URL from os.environ\n"
            "    • The migrations/ directory is not present in the Docker image\n"
            "    • alembic binary not found at: %s",
            result.returncode,
            alembic_cmd,
        )
        return False

    logger.info("✓ Alembic migrations completed successfully")
    return True


# ---------------------------------------------------------------------------
# Step 3 — app-level init (create_all bootstrap + seed)
# ---------------------------------------------------------------------------


def run_app_init() -> bool:
    """Run init_db() which handles optional create_all and idempotent seeding.

    Imported lazily so app models are only loaded after migrations have run
    and the schema is guaranteed to be up-to-date.
    """
    logger.info("Step 3: Running DB initialization and seed…")
    try:
        from app.db.init_db import init_db  # noqa: PLC0415 — intentional late import

        init_db()
        logger.info("✓ DB initialization and seed completed")
        return True
    except Exception:
        logger.exception("✗ DB initialization/seed failed")
        return False


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    """Orchestrate: load settings → wait → migrate → init."""

    # Load and validate the DSN from settings (exits on misconfiguration).
    database_url = _load_database_url()

    if not wait_for_db(database_url):
        return 1

    if not run_migrations(database_url):
        logger.error(
            "Deployment aborted: resolve the migration error above before starting the application."
        )
        return 1

    if not run_app_init():
        return 1

    logger.info("✓ All database initialisation steps completed — application may start")
    return 0


if __name__ == "__main__":
    sys.exit(main())
