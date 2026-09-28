"""Unit tests for app.core.task_control.

Note: the global `_no_celery_dispatch` fixture (tests/conftest.py) stubs out
`mark_request_cancelled`/`is_request_cancelled`/`clear_request_cancelled` on
the module so other tests never hit a real Redis connection. These tests
target the real implementations directly (imported here, before any fixture
runs, so the names below are unaffected by that later monkeypatching).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app.core.task_control as task_control
from app.core.task_control import (
    claim_task_execution,
    clear_request_cancelled,
    is_request_cancelled,
    mark_request_cancelled,
    register_delivery_within_limit,
)


@pytest.fixture(autouse=True)
def _reset_cached_client():
    """The module caches one Redis client across calls (see _get_client).

    Reset it before/after each test so a `create_sync_redis` patched in one
    test doesn't leak into the next via the cached client from a prior test.
    """
    task_control._client = None
    yield
    task_control._client = None


def _mock_redis_client() -> MagicMock:
    client = MagicMock()
    client.close = MagicMock()
    return client


def test_mark_request_cancelled_sets_flag_with_ttl() -> None:
    client = _mock_redis_client()
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        mark_request_cancelled("req-1")

    args, kwargs = client.set.call_args
    assert args[0] == "taskctl:cancelled:req-1"
    assert args[1] == "1"
    assert kwargs["ex"] > 0


def test_client_is_reused_across_calls() -> None:
    """The Redis client is created once and reused, not reconnected per call."""
    client = _mock_redis_client()
    with patch("app.core.task_control.create_sync_redis", return_value=client) as create_mock:
        mark_request_cancelled("req-1")
        is_request_cancelled("req-1")
        clear_request_cancelled("req-1")

    create_mock.assert_called_once()


def test_is_request_cancelled_true_when_key_exists() -> None:
    client = _mock_redis_client()
    client.exists.return_value = 1
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        assert is_request_cancelled("req-1") is True
    client.exists.assert_called_once_with("taskctl:cancelled:req-1")


def test_is_request_cancelled_false_when_key_missing() -> None:
    client = _mock_redis_client()
    client.exists.return_value = 0
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        assert is_request_cancelled("req-1") is False


def test_is_request_cancelled_fails_open_on_redis_error() -> None:
    """A Redis outage must not itself abort in-flight work."""
    client = _mock_redis_client()
    client.exists.side_effect = RuntimeError("connection refused")
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        assert is_request_cancelled("req-1") is False


def test_mark_request_cancelled_swallows_redis_error() -> None:
    client = _mock_redis_client()
    client.set.side_effect = RuntimeError("connection refused")
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        mark_request_cancelled("req-1")  # must not raise


def test_clear_request_cancelled_deletes_key() -> None:
    client = _mock_redis_client()
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        clear_request_cancelled("req-1")
    client.delete.assert_called_once_with("taskctl:cancelled:req-1")


def test_clear_request_cancelled_swallows_redis_error() -> None:
    client = _mock_redis_client()
    client.delete.side_effect = RuntimeError("connection refused")
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        clear_request_cancelled("req-1")  # must not raise


def test_claim_task_execution_true_on_first_claim() -> None:
    client = _mock_redis_client()
    client.set.return_value = True
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        assert claim_task_execution("task-1") is True
    args, kwargs = client.set.call_args
    assert args[0] == "taskctl:claimed:task-1"
    assert args[1] == "1"
    assert kwargs["nx"] is True
    assert kwargs["ex"] > 0


def test_claim_task_execution_false_when_already_claimed() -> None:
    """Redis SET NX returns None/False when the key already exists — this is
    the redelivered-message case: a second invocation of the same task_id
    must not proceed."""
    client = _mock_redis_client()
    client.set.return_value = None
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        assert claim_task_execution("task-1") is False


def test_claim_task_execution_fails_open_on_redis_error() -> None:
    """A Redis outage must not itself block a legitimate run."""
    client = _mock_redis_client()
    client.set.side_effect = RuntimeError("connection refused")
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        assert claim_task_execution("task-1") is True


def test_register_delivery_within_limit_true_on_first_delivery() -> None:
    client = _mock_redis_client()
    client.incr.return_value = 1
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        assert register_delivery_within_limit("task-1", max_deliveries=2) is True
    client.incr.assert_called_once_with("taskctl:deliveries:task-1")
    client.expire.assert_called_once()


def test_register_delivery_within_limit_true_on_second_delivery() -> None:
    """The one allowed retry/redelivery still proceeds."""
    client = _mock_redis_client()
    client.incr.return_value = 2
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        assert register_delivery_within_limit("task-1", max_deliveries=2) is True
    # TTL is only (re-)set on the first delivery, not every subsequent one.
    client.expire.assert_not_called()


def test_register_delivery_within_limit_false_once_exceeded() -> None:
    """A 3rd delivery of the same task_id (e.g. a worker that keeps dying on
    the same module) is refused — the poison-loop guard."""
    client = _mock_redis_client()
    client.incr.return_value = 3
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        assert register_delivery_within_limit("task-1", max_deliveries=2) is False


def test_register_delivery_within_limit_fails_open_on_redis_error() -> None:
    """A Redis outage must not itself block a legitimate run."""
    client = _mock_redis_client()
    client.incr.side_effect = RuntimeError("connection refused")
    with patch("app.core.task_control.create_sync_redis", return_value=client):
        assert register_delivery_within_limit("task-1", max_deliveries=2) is True
