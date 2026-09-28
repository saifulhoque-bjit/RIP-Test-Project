"""Unit tests for observability routes."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.exceptions import ServiceUnavailableError, ValidationError
from app.routes.v1.observability import (
    get_dead_letter_queue,
    get_queue_dashboard,
    get_queue_status,
    replay_dead_letter_task,
)
from app.schemas.observability_schema import DeadLetterReplayRequest
from app.utils.queue_monitoring import CeleryHealthStatus, QueueDashboardSummary, QueueStats


def test_get_queue_status_returns_health_payload() -> None:
    status = CeleryHealthStatus(
        broker_available=True,
        active_workers=2,
        total_concurrency=8,
        registered_tasks=11,
        queues=[QueueStats(name="routing", depth=3)],
        workers=[],
    )

    with patch("app.routes.v1.observability.get_celery_health_status", return_value=status):
        result = get_queue_status(_=None)

    assert result.success is True
    assert result.data is not None
    assert result.data.active_workers == 2
    assert result.data.queues[0].name == "routing"


def test_get_queue_dashboard_returns_summary_payload() -> None:
    summary = QueueDashboardSummary(
        broker_available=True,
        active_workers=2,
        total_concurrency=8,
        registered_tasks=11,
        total_queue_depth=5,
        stalled_queues=["notifications"],
        queues=[QueueStats(name="routing", depth=3)],
        workers=[],
    )

    with patch("app.routes.v1.observability.get_queue_dashboard_summary", return_value=summary):
        result = get_queue_dashboard(_=None)

    assert result.success is True
    assert result.data is not None
    assert result.data.total_queue_depth == 5
    assert result.data.stalled_queues == ["notifications"]


def test_get_dead_letter_queue_caps_max_items() -> None:
    with patch(
        "app.routes.v1.observability.get_dead_letter_tasks",
        return_value=[{"task_id": "abc", "status": "FAILURE"}],
    ) as mock_dlq:
        result = get_dead_letter_queue(max_items=9999, _=None)

    mock_dlq.assert_called_once_with(max_items=500)
    assert result.success is True
    assert result.data is not None
    assert len(result.data) == 1


def test_replay_dead_letter_task_returns_replay_details() -> None:
    payload = DeadLetterReplayRequest(
        task_name="tasks.project.sync_project_to_neo4j",
        args=["project-id", "name", "active"],
        kwargs={},
        queue="neo4j_sync",
    )

    with patch(
        "app.routes.v1.observability.replay_failed_task",
        return_value={
            "task_name": payload.task_name,
            "replay_task_id": "new-task-id",
            "queue": payload.queue,
        },
    ) as mock_replay:
        result = replay_dead_letter_task(payload=payload, _=None)

    mock_replay.assert_called_once_with(
        task_name=payload.task_name,
        args=payload.args,
        kwargs=payload.kwargs,
        queue=payload.queue,
    )
    assert result.success is True
    assert result.data is not None
    assert result.data.replay_task_id == "new-task-id"


def test_replay_dead_letter_task_maps_validation_error() -> None:
    payload = DeadLetterReplayRequest(task_name="tasks.unknown")

    with patch(
        "app.routes.v1.observability.replay_failed_task",
        side_effect=ValueError("task_name is not replayable"),
    ):
        with pytest.raises(ValidationError, match="not replayable"):
            replay_dead_letter_task(payload=payload, _=None)


def test_replay_dead_letter_task_maps_broker_failure() -> None:
    payload = DeadLetterReplayRequest(task_name="tasks.project.sync_project_to_neo4j")

    with patch(
        "app.routes.v1.observability.replay_failed_task",
        side_effect=RuntimeError("failed to publish replay task"),
    ):
        with pytest.raises(ServiceUnavailableError, match="failed to publish replay task"):
            replay_dead_letter_task(payload=payload, _=None)
