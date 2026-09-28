"""Unit tests for the task-cancellation routes in app.routes.v1.project_tasks."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
import uuid

from app.routes.v1.project_tasks import (
    cancel_project_tasks,
    cancel_task,
    cancel_task_request,
    list_project_tasks,
)


def test_cancel_project_tasks_route_delegates_to_service() -> None:
    project_id = uuid.uuid4()
    service = MagicMock()
    service.cancel_project.return_value = {"request_ids": [], "cancelled_count": 0}

    result = cancel_project_tasks(project_id, _current_user=None, service=service)

    service.cancel_project.assert_called_once_with(project_id)
    assert result.success is True
    assert result.data["cancelled_count"] == 0


def test_cancel_task_route_delegates_to_service() -> None:
    task_id = uuid.uuid4()
    service = MagicMock()
    service.cancel_task.return_value = {"request_id": str(task_id), "cancelled_count": 1}

    result = cancel_task(task_id, _current_user=None, service=service)

    service.cancel_task.assert_called_once_with(task_id)
    assert result.success is True
    assert result.data["cancelled_count"] == 1


def test_cancel_task_request_route_delegates_to_service() -> None:
    request_id = uuid.uuid4()
    service = MagicMock()
    service.cancel_request.return_value = {"request_id": str(request_id), "cancelled_count": 2}

    result = cancel_task_request(request_id, _current_user=None, service=service)

    service.cancel_request.assert_called_once_with(request_id)
    assert result.success is True
    assert result.data["cancelled_count"] == 2


def test_task_routes_are_sync_so_fastapi_runs_them_in_the_threadpool() -> None:
    """Regression guard: these handlers must never become ``async def``.

    They await nothing and call fully blocking service code (Postgres, a
    Neo4j backlog delete, Redis, a Celery revoke broadcast). As ``async def``
    that work would run on the event loop and freeze the whole API — the
    WebSocket that delivers the ``cancelled`` event included — for the
    duration of the cancel. See the module docstring.
    """
    for handler in (
        list_project_tasks,
        cancel_project_tasks,
        cancel_task,
        cancel_task_request,
    ):
        assert not asyncio.iscoroutinefunction(handler), (
            f"{handler.__name__} must stay a plain def — see "
            "app/routes/v1/project_tasks.py module docstring"
        )
