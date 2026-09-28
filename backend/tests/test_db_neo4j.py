"""Unit tests for app.db.neo4j (driver singleton + index/constraint setup)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app.db.neo4j as neo4j_module
from app.db.neo4j import (
    _build_candidate_uris,
    close_neo4j_driver,
    create_neo4j_indexes,
    get_neo4j_driver,
    reset_driver_after_fork,
)


@pytest.fixture(autouse=True)
def _clean_driver_singleton(monkeypatch):
    """Every test starts with no cached driver and leaves none behind."""
    monkeypatch.setattr(neo4j_module, "_driver", None)


class TestBuildCandidateUris:
    def test_docker_hostname_adds_localhost_fallback(self):
        result = _build_candidate_uris("bolt://neo4j:7687")
        assert result == ["bolt://neo4j:7687", "bolt://localhost:7687"]

    def test_localhost_adds_docker_fallback(self):
        result = _build_candidate_uris("bolt://localhost:7687")
        assert result == ["bolt://localhost:7687", "bolt://neo4j:7687"]

    def test_127_0_0_1_adds_docker_fallback(self):
        result = _build_candidate_uris("bolt://127.0.0.1:7687")
        assert "bolt://neo4j:7687" in result

    def test_other_hostname_has_no_fallback(self):
        result = _build_candidate_uris("bolt://prod-neo4j.internal:7687")
        assert result == ["bolt://prod-neo4j.internal:7687"]

    def test_deduplicates_candidates(self):
        result = _build_candidate_uris("bolt://neo4j:7687")
        assert len(result) == len(set(result))


class TestGetNeo4jDriver:
    def test_returns_cached_driver_without_reconnecting(self, monkeypatch):
        cached = MagicMock()
        monkeypatch.setattr(neo4j_module, "_driver", cached)

        with patch("app.db.neo4j.GraphDatabase") as mock_gdb:
            result = get_neo4j_driver()

        assert result is cached
        mock_gdb.driver.assert_not_called()

    def test_connects_on_first_configured_uri(self):
        driver = MagicMock()
        with (
            patch("app.db.neo4j.GraphDatabase") as mock_gdb,
            patch("app.db.neo4j.settings") as mock_settings,
        ):
            mock_settings.NEO4J_URI = "bolt://neo4j:7687"
            mock_settings.NEO4J_USER = "neo4j"
            mock_settings.NEO4J_PASSWORD = "pw"
            mock_gdb.driver.return_value = driver

            result = get_neo4j_driver()

        assert result is driver
        driver.verify_connectivity.assert_called_once()

    def test_falls_back_to_second_candidate_uri(self):
        bad_driver = MagicMock()
        bad_driver.verify_connectivity.side_effect = RuntimeError("unreachable")
        good_driver = MagicMock()

        with (
            patch("app.db.neo4j.GraphDatabase") as mock_gdb,
            patch("app.db.neo4j.settings") as mock_settings,
        ):
            mock_settings.NEO4J_URI = "bolt://neo4j:7687"
            mock_settings.NEO4J_USER = "neo4j"
            mock_settings.NEO4J_PASSWORD = "pw"
            mock_gdb.driver.side_effect = [bad_driver, good_driver]

            result = get_neo4j_driver()

        assert result is good_driver

    def test_raises_last_error_when_all_candidates_fail(self):
        bad_driver = MagicMock()
        bad_driver.verify_connectivity.side_effect = RuntimeError("unreachable")

        with (
            patch("app.db.neo4j.GraphDatabase") as mock_gdb,
            patch("app.db.neo4j.settings") as mock_settings,
        ):
            mock_settings.NEO4J_URI = "bolt://prod-only:7687"
            mock_settings.NEO4J_USER = "neo4j"
            mock_settings.NEO4J_PASSWORD = "pw"
            mock_gdb.driver.return_value = bad_driver

            with pytest.raises(RuntimeError, match="unreachable"):
                get_neo4j_driver()


class TestCreateNeo4jIndexes:
    def test_runs_all_statements_against_a_session(self):
        session = MagicMock()
        driver = MagicMock()
        driver.session.return_value.__enter__.return_value = session
        driver.session.return_value.__exit__.return_value = None

        with patch("app.db.neo4j.get_neo4j_driver", return_value=driver):
            create_neo4j_indexes()

        assert session.run.call_count > 0

    def test_swallows_per_statement_errors(self):
        session = MagicMock()
        session.run.side_effect = RuntimeError("statement failed")
        driver = MagicMock()
        driver.session.return_value.__enter__.return_value = session
        driver.session.return_value.__exit__.return_value = None

        with patch("app.db.neo4j.get_neo4j_driver", return_value=driver):
            create_neo4j_indexes()  # must not raise


class TestCloseNeo4jDriver:
    def test_closes_and_clears_driver(self, monkeypatch):
        driver = MagicMock()
        monkeypatch.setattr(neo4j_module, "_driver", driver)

        close_neo4j_driver()

        driver.close.assert_called_once()
        assert neo4j_module._driver is None

    def test_noop_when_no_driver(self):
        close_neo4j_driver()  # must not raise


class TestResetDriverAfterFork:
    def test_clears_driver_without_closing(self, monkeypatch):
        driver = MagicMock()
        monkeypatch.setattr(neo4j_module, "_driver", driver)

        reset_driver_after_fork()

        driver.close.assert_not_called()
        assert neo4j_module._driver is None
