"""Unit tests for app.core.health."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.core.health import health, root


class TestRoot:
    def test_returns_service_identity(self):
        result = root()

        assert result["status"] == "ok"
        assert result["service"] == "rip-backend"
        assert "version" in result


class TestHealth:
    async def test_all_checks_ok_returns_200(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.return_value = None

        redis_client = AsyncMock()
        neo4j_driver = MagicMock()

        with (
            patch("app.db.session.SessionLocal", return_value=session),
            patch("app.core.redis_client.create_async_redis", return_value=redis_client),
            patch("app.db.neo4j.get_neo4j_driver", return_value=neo4j_driver),
        ):
            response = await health()

        assert response.status_code == 200

    async def test_postgres_failure_returns_503_degraded(self):
        redis_client = AsyncMock()
        neo4j_driver = MagicMock()

        with (
            patch("app.db.session.SessionLocal", side_effect=RuntimeError("db down")),
            patch("app.core.redis_client.create_async_redis", return_value=redis_client),
            patch("app.db.neo4j.get_neo4j_driver", return_value=neo4j_driver),
        ):
            response = await health()

        assert response.status_code == 503

    async def test_redis_failure_returns_503_degraded(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.return_value = None
        neo4j_driver = MagicMock()

        with (
            patch("app.db.session.SessionLocal", return_value=session),
            patch(
                "app.core.redis_client.create_async_redis", side_effect=RuntimeError("redis down")
            ),
            patch("app.db.neo4j.get_neo4j_driver", return_value=neo4j_driver),
        ):
            response = await health()

        assert response.status_code == 503

    async def test_neo4j_failure_returns_503_degraded(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.return_value = None
        redis_client = AsyncMock()

        with (
            patch("app.db.session.SessionLocal", return_value=session),
            patch("app.core.redis_client.create_async_redis", return_value=redis_client),
            patch("app.db.neo4j.get_neo4j_driver", side_effect=RuntimeError("neo4j down")),
        ):
            response = await health()

        assert response.status_code == 503
