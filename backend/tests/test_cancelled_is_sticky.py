"""A cancelled run can never be revived by its own orphaned worker.

Cancellation is applied synchronously by ``ProjectTaskService.cancel_request``
while the worker is very likely still mid-LLM-call — the source-code
pipeline's calls are blocking, non-streaming ``litellm.completion`` calls with
a 30-minute request timeout, so there is no way to interrupt one. That worker
keeps running for a while afterwards and keeps reporting progress.

Every one of those late writes funnels through the two guards tested here.
Without them the first of them flips the run back to ``running`` and the UI
shows a cancelled pipeline as alive again.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import uuid

from app.core.constants import (
    SOURCE_STATUS_CANCELLED,
    SOURCE_STATUS_RUNNING,
    TASK_STATUS_CANCELLED,
)
from app.repositories.postgres.project_task_repository import ProjectTaskRepository

# ── ProjectTask.status ──────────────────────────────────────────────────────


def _repo_with_task(task: MagicMock) -> ProjectTaskRepository:
    repo = ProjectTaskRepository(MagicMock())
    repo.get_by_id = MagicMock(return_value=task)  # type: ignore[method-assign]
    return repo


def test_update_status_ignores_a_late_write_to_a_cancelled_task() -> None:
    task = MagicMock(status=TASK_STATUS_CANCELLED, progress=40, stage="cancelled")
    repo = _repo_with_task(task)

    returned = repo.update_status(
        uuid.uuid4(), status=SOURCE_STATUS_RUNNING, progress=75, stage="module.processing"
    )

    assert returned is task
    assert task.status == TASK_STATUS_CANCELLED
    # The whole write is dropped, not just the status.
    assert task.progress == 40
    assert task.stage == "cancelled"


def test_update_status_still_applies_to_a_live_task() -> None:
    task = MagicMock(status=SOURCE_STATUS_RUNNING, progress=10, stage="queued")
    repo = _repo_with_task(task)

    repo.update_status(uuid.uuid4(), status=TASK_STATUS_CANCELLED, progress=100)

    assert task.status == TASK_STATUS_CANCELLED
    assert task.progress == 100


def test_update_status_allows_other_terminal_transitions() -> None:
    """Only `cancelled` is sticky — ready_for_review -> completed is legitimate."""
    task = MagicMock(status="ready_for_review", progress=100, stage="done")
    repo = _repo_with_task(task)

    repo.update_status(uuid.uuid4(), status="completed")

    assert task.status == "completed"


def test_update_status_returns_none_for_a_missing_task() -> None:
    repo = ProjectTaskRepository(MagicMock())
    repo.get_by_id = MagicMock(return_value=None)  # type: ignore[method-assign]

    assert repo.update_status(uuid.uuid4(), status=TASK_STATUS_CANCELLED) is None


# ── sources.status ──────────────────────────────────────────────────────────


def _patched_uow(source: MagicMock):
    uow = MagicMock()
    uow.sources.get_by_uuid.return_value = source
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None
    return uow, cm


def test_mark_status_does_not_un_cancel_a_source() -> None:
    from app.workers import _task_helpers

    source = MagicMock(status=SOURCE_STATUS_CANCELLED)
    uow, cm = _patched_uow(source)

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _task_helpers._mark_status(str(uuid.uuid4()), SOURCE_STATUS_RUNNING)

    assert source.status == SOURCE_STATUS_CANCELLED
    uow.commit.assert_not_called()


def test_mark_status_still_updates_a_live_source() -> None:
    from app.workers import _task_helpers

    source = MagicMock(status=SOURCE_STATUS_RUNNING)
    uow, cm = _patched_uow(source)

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _task_helpers._mark_status(str(uuid.uuid4()), SOURCE_STATUS_CANCELLED)

    assert source.status == SOURCE_STATUS_CANCELLED
    uow.commit.assert_called_once()
