"""SQLAlchemy engine and session factory."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

try:
    async_engine = create_async_engine(
        settings.DATABASE_ASYNC_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        echo=False,
    )
    AsyncSessionLocal = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
except ModuleNotFoundError:  # pragma: no cover
    async_engine = None
    AsyncSessionLocal = None


def dispose_engines_after_fork() -> None:
    """Discard connection pools inherited from the pre-fork parent process.

    ``engine``/``async_engine`` are created once at import time, which — under
    a Celery prefork worker — happens in the master process before any child
    is forked. Each forked child inherits the SAME underlying TCP sockets; two
    processes issuing queries over one shared socket corrupts both (observed
    as intermittent SSL/protocol errors). ``dispose(close=False)`` drops the
    inherited pool WITHOUT closing those sockets (closing here would tear down
    the parent's live connections too), so this child lazily opens its own
    fresh connections on first use. Call this from a ``worker_process_init``
    handler; it's a no-op cost-wise for non-forking pools (threads/solo) since
    nothing calls it there.
    """
    engine.dispose(close=False)
    if async_engine is not None:
        async_engine.sync_engine.dispose(close=False)
