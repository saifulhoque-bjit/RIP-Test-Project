"""FastAPI application entry point.

Wires together the application components defined in ``app/core/``:

- ``lifespan``   — DB init and WebSocket manager startup/shutdown
- ``middleware`` — CORS, security headers, correlation ID, rate limiting
- ``health``     — liveness (``/``) and deep health-check (``/health``) routes
"""

from __future__ import annotations

from fastapi import FastAPI

from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.health import health_router
from app.core.lifespan import lifespan
from app.core.messages import APP_TITLE
from app.core.middleware import register_middleware
from app.router import v1_router
from app.utils.openapi import build_custom_openapi
from app.version import __version__
from app.websockets.notification_ws import notification_ws_router
from app.websockets.project_status_ws import project_status_ws_router
from app.websockets.source_ws import ws_router


def create_app() -> FastAPI:
    application = FastAPI(
        title=APP_TITLE,
        version=__version__,
        docs_url="/docs" if settings.APP_ENV != "production" else None,
        redoc_url="/redoc" if settings.APP_ENV != "production" else None,
        openapi_url="/openapi.json" if settings.APP_ENV != "production" else None,
        debug=False,
        lifespan=lifespan,
    )

    register_middleware(application)
    register_exception_handlers(application)

    application.include_router(health_router)
    application.include_router(v1_router)
    # project_status_ws_router (/ws/projects/pipelines) must be included
    # before ws_router (/ws/projects/{project_id}): Starlette matches
    # WebSocket routes in registration order, not by specificity, and the
    # default path converter for {project_id} matches any single segment —
    # including the literal "pipelines". Registering the fixed-path route
    # first is the standard fix (mirrors FastAPI's own guidance for
    # fixed-path vs. variable-path siblings). Do not reorder these two.
    application.include_router(project_status_ws_router)
    application.include_router(ws_router)
    application.include_router(notification_ws_router)

    application.openapi = build_custom_openapi(application)  # type: ignore[method-assign]

    return application


app = create_app()
