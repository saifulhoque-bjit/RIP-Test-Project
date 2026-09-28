"""Unit tests for ProjectTaskService's cancellation methods."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import uuid

import pytest

from app.core.constants import TASK_STATUS_CANCELLED
from app.core.exceptions import NotFoundError
from app.services.project_task_service import ProjectTaskService


def _make_task(
    *,
    task_id=None,
    project_id=None,
    request_id=None,
    task_type="source_process",
    celery_task_id=None,
    progress=10,
    meta=None,
) -> MagicMock:
    return MagicMock(
        id=task_id or uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        request_id=request_id or uuid.uuid4(),
        task_type=task_type,
        celery_task_id=celery_task_id,
        progress=progress,
        meta=meta if meta is not None else {},
    )


def _source(source_type: str) -> MagicMock:
    return MagicMock(source_type=source_type)


def _uow_cm(uow: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None
    return cm


def test_cancel_task_raises_not_found_for_unknown_task() -> None:
    uow = MagicMock()
    uow.project_tasks.get_by_id.return_value = None

    with patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)):
        with pytest.raises(NotFoundError):
            ProjectTaskService().cancel_task(uuid.uuid4())


def test_cancel_task_resolves_request_id_and_delegates_to_cancel_request() -> None:
    request_id = uuid.uuid4()
    task = _make_task(request_id=request_id)
    uow = MagicMock()
    uow.project_tasks.get_by_id.return_value = task

    with (
        patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)),
        patch.object(
            ProjectTaskService,
            "cancel_request",
            return_value={"request_id": str(request_id), "cancelled_count": 1},
        ) as mock_cancel_request,
    ):
        result = ProjectTaskService().cancel_task(task.id)

    mock_cancel_request.assert_called_once_with(request_id)
    assert result["cancelled_count"] == 1


def test_cancel_request_marks_flag_revokes_queued_task_and_cancels_immediately() -> None:
    """Cancellation is applied in this call — not deferred to the worker."""
    request_id = uuid.uuid4()
    task = _make_task(request_id=request_id, celery_task_id="celery-1")

    uow = MagicMock()
    uow.project_tasks.list_active_by_request_id.return_value = [task]

    mock_celery_app = MagicMock()

    with (
        patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)),
        patch("app.core.task_control.mark_request_cancelled") as mock_mark,
        patch("app.core.celery_app.celery_app", mock_celery_app),
        patch("app.workers._task_helpers.emit_task_event") as mock_emit,
    ):
        result = ProjectTaskService().cancel_request(request_id)

    mock_mark.assert_called_once_with(request_id)
    mock_celery_app.control.revoke.assert_called_once_with("celery-1")
    uow.commit.assert_called_once()

    # Straight to the terminal status — there is no intermediate "cancelling".
    assert mock_emit.call_args.kwargs["status"] == TASK_STATUS_CANCELLED
    assert mock_emit.call_args.kwargs["task_db_id"] == str(task.id)
    assert result == {"request_id": str(request_id), "cancelled_count": 1}


def test_cancel_request_rolls_back_a_source_code_run() -> None:
    """A source-code run's generated backlog is rolled back in the cancel call."""
    request_id = uuid.uuid4()
    source_id = str(uuid.uuid4())
    task = _make_task(
        request_id=request_id,
        task_type="source_process",
        meta={"source_ids": [source_id]},
    )

    uow = MagicMock()
    uow.project_tasks.list_active_by_request_id.return_value = [task]
    uow.sources.get_by_uuid.return_value = _source("source_code")

    with (
        patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)),
        patch("app.core.task_control.mark_request_cancelled"),
        patch("app.core.celery_app.celery_app", MagicMock()),
        patch(
            "app.workers.source_code_task._finalize_source_code_cancellation"
        ) as mock_finalize,
        patch("app.workers.source_code_task._delete_source_code_backlog") as mock_delete,
    ):
        ProjectTaskService().cancel_request(request_id)

    mock_finalize.assert_called_once()
    kwargs = mock_finalize.call_args.kwargs
    assert kwargs["source_ids"] == [source_id]
    assert kwargs["task_db_id"] == str(task.id)
    assert kwargs["project_id"] == str(task.project_id)
    # The slow whole-project Neo4j delete must never run in the request —
    # _finalize_source_code_cancellation defers it to a Celery task.
    mock_delete.assert_not_called()


def test_cancel_request_does_no_neo4j_work_for_a_multi_source_request() -> None:
    """Every task is stamped cancelled without any inline backlog delete.

    A bulk upload of several source-code sources shares one request_id. The
    delete wipes the whole project's backlog, so running it inline per task
    repeated the slowest step of the rollback once per source — inside the
    request the user is waiting on. Regression guard for cancel latency.
    """
    request_id = uuid.uuid4()
    project_id = uuid.uuid4()
    source_a, source_b = str(uuid.uuid4()), str(uuid.uuid4())
    tasks = [
        _make_task(
            project_id=project_id,
            request_id=request_id,
            task_type="source_process",
            meta={"source_ids": [source_a]},
        ),
        _make_task(
            project_id=project_id,
            request_id=request_id,
            task_type="source_process",
            meta={"source_ids": [source_b]},
        ),
    ]

    uow = MagicMock()
    uow.project_tasks.list_active_by_request_id.return_value = tasks
    uow.sources.get_by_uuid.return_value = _source("source_code")

    with (
        patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)),
        patch("app.core.task_control.mark_request_cancelled"),
        patch("app.core.celery_app.celery_app", MagicMock()),
        patch(
            "app.workers.source_code_task._finalize_source_code_cancellation"
        ) as mock_finalize,
        patch("app.workers.source_code_task._delete_source_code_backlog") as mock_delete,
    ):
        result = ProjectTaskService().cancel_request(request_id)

    mock_delete.assert_not_called()
    # Every task is still stamped cancelled individually.
    assert mock_finalize.call_count == 2
    assert {c.kwargs["source_ids"][0] for c in mock_finalize.call_args_list} == {
        source_a,
        source_b,
    }
    assert result["cancelled_count"] == 2


def test_cancel_request_does_not_roll_back_a_non_source_code_run() -> None:
    """RFP ingestion has no backlog rollback — but its sources are still stamped.

    Marking only the task cancelled would leave the SourceIngestion row on
    "running", and that is what the Pipelines table renders: the run would
    read as alive forever while the task is terminal (and so no longer
    cancellable).
    """
    request_id = uuid.uuid4()
    source_id = str(uuid.uuid4())
    task = _make_task(
        request_id=request_id,
        task_type="source_process",
        meta={"source_ids": [source_id]},
    )

    uow = MagicMock()
    uow.project_tasks.list_active_by_request_id.return_value = [task]
    uow.sources.get_by_uuid.return_value = _source("rfp")

    with (
        patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)),
        patch("app.core.task_control.mark_request_cancelled"),
        patch("app.core.celery_app.celery_app", MagicMock()),
        patch(
            "app.workers.source_code_task._finalize_source_code_cancellation"
        ) as mock_finalize,
        patch("app.workers.source_code_task._delete_source_code_backlog") as mock_delete,
        patch("app.workers._task_helpers.emit_task_event") as mock_emit,
        patch("app.workers._task_helpers.mark_sources_and_ingestion_cancelled") as mock_mark,
    ):
        ProjectTaskService().cancel_request(request_id)

    mock_finalize.assert_not_called()  # no backlog to roll back
    mock_delete.assert_not_called()
    assert mock_emit.call_args.kwargs["status"] == TASK_STATUS_CANCELLED
    assert mock_mark.call_args.kwargs["source_ids"] == [source_id]


def test_cancel_request_marks_no_sources_for_a_task_that_owns_none() -> None:
    """Regeneration tasks own no Source rows — there is nothing to stamp."""
    request_id = uuid.uuid4()
    task = _make_task(request_id=request_id, task_type="story_regeneration", meta={})

    uow = MagicMock()
    uow.project_tasks.list_active_by_request_id.return_value = [task]

    with (
        patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)),
        patch("app.core.task_control.mark_request_cancelled"),
        patch("app.core.celery_app.celery_app", MagicMock()),
        patch("app.workers._task_helpers.emit_task_event") as mock_emit,
        patch("app.workers._task_helpers.mark_sources_and_ingestion_cancelled") as mock_mark,
    ):
        ProjectTaskService().cancel_request(request_id)

    assert mock_emit.call_args.kwargs["status"] == TASK_STATUS_CANCELLED
    mock_mark.assert_not_called()


def test_cancel_request_does_not_roll_back_a_non_source_process_task() -> None:
    """Only source_process runs own a backlog — regeneration tasks do not."""
    request_id = uuid.uuid4()
    task = _make_task(
        request_id=request_id,
        task_type="story_regeneration",
        meta={"source_ids": [str(uuid.uuid4())]},
    )

    uow = MagicMock()
    uow.project_tasks.list_active_by_request_id.return_value = [task]

    with (
        patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)),
        patch("app.core.task_control.mark_request_cancelled"),
        patch("app.core.celery_app.celery_app", MagicMock()),
        patch(
            "app.workers.source_code_task._finalize_source_code_cancellation"
        ) as mock_finalize,
        patch("app.workers._task_helpers.emit_task_event"),
    ):
        ProjectTaskService().cancel_request(request_id)

    mock_finalize.assert_not_called()
    uow.sources.get_by_uuid.assert_not_called()


def test_cancel_request_skips_revoke_when_task_never_dispatched() -> None:
    """A task still being created (no celery_task_id yet) has nothing to revoke."""
    request_id = uuid.uuid4()
    task = _make_task(request_id=request_id, celery_task_id=None)

    uow = MagicMock()
    uow.project_tasks.list_active_by_request_id.return_value = [task]
    mock_celery_app = MagicMock()

    with (
        patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)),
        patch("app.core.task_control.mark_request_cancelled"),
        patch("app.core.celery_app.celery_app", mock_celery_app),
    ):
        ProjectTaskService().cancel_request(request_id)

    mock_celery_app.control.revoke.assert_not_called()


def test_cancel_request_continues_when_revoke_raises() -> None:
    """A broker hiccup during revoke must not prevent the cancel landing."""
    request_id = uuid.uuid4()
    task = _make_task(request_id=request_id, celery_task_id="celery-1")

    uow = MagicMock()
    uow.project_tasks.list_active_by_request_id.return_value = [task]
    mock_celery_app = MagicMock()
    mock_celery_app.control.revoke.side_effect = RuntimeError("broker down")

    with (
        patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)),
        patch("app.core.task_control.mark_request_cancelled"),
        patch("app.core.celery_app.celery_app", mock_celery_app),
        patch("app.workers._task_helpers.emit_task_event") as mock_emit,
    ):
        result = ProjectTaskService().cancel_request(request_id)

    assert mock_emit.call_args.kwargs["status"] == TASK_STATUS_CANCELLED
    assert result["cancelled_count"] == 1


def test_cancel_request_empty_when_nothing_active() -> None:
    request_id = uuid.uuid4()
    uow = MagicMock()
    uow.project_tasks.list_active_by_request_id.return_value = []

    with (
        patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)),
        patch("app.core.task_control.mark_request_cancelled") as mock_mark,
    ):
        result = ProjectTaskService().cancel_request(request_id)

    mock_mark.assert_called_once_with(request_id)
    assert result == {"request_id": str(request_id), "cancelled_count": 0}


def test_cancel_project_cancels_every_distinct_request_once() -> None:
    project_id = uuid.uuid4()
    request_a = uuid.uuid4()
    request_b = uuid.uuid4()
    task_a = _make_task(project_id=project_id, request_id=request_a)
    task_b = _make_task(project_id=project_id, request_id=request_b)

    uow = MagicMock()
    uow.project_tasks.list_active_by_project.return_value = [task_a, task_b]

    with (
        patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)),
        patch.object(
            ProjectTaskService,
            "cancel_request",
            side_effect=lambda rid: {"request_id": str(rid), "cancelled_count": 1},
        ) as mock_cancel_request,
    ):
        result = ProjectTaskService().cancel_project(project_id)

    assert mock_cancel_request.call_count == 2
    assert result["cancelled_count"] == 2
    assert set(result["request_ids"]) == {str(request_a), str(request_b)}


def test_cancel_sibling_tasks_excludes_the_given_task() -> None:
    """The RFP fail-fast path excludes its own task (finalizing as FAILED,
    not CANCELLED) while still cancelling every other active sibling."""
    request_id = uuid.uuid4()
    project_id = uuid.uuid4()
    keep_task = _make_task(
        task_id=uuid.uuid4(), project_id=project_id, request_id=request_id, task_type="source_process"
    )
    sibling_task = _make_task(
        project_id=project_id, request_id=request_id, task_type="module_regeneration", celery_task_id="celery-2"
    )

    uow = MagicMock()
    uow.project_tasks.list_active_by_request_id.return_value = [keep_task, sibling_task]
    mock_celery_app = MagicMock()

    with (
        patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)),
        patch("app.core.task_control.mark_request_cancelled") as mock_mark,
        patch("app.core.celery_app.celery_app", mock_celery_app),
        patch("app.workers._task_helpers.emit_task_event") as mock_emit,
    ):
        result = ProjectTaskService().cancel_sibling_tasks(
            request_id, exclude_task_id=keep_task.id
        )

    mock_mark.assert_called_once_with(request_id)
    # Only the sibling's celery task is revoked/cancelled — never the excluded one.
    mock_celery_app.control.revoke.assert_called_once_with("celery-2")
    assert mock_emit.call_count == 1
    assert mock_emit.call_args.kwargs["task_db_id"] == str(sibling_task.id)
    assert result == {"request_id": str(request_id), "cancelled_count": 1}


def test_cancel_request_still_cancels_everything_with_no_exclusion() -> None:
    """cancel_request (now delegating to cancel_sibling_tasks) is unaffected."""
    request_id = uuid.uuid4()
    task = _make_task(request_id=request_id, celery_task_id="celery-1")

    uow = MagicMock()
    uow.project_tasks.list_active_by_request_id.return_value = [task]
    mock_celery_app = MagicMock()

    with (
        patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)),
        patch("app.core.task_control.mark_request_cancelled"),
        patch("app.core.celery_app.celery_app", mock_celery_app),
        patch("app.workers._task_helpers.emit_task_event") as mock_emit,
    ):
        result = ProjectTaskService().cancel_request(request_id)

    mock_celery_app.control.revoke.assert_called_once_with("celery-1")
    assert mock_emit.call_args.kwargs["task_db_id"] == str(task.id)
    assert result == {"request_id": str(request_id), "cancelled_count": 1}


def test_cancel_project_no_active_tasks_is_empty() -> None:
    project_id = uuid.uuid4()
    uow = MagicMock()
    uow.project_tasks.list_active_by_project.return_value = []

    with patch("app.services.project_task_service.UnitOfWork", return_value=_uow_cm(uow)):
        result = ProjectTaskService().cancel_project(project_id)

    assert result == {"request_ids": [], "cancelled_count": 0}
