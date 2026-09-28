"""Unit tests for app.workers.process_source_tasks dispatcher."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.core.constants import (
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_QUEUED,
    SOURCE_STATUS_READY_FOR_REVIEW,
)
from app.workers.process_source_tasks import process_source_task

_ID1 = "00000000-0000-0000-0000-000000000001"


def _uow_with_source(source: object | None) -> MagicMock:
    uow = MagicMock()
    uow.sources.get_many_by_uuids.return_value = [source] if source is not None else []
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None
    return cm


def _uow_with_source_map(source_map: dict[str, object]) -> MagicMock:
    uow = MagicMock()
    uow.sources.get_many_by_uuids.side_effect = lambda uuids: [
        source_map[str(uid)] for uid in uuids if str(uid) in source_map
    ]
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None
    return cm


def test_process_source_not_found() -> None:
    with patch("app.db.unit_of_work.UnitOfWork", return_value=_uow_with_source(None)):
        result = process_source_task.run("p1", _ID1)

    assert result["status"] == "not_found"


def test_process_source_already_completed() -> None:
    source = SimpleNamespace(id=UUID(_ID1), status=SOURCE_STATUS_READY_FOR_REVIEW)
    with patch("app.db.unit_of_work.UnitOfWork", return_value=_uow_with_source(source)):
        result = process_source_task.run("p1", _ID1)

    assert result["status"] == "already_terminal"


def test_process_source_missing_storage_key_marks_failed() -> None:
    source = SimpleNamespace(
        id=UUID(_ID1),
        status="uploaded",
        mime_type="application/pdf",
        storage_key=None,
        original_name="x.pdf",
        processing_error=None,
    )
    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=_uow_with_source(source)),
        patch("app.workers.process_source_tasks._mark_status") as mock_mark,
    ):
        result = process_source_task.run("p1", _ID1)

    assert result["status"] == "failed"
    mock_mark.assert_called_once()


def test_process_source_dispatch_document() -> None:
    source = SimpleNamespace(
        id=UUID(_ID1),
        status="uploaded",
        mime_type="application/pdf",
        storage_key="s3/key",
        original_name="x.pdf",
        processing_error=None,
    )
    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=_uow_with_source(source)),
        patch("app.workers.document_task.parse_document_task.apply_async") as mock_orchestrate,
    ):
        result = process_source_task.run("p1", _ID1)

    assert source.status == SOURCE_STATUS_QUEUED
    assert result["status"] == SOURCE_STATUS_QUEUED
    mock_orchestrate.assert_called_once_with(args=["p1", [_ID1], None, False, None])


def test_process_source_dispatch_document_forwards_skip_processing_true() -> None:
    source = SimpleNamespace(
        id=UUID(_ID1),
        status="uploaded",
        mime_type="application/pdf",
        storage_key="s3/key",
        original_name="x.pdf",
        processing_error=None,
    )
    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=_uow_with_source(source)),
        patch("app.workers.document_task.parse_document_task.apply_async") as mock_orchestrate,
    ):
        process_source_task.run("p1", _ID1, None, True)

    mock_orchestrate.assert_called_once_with(args=["p1", [_ID1], None, True, None])


def test_process_source_dispatch_image_and_code() -> None:
    image_source = SimpleNamespace(
        id=UUID(_ID1),
        status="uploaded",
        mime_type="image/png",
        storage_key="s3/img",
        original_name="x.png",
        processing_error=None,
    )
    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=_uow_with_source(image_source)),
        patch("app.workers.process_source_tasks._parse_image_task.apply_async") as mock_img,
    ):
        process_source_task.run("p1", _ID1)
    mock_img.assert_called_once_with(args=["p1", _ID1, None, None])

    code_source = SimpleNamespace(
        id=UUID(_ID1),
        status="uploaded",
        mime_type="application/zip",
        storage_key="s3/code",
        original_name="x.zip",
        processing_error=None,
    )
    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=_uow_with_source(code_source)),
        patch("app.workers.process_source_tasks._parse_code_task.apply_async") as mock_code,
    ):
        process_source_task.run("p1", _ID1)
    mock_code.assert_called_once_with(args=["p1", _ID1, None, False, None])


def test_process_source_dispatch_code_forwards_skip_processing_true() -> None:
    code_source = SimpleNamespace(
        id=UUID(_ID1),
        status="uploaded",
        mime_type="application/zip",
        storage_key="s3/code",
        original_name="x.zip",
        processing_error=None,
    )
    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=_uow_with_source(code_source)),
        patch("app.workers.process_source_tasks._parse_code_task.apply_async") as mock_code,
    ):
        process_source_task.run("p1", _ID1, None, True)

    mock_code.assert_called_once_with(args=["p1", _ID1, None, True, None])


def test_process_source_unsupported_mime_marks_failed() -> None:
    source = SimpleNamespace(
        id=UUID(_ID1),
        status="uploaded",
        mime_type="application/octet-stream",
        storage_key="s3/unk",
        original_name="x.bin",
        processing_error=None,
    )
    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=_uow_with_source(source)),
        patch("app.workers.process_source_tasks._mark_status") as mock_mark,
    ):
        result = process_source_task.run("p1", "00000000-0000-0000-0000-000000000001")

    assert result["status"] == "failed"
    mock_mark.assert_called_once()


def test_process_source_dispatch_exception_marks_all_failed_on_exhaustion() -> None:
    """When dispatch raises and all retries are exhausted, all dispatched sources are marked failed."""
    source = SimpleNamespace(
        id=UUID(_ID1),
        status="uploaded",
        mime_type="application/pdf",
        storage_key="s3/key",
        original_name="x.pdf",
        processing_error=None,
    )
    with (
        patch.object(process_source_task, "max_retries", 0),
        patch("app.db.unit_of_work.UnitOfWork", return_value=_uow_with_source(source)),
        patch(
            "app.workers.document_task.parse_document_task.apply_async",
            side_effect=RuntimeError("broker down"),
        ),
        patch("app.workers.process_source_tasks._mark_status") as mock_mark,
    ):
        result = process_source_task.run("p1", _ID1)

    assert result["status"] == SOURCE_STATUS_FAILED
    mock_mark.assert_called_once()


def test_process_source_batch_dispatch_by_mime() -> None:
    source_ids = [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000003",
        "00000000-0000-0000-0000-000000000004",
    ]
    source_map = {
        source_ids[0]: SimpleNamespace(
            id=UUID(source_ids[0]),
            status="uploaded",
            mime_type="application/pdf",
            storage_key="s3/doc-1",
            original_name="a.pdf",
            processing_error=None,
        ),
        source_ids[1]: SimpleNamespace(
            id=UUID(source_ids[1]),
            status="uploaded",
            mime_type="text/csv",
            storage_key="s3/doc-2",
            original_name="b.csv",
            processing_error=None,
        ),
        source_ids[2]: SimpleNamespace(
            id=UUID(source_ids[2]),
            status="uploaded",
            mime_type="image/png",
            storage_key="s3/img-1",
            original_name="c.png",
            processing_error=None,
        ),
        source_ids[3]: SimpleNamespace(
            id=UUID(source_ids[3]),
            status="uploaded",
            mime_type="application/zip",
            storage_key="s3/code-1",
            original_name="d.zip",
            processing_error=None,
        ),
    }

    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=_uow_with_source_map(source_map)),
        patch("app.workers.document_task.parse_document_task.apply_async") as mock_phase1,
        patch("app.workers.process_source_tasks._parse_image_task.apply_async") as mock_img,
        patch("app.workers.process_source_tasks._parse_code_task.apply_async") as mock_code,
    ):
        result = process_source_task.run("p1", source_ids)

    assert result["status"] == SOURCE_STATUS_QUEUED
    assert result["documents"] == source_ids[:2]
    assert result["images"] == [source_ids[2]]
    assert result["source_code"] == [source_ids[3]]
    mock_phase1.assert_called_once_with(args=["p1", source_ids[:2], None, False, None])
    mock_img.assert_called_once_with(args=["p1", source_ids[2], None, None])
    mock_code.assert_called_once_with(args=["p1", source_ids[3], None, False, None])
