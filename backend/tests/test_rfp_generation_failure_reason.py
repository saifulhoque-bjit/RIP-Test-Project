"""A failed RFP module/feature generation run must always carry a failure reason.

The Pipelines tab only offers "Error details" for a failed run whose
``SourceIngestion.errors`` is non-empty (frontend ``hasFailureReason``). An
exception with an empty message (``TimeoutError()``, Celery's
``SoftTimeLimitExceeded()``) used to be recorded as ``str(exc) == ""``, which
``_add_source_ingestion_error`` drops — leaving the run ``failed`` with no
reason at all.

Stubs: ``record_activity``/``_notify_module_feature_status``/``_mark_status``
(side-effect bookkeeping), ``_run_source_module_feature_phase`` (raises the
failure under test), and ``UnitOfWork`` at the DB boundary — the real
``_update_source_ingestion_fields``/``_add_source_ingestion_error`` run and the
test asserts what they write.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from celery.exceptions import SoftTimeLimitExceeded
import pytest

from app.core.constants import SOURCE_STATUS_FAILED
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.workers.document_task_stages import _run_source_module_feature_generation_task

SOURCE_ID = "00000000-0000-0000-0000-000000000333"
INGESTION_ID = UUID("00000000-0000-0000-0000-0000000004a1")


def _fake_uow() -> MagicMock:
    uow = MagicMock()
    uow.__enter__.return_value = uow
    uow.__exit__.return_value = False
    uow.sources.get_many_by_uuids.return_value = [
        SimpleNamespace(source_ingestion_id=INGESTION_ID)
    ]
    return uow


@pytest.mark.parametrize(
    "exc", [TimeoutError(), SoftTimeLimitExceeded(), RuntimeError("phase failed hard")]
)
def test_terminal_generation_failure_records_non_empty_reason(exc: Exception) -> None:
    task_self = MagicMock()
    task_self.request.retries = 3
    task_self.max_retries = 3
    uow = _fake_uow()

    with (
        patch("app.workers.document_task_stages.record_activity"),
        patch("app.workers.document_task_stages._notify_module_feature_status"),
        patch("app.workers.document_task_stages._mark_status"),
        patch(
            "app.workers.document_task_stages._run_source_module_feature_phase",
            side_effect=exc,
        ),
        patch("app.db.unit_of_work.UnitOfWork", return_value=uow),
    ):
        result = _run_source_module_feature_generation_task(
            self=task_self,
            project_id="00000000-0000-0000-0000-0000000000d3",
            source_ids=[SOURCE_ID],
            task_db_id="task-r3",
        )

    assert result["status"] == SOURCE_STATUS_FAILED
    uow.source_ingestions.update_fields.assert_any_call(
        INGESTION_ID, status=SourceIngestionStatus.FAILED.value
    )
    recorded = [c.args for c in uow.source_ingestions.add_error.call_args_list]
    assert len(recorded) == 1, "failed run recorded no error reason"
    ingestion_id, error = recorded[0]
    assert ingestion_id == INGESTION_ID
    assert error.strip(), "failed run recorded an empty error reason"
    assert result["error"] == error
