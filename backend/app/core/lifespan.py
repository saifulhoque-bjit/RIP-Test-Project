"""Application lifespan — startup and shutdown event hooks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.init_db import init_db
from app.db.neo4j import create_neo4j_indexes
from app.db.seed_super_admin import seed_super_admin
from app.services.setting_service import SettingService
from app.utils.logger import get_logger
from app.websockets.manager import manager as ws_manager
from app.websockets.notification_manager import notification_manager
from app.websockets.project_status_manager import project_status_manager

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Handle application startup and shutdown.

    Startup:
    - Initialise the relational DB (create tables on first run).
    - Seed the default Super Admin user + tenant (idempotent, MVP bootstrap).
    - Start the WebSocket Redis listener pool.

    Shutdown:
    - Gracefully drain and close the WebSocket connection pool.
    """
    from app.core.config import settings

    logger.info("Application startup — env=%s", settings.APP_ENV)
    init_db()
    try:
        create_neo4j_indexes()
        SettingService().seed_default_settings()
    except Exception as exc:  # pragma: no cover
        logger.warning("Neo4j setup skipped (Neo4j unreachable at startup): %s", exc)
    try:
        await seed_super_admin()
    except Exception as exc:  # pragma: no cover
        logger.warning("Super Admin seed skipped (error during bootstrap): %s", exc)
    await ws_manager.startup()
    await notification_manager.startup()
    await project_status_manager.startup()
    yield
    await project_status_manager.shutdown()
    await notification_manager.shutdown()
    await ws_manager.shutdown()
    logger.info("Application shutdown complete")
