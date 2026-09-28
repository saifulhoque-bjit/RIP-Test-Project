"""Tests for app wiring modules: main/router/middleware/lifespan."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import pytest
from slowapi.errors import RateLimitExceeded

from app.core.lifespan import lifespan
from app.core.middleware import register_middleware
from app.main import create_app
from app.router import v1_router


def test_router_prefix_and_routes_present() -> None:
    assert v1_router.prefix == "/api/v1"
    assert len(v1_router.routes) > 0


def test_create_app_includes_core_routes() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/" in paths
    assert "/health" in paths
    assert "/ws/projects/{project_id}" in paths
    assert any(path.startswith("/api/v1") for path in paths)


def test_register_middleware_sets_limiter_and_handlers() -> None:
    app = FastAPI()
    register_middleware(app)

    assert hasattr(app.state, "limiter")
    assert len(app.user_middleware) >= 3
    assert RateLimitExceeded in app.exception_handlers


@pytest.mark.asyncio
async def test_lifespan_startup_and_shutdown_calls() -> None:
    app = FastAPI()

    with (
        patch("app.core.lifespan.init_db") as mock_init_db,
        patch("app.core.lifespan.create_neo4j_indexes") as mock_indexes,
        patch("app.core.lifespan.SettingService") as mock_setting_service_cls,
        patch(
            "app.core.lifespan.ws_manager.startup", new=AsyncMock(return_value=None)
        ) as mock_ws_start,
        patch(
            "app.core.lifespan.ws_manager.shutdown",
            new=AsyncMock(return_value=None),
        ) as mock_ws_shutdown,
    ):
        mock_setting_service = mock_setting_service_cls.return_value
        async with lifespan(app):
            mock_ws_start.assert_awaited_once()

    mock_init_db.assert_called_once()
    mock_indexes.assert_called_once()
    mock_setting_service.seed_default_settings.assert_called_once()
    mock_ws_start.assert_awaited_once()
    mock_ws_shutdown.assert_awaited_once()
