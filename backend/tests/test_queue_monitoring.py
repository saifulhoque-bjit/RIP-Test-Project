"""Unit tests for queue monitoring utilities."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.utils.queue_monitoring import (
    QUEUE_NAMES,
    CeleryHealthStatus,
    QueueStats,
    WorkerInfo,
    _get_task_routes,
    _read_result_backend_record,
    _route_queue_for_task,
    get_celery_health_status,
    get_dead_letter_tasks,
    get_queue_dashboard_summary,
    get_queue_depths,
    get_worker_details,
    is_broker_available,
    replay_failed_task,
)


def test_replay_failed_task_uses_routed_queue_when_unspecified() -> None:
    routes = {"tasks.project.sync_project_to_neo4j": {"queue": "neo4j_sync"}}

    with (
        patch("app.utils.queue_monitoring._get_task_routes", return_value=routes),
        patch(
            "app.utils.queue_monitoring.celery_app.send_task",
            return_value=SimpleNamespace(id="task-123"),
        ) as mock_send,
    ):
        result = replay_failed_task(
            task_name="tasks.project.sync_project_to_neo4j", args=["p1"], kwargs={"x": 1}
        )

    mock_send.assert_called_once_with(
        "tasks.project.sync_project_to_neo4j",
        args=["p1"],
        kwargs={"x": 1},
        queue="neo4j_sync",
    )
    assert result["replay_task_id"] == "task-123"
    assert result["queue"] == "neo4j_sync"


def test_replay_failed_task_rejects_unknown_task() -> None:
    routes = {"tasks.process_source": {"queue": "routing"}}

    with patch("app.utils.queue_monitoring._get_task_routes", return_value=routes):
        with pytest.raises(ValueError, match="not replayable"):
            replay_failed_task(task_name="tasks.unknown")


def test_replay_failed_task_rejects_unknown_queue() -> None:
    routes = {"tasks.process_source": {"queue": "routing"}}

    with patch("app.utils.queue_monitoring._get_task_routes", return_value=routes):
        with pytest.raises(ValueError, match="not recognized"):
            replay_failed_task(task_name="tasks.process_source", queue="unknown_queue")


def test_replay_failed_task_wraps_publish_failure() -> None:
    routes = {"tasks.process_source": {"queue": "routing"}}

    with (
        patch("app.utils.queue_monitoring._get_task_routes", return_value=routes),
        patch(
            "app.utils.queue_monitoring.celery_app.send_task",
            side_effect=RuntimeError("broker unavailable"),
        ),
    ):
        with pytest.raises(RuntimeError, match="failed to publish replay task"):
            replay_failed_task(task_name="tasks.process_source")


class TestGetQueueDepths:
    def test_returns_depths_for_each_queue(self):
        redis_client = MagicMock()
        redis_client.pipeline.return_value.execute.return_value = [3, 5]

        with patch("app.core.redis_client.create_sync_redis", return_value=redis_client):
            result = get_queue_depths(["parsing", "routing"])

        assert result == {"parsing": 3, "routing": 5}
        redis_client.close.assert_called_once()

    def test_broker_failure_returns_minus_one_for_all(self):
        with patch("app.core.redis_client.create_sync_redis", side_effect=RuntimeError("down")):
            result = get_queue_depths(["parsing", "routing"])

        assert result == {"parsing": -1, "routing": -1}


class TestGetWorkerDetails:
    def test_none_inspector_returns_empty(self):
        assert get_worker_details(None) == []

    def test_inspect_failure_returns_empty(self):
        inspector = MagicMock()
        inspector.stats.side_effect = RuntimeError("timeout")

        assert get_worker_details(inspector) == []

    def test_returns_sorted_worker_info(self):
        inspector = MagicMock()
        inspector.stats.return_value = {
            "worker_b": {
                "pool": {"max-concurrency": 4, "implementation": "prefork"},
                "total": {"task.a": 2},
            },
            "worker_a": {"pool": {}, "total": {}},
        }
        inspector.active.return_value = {"worker_b": [1, 2]}
        inspector.active_queues.return_value = {
            "worker_b": [{"name": "parsing"}, {"name": "routing"}]
        }

        result = get_worker_details(inspector)

        assert [w.name for w in result] == ["worker_a", "worker_b"]
        worker_b = result[1]
        assert worker_b.queues == ["parsing", "routing"]
        assert worker_b.concurrency == 4
        assert worker_b.active_tasks == 2
        assert worker_b.processed_tasks == 2

    def test_none_stats_active_queues_default_to_empty(self):
        inspector = MagicMock()
        inspector.stats.return_value = None
        inspector.active.return_value = None
        inspector.active_queues.return_value = None

        assert get_worker_details(inspector) == []


class TestIsBrokerAvailable:
    def test_returns_true_when_connect_succeeds(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.__exit__.return_value = False

        with patch("app.utils.queue_monitoring.celery_app.connection", return_value=conn):
            assert is_broker_available() is True

    def test_returns_false_on_exception(self):
        with patch(
            "app.utils.queue_monitoring.celery_app.connection", side_effect=RuntimeError("down")
        ):
            assert is_broker_available() is False


class TestGetCeleryHealthStatus:
    def test_no_inspector_returns_minus_one_placeholders(self):
        with (
            patch("app.utils.queue_monitoring._safe_inspect", return_value=None),
            patch(
                "app.utils.queue_monitoring.get_queue_depths",
                return_value=dict.fromkeys(QUEUE_NAMES, -1),
            ),
            patch("app.utils.queue_monitoring.is_broker_available", return_value=False),
        ):
            status = get_celery_health_status()

        assert status.broker_available is False
        assert status.active_workers == -1
        assert status.total_concurrency == -1
        assert status.registered_tasks == -1
        assert status.workers == []

    def test_healthy_cluster_aggregates_worker_stats(self):
        inspector = MagicMock()
        inspector.registered.return_value = {"worker_a": ["tasks.a", "tasks.b"]}
        fake_worker = SimpleNamespace(name="worker_a", concurrency=4)

        with (
            patch("app.utils.queue_monitoring._safe_inspect", return_value=inspector),
            patch("app.utils.queue_monitoring.get_worker_details", return_value=[fake_worker]),
            patch(
                "app.utils.queue_monitoring.get_queue_depths",
                return_value=dict.fromkeys(QUEUE_NAMES, 0),
            ),
            patch("app.utils.queue_monitoring.is_broker_available", return_value=True),
        ):
            status = get_celery_health_status()

        assert status.broker_available is True
        assert status.active_workers == 1
        assert status.total_concurrency == 4
        assert status.registered_tasks == 2

    def test_registered_tasks_failure_defaults_to_minus_one(self):
        inspector = MagicMock()
        inspector.registered.side_effect = RuntimeError("timeout")

        with (
            patch("app.utils.queue_monitoring._safe_inspect", return_value=inspector),
            patch("app.utils.queue_monitoring.get_worker_details", return_value=[]),
            patch(
                "app.utils.queue_monitoring.get_queue_depths",
                return_value=dict.fromkeys(QUEUE_NAMES, 0),
            ),
            patch("app.utils.queue_monitoring.is_broker_available", return_value=True),
        ):
            status = get_celery_health_status()

        assert status.registered_tasks == -1


class TestGetQueueDashboardSummary:
    def test_flags_queues_with_depth_but_no_consumer(self):
        status = CeleryHealthStatus(
            broker_available=True,
            active_workers=1,
            total_concurrency=4,
            registered_tasks=2,
            queues=[
                QueueStats(name="routing", depth=3),
                QueueStats(name="notifications", depth=2),
                QueueStats(name="parsing", depth=0),
            ],
            workers=[
                WorkerInfo(
                    name="worker_a",
                    queues=["routing"],
                    concurrency=4,
                    pool_implementation="prefork",
                    active_tasks=0,
                    processed_tasks=0,
                )
            ],
        )

        with patch("app.utils.queue_monitoring.get_celery_health_status", return_value=status):
            summary = get_queue_dashboard_summary()

        assert summary.total_queue_depth == 5
        assert summary.stalled_queues == ["notifications"]
        assert summary.active_workers == 1
        assert summary.broker_available is True

    def test_excludes_negative_depths_from_total(self):
        status = CeleryHealthStatus(
            broker_available=False,
            active_workers=-1,
            total_concurrency=-1,
            registered_tasks=-1,
            queues=[QueueStats(name="routing", depth=-1), QueueStats(name="parsing", depth=2)],
            workers=[],
        )

        with patch("app.utils.queue_monitoring.get_celery_health_status", return_value=status):
            summary = get_queue_dashboard_summary()

        assert summary.total_queue_depth == 2
        assert summary.stalled_queues == ["parsing"]


class TestGetDeadLetterTasks:
    def test_returns_only_failed_records(self):
        redis_client = MagicMock()
        redis_client.scan_iter.return_value = ["celery-task-meta-1", "celery-task-meta-2"]

        def _record(_client, key):
            if key == "celery-task-meta-1":
                return {"status": "FAILURE", "name": "tasks.a", "traceback": "boom"}
            return {"status": "SUCCESS", "name": "tasks.b"}

        with (
            patch("app.core.redis_client.create_sync_redis", return_value=redis_client),
            patch("app.utils.queue_monitoring._read_result_backend_record", side_effect=_record),
        ):
            result = get_dead_letter_tasks()

        assert len(result) == 1
        assert result[0]["task_id"] == "1"
        assert result[0]["task_name"] == "tasks.a"

    def test_stops_at_max_items(self):
        redis_client = MagicMock()
        redis_client.scan_iter.return_value = [f"celery-task-meta-{i}" for i in range(5)]

        with (
            patch("app.core.redis_client.create_sync_redis", return_value=redis_client),
            patch(
                "app.utils.queue_monitoring._read_result_backend_record",
                return_value={"status": "FAILURE", "name": "tasks.a"},
            ),
        ):
            result = get_dead_letter_tasks(max_items=2)

        assert len(result) == 2

    def test_broker_failure_returns_empty_list(self):
        with patch("app.core.redis_client.create_sync_redis", side_effect=RuntimeError("down")):
            assert get_dead_letter_tasks() == []

    def test_malformed_record_is_skipped(self):
        redis_client = MagicMock()
        redis_client.scan_iter.return_value = ["celery-task-meta-1"]

        with (
            patch("app.core.redis_client.create_sync_redis", return_value=redis_client),
            patch(
                "app.utils.queue_monitoring._read_result_backend_record",
                side_effect=RuntimeError("bad record"),
            ),
        ):
            assert get_dead_letter_tasks() == []


class TestRouteQueueForTask:
    def test_returns_configured_queue(self):
        with patch(
            "app.utils.queue_monitoring._get_task_routes",
            return_value={"tasks.a": {"queue": "routing"}},
        ):
            assert _route_queue_for_task("tasks.a") == "routing"

    def test_returns_none_when_not_routed(self):
        with patch("app.utils.queue_monitoring._get_task_routes", return_value={}):
            assert _route_queue_for_task("tasks.unknown") is None

    def test_returns_none_when_route_is_not_a_dict(self):
        with patch(
            "app.utils.queue_monitoring._get_task_routes", return_value={"tasks.a": "not-a-dict"}
        ):
            assert _route_queue_for_task("tasks.a") is None


class TestGetTaskRoutes:
    def test_returns_configured_routes(self):
        fake_app = MagicMock()
        fake_app.conf.get.return_value = {"tasks.a": {"queue": "routing"}}
        with patch("app.utils.queue_monitoring.celery_app", fake_app):
            assert _get_task_routes() == {"tasks.a": {"queue": "routing"}}

    def test_returns_empty_dict_on_exception(self):
        fake_app = MagicMock()
        fake_app.conf.get.side_effect = RuntimeError("boom")
        with patch("app.utils.queue_monitoring.celery_app", fake_app):
            assert _get_task_routes() == {}

    def test_non_dict_config_returns_empty_dict(self):
        fake_app = MagicMock()
        fake_app.conf.get.return_value = "not-a-dict"
        with patch("app.utils.queue_monitoring.celery_app", fake_app):
            assert _get_task_routes() == {}


class TestReadResultBackendRecord:
    def test_hash_type_returns_hgetall(self):
        redis_client = MagicMock()
        redis_client.type.return_value = "hash"
        redis_client.hgetall.return_value = {"status": "FAILURE"}

        result = _read_result_backend_record(redis_client, "key-1")

        assert result == {"status": "FAILURE"}

    def test_string_type_parses_json(self):
        redis_client = MagicMock()
        redis_client.type.return_value = "string"
        redis_client.get.return_value = '{"status": "FAILURE"}'

        result = _read_result_backend_record(redis_client, "key-1")

        assert result == {"status": "FAILURE"}

    def test_string_type_empty_value_returns_empty_dict(self):
        redis_client = MagicMock()
        redis_client.type.return_value = "string"
        redis_client.get.return_value = None

        assert _read_result_backend_record(redis_client, "key-1") == {}

    def test_string_type_invalid_json_returns_empty_dict(self):
        redis_client = MagicMock()
        redis_client.type.return_value = "string"
        redis_client.get.return_value = "not json"

        assert _read_result_backend_record(redis_client, "key-1") == {}

    def test_string_type_non_dict_json_returns_empty_dict(self):
        redis_client = MagicMock()
        redis_client.type.return_value = "string"
        redis_client.get.return_value = "[1, 2, 3]"

        assert _read_result_backend_record(redis_client, "key-1") == {}

    def test_unknown_type_returns_empty_dict(self):
        redis_client = MagicMock()
        redis_client.type.return_value = "list"

        assert _read_result_backend_record(redis_client, "key-1") == {}
