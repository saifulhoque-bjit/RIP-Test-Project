"""Health-check and liveness endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.utils.logger import get_logger
from app.version import __release__, __sprint__, __version__

logger = get_logger(__name__)

health_router = APIRouter(tags=["Health"])


@health_router.get("/", include_in_schema=False)
def root() -> dict:
    """Minimal liveness probe — returns service identity and version."""
    return {
        "status": "ok",
        "service": "rip-backend",
        "version": __version__,
        "sprint": __sprint__,
        "release": __release__,
    }


@health_router.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    """Deep health check — probes Postgres, Redis, and Neo4j in parallel."""
    checks: dict[str, str] = {}

    async def _check_postgres() -> str:
        try:
            from sqlalchemy import text

            from app.db.session import SessionLocal

            def _probe() -> None:
                with SessionLocal() as session:
                    session.execute(text("SELECT 1"))

            await asyncio.wait_for(asyncio.to_thread(_probe), timeout=2.0)
            return "ok"
        except Exception:
            logger.warning("Health: postgres probe failed", exc_info=True)
            return "error"

    async def _check_redis() -> str:
        try:
            from app.core.redis_client import create_async_redis

            r = create_async_redis(settings.REDIS_URL, socket_connect_timeout=2.0)
            await asyncio.wait_for(r.ping(), timeout=2.0)
            await r.aclose()
            return "ok"
        except Exception:
            logger.warning("Health: redis probe failed", exc_info=True)
            return "error"

    async def _check_neo4j() -> str:
        try:
            from app.db.neo4j import get_neo4j_driver

            driver = get_neo4j_driver()
            await asyncio.wait_for(asyncio.to_thread(driver.verify_connectivity), timeout=2.0)
            return "ok"
        except Exception:
            logger.warning("Health: neo4j probe failed", exc_info=True)
            return "error"

    results = await asyncio.gather(
        _check_postgres(),
        _check_redis(),
        _check_neo4j(),
        return_exceptions=False,
    )
    checks["postgres"], checks["redis"], checks["neo4j"] = results
    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    status_code = 200 if overall == "ok" else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall,
            "version": __version__,
            "sprint": __sprint__,
            "release": __release__,
            "env": settings.APP_ENV,
            "checks": checks,
        },
    )
