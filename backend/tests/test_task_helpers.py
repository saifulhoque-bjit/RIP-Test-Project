"""Unit tests for app.workers._task_helpers."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import zipfile

import pytest

from app.core.constants import (
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_READY_FOR_REVIEW,
)
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.workers._task_helpers import (
    RETRYABLE_MODULE_INFRA,
    RETRYABLE_NEO4J_INFRA,
    _add_run_stage_by_id,
    _add_source_ingestion_error,
    _add_source_ingestion_error_by_id,
    _download_source_zip_from_s3,
    _extract_zip_to_codebase_folder,
    _get_source_ingestion_source_type,
    _handle_task_exception,
    _increment_retry,
    _increment_source_ingestion_module_counts,
    _mark_sources_status_by_project,
    _mark_status,
    _resolve_source_ingestion_id,
    _run_async,
    _update_source_ingestion_fields,
    _update_source_ingestion_fields_by_id,
    _upsert_neo4j_file_node,
    emit_task_event,
    is_retryable_boto,
    mark_cancelled_and_check,
    mark_sources_and_ingestion_cancelled,
)


@pytest.mark.asyncio
async def _sample_coro() -> str:
    return "ok"


def test_run_async_executes_coroutine() -> None:
    result = _run_async(_sample_coro())
    assert result == "ok"


def test_mark_status_updates_and_publishes_completed() -> None:
    source = SimpleNamespace(status="queued", processing_error=None)
    uow = MagicMock()
    uow.sources.get_by_uuid.return_value = source

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _mark_status("00000000-0000-0000-0000-000000000001", SOURCE_STATUS_READY_FOR_REVIEW)

    assert source.status == SOURCE_STATUS_READY_FOR_REVIEW
    uow.commit.assert_called_once()


def test_mark_status_sets_error_for_legacy_non_lifecycle_status() -> None:
    """A status outside SOURCE_PROCESSING_STATUS_VALUES (e.g. a legacy
    "partial" label) still gets normalized and its error recorded."""
    source = SimpleNamespace(status="parsing", processing_error=None)
    uow = MagicMock()
    uow.sources.get_by_uuid.return_value = source

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _mark_status("00000000-0000-0000-0000-000000000001", "partial", error="oops")

    assert source.processing_error == "oops"


def test_mark_status_not_found_is_noop() -> None:
    uow = MagicMock()
    uow.sources.get_by_uuid.return_value = None
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _mark_status("00000000-0000-0000-0000-000000000001", SOURCE_STATUS_FAILED, error="err")

    uow.commit.assert_not_called()


def test_increment_retry_updates_counter() -> None:
    source = SimpleNamespace(retry_count=None)
    uow = MagicMock()
    uow.sources.get_by_uuid.return_value = source
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _increment_retry("00000000-0000-0000-0000-000000000001")

    assert source.retry_count == 1
    uow.commit.assert_called_once()


# ── SourceIngestion field updates ───────────────────────────────────────────


def test_update_source_ingestion_fields_updates_every_distinct_ingestion() -> None:
    ingestion_id = uuid.uuid4()
    other_ingestion_id = uuid.uuid4()
    sources = [
        SimpleNamespace(source_ingestion_id=ingestion_id),
        SimpleNamespace(source_ingestion_id=other_ingestion_id),
        SimpleNamespace(source_ingestion_id=None),
    ]
    uow = MagicMock()
    uow.sources.get_many_by_uuids.return_value = sources

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _update_source_ingestion_fields(
            source_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
            fields={"tot_modules": 3, "tot_features": 7},
        )

    assert uow.source_ingestions.update_fields.call_count == 2
    called_ids = {c.args[0] for c in uow.source_ingestions.update_fields.call_args_list}
    assert called_ids == {ingestion_id, other_ingestion_id}
    for c in uow.source_ingestions.update_fields.call_args_list:
        assert c.kwargs == {"tot_modules": 3, "tot_features": 7}
    uow.commit.assert_called_once()


def test_update_source_ingestion_fields_noop_when_no_ingestion_found() -> None:
    uow = MagicMock()
    uow.sources.get_many_by_uuids.return_value = [SimpleNamespace(source_ingestion_id=None)]

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _update_source_ingestion_fields(source_ids=[str(uuid.uuid4())], fields={"tot_modules": 1})

    uow.source_ingestions.update_fields.assert_not_called()
    uow.commit.assert_not_called()


def test_update_source_ingestion_fields_noop_when_fields_empty() -> None:
    uow = MagicMock()

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _update_source_ingestion_fields(source_ids=[str(uuid.uuid4())], fields={})

    uow.sources.get_many_by_uuids.assert_not_called()


def test_update_source_ingestion_fields_swallows_exceptions() -> None:
    with patch("app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db down")):
        _update_source_ingestion_fields(
            source_ids=[str(uuid.uuid4())], fields={"tot_modules": 1}
        )  # must not raise


def test_increment_source_ingestion_module_counts_updates_every_distinct_ingestion() -> None:
    ingestion_id = uuid.uuid4()
    other_ingestion_id = uuid.uuid4()
    sources = [
        SimpleNamespace(source_ingestion_id=ingestion_id),
        SimpleNamespace(source_ingestion_id=other_ingestion_id),
        SimpleNamespace(source_ingestion_id=None),
    ]
    uow = MagicMock()
    uow.sources.get_many_by_uuids.return_value = sources

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _increment_source_ingestion_module_counts(
            source_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
            modules=1,
            features=5,
            user_stories=12,
        )

    assert uow.source_ingestions.increment_module_completion_counts.call_count == 2
    called_ids = {
        c.args[0] for c in uow.source_ingestions.increment_module_completion_counts.call_args_list
    }
    assert called_ids == {ingestion_id, other_ingestion_id}
    for c in uow.source_ingestions.increment_module_completion_counts.call_args_list:
        assert c.kwargs == {"modules": 1, "features": 5, "user_stories": 12, "modules_failed": 0}
    uow.commit.assert_called_once()


def test_increment_source_ingestion_module_counts_failed_module() -> None:
    ingestion_id = uuid.uuid4()
    uow = MagicMock()
    uow.sources.get_many_by_uuids.return_value = [
        SimpleNamespace(source_ingestion_id=ingestion_id)
    ]

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _increment_source_ingestion_module_counts(
            source_ids=[str(uuid.uuid4())], modules_failed=1
        )

    uow.source_ingestions.increment_module_completion_counts.assert_called_once_with(
        ingestion_id, modules=0, features=0, user_stories=0, modules_failed=1
    )
    uow.commit.assert_called_once()


def test_increment_source_ingestion_module_counts_noop_when_no_ingestion_found() -> None:
    uow = MagicMock()
    uow.sources.get_many_by_uuids.return_value = [SimpleNamespace(source_ingestion_id=None)]

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _increment_source_ingestion_module_counts(source_ids=[str(uuid.uuid4())], modules=1)

    uow.source_ingestions.increment_module_completion_counts.assert_not_called()
    uow.commit.assert_not_called()


def test_increment_source_ingestion_module_counts_noop_when_all_deltas_zero() -> None:
    uow = MagicMock()

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _increment_source_ingestion_module_counts(source_ids=[str(uuid.uuid4())])

    uow.sources.get_many_by_uuids.assert_not_called()


def test_increment_source_ingestion_module_counts_swallows_exceptions() -> None:
    with patch("app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db down")):
        _increment_source_ingestion_module_counts(
            source_ids=[str(uuid.uuid4())], modules=1
        )  # must not raise


def test_resolve_source_ingestion_id_returns_first_found() -> None:
    ingestion_id = uuid.uuid4()
    sources = [
        SimpleNamespace(source_ingestion_id=None),
        SimpleNamespace(source_ingestion_id=ingestion_id),
    ]
    uow = MagicMock()
    uow.sources.get_many_by_uuids.return_value = sources

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        result = _resolve_source_ingestion_id(source_ids=[str(uuid.uuid4()), str(uuid.uuid4())])

    assert result == str(ingestion_id)


def test_resolve_source_ingestion_id_returns_none_when_no_ingestion_found() -> None:
    uow = MagicMock()
    uow.sources.get_many_by_uuids.return_value = [SimpleNamespace(source_ingestion_id=None)]

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        result = _resolve_source_ingestion_id(source_ids=[str(uuid.uuid4())])

    assert result is None


def test_resolve_source_ingestion_id_swallows_exceptions() -> None:
    with patch("app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db down")):
        result = _resolve_source_ingestion_id(source_ids=[str(uuid.uuid4())])

    assert result is None


def test_add_source_ingestion_error_appends_to_every_distinct_ingestion() -> None:
    ingestion_id = uuid.uuid4()
    other_ingestion_id = uuid.uuid4()
    sources = [
        SimpleNamespace(source_ingestion_id=ingestion_id),
        SimpleNamespace(source_ingestion_id=other_ingestion_id),
        SimpleNamespace(source_ingestion_id=None),
    ]
    uow = MagicMock()
    uow.sources.get_many_by_uuids.return_value = sources

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _add_source_ingestion_error(source_ids=[str(uuid.uuid4()), str(uuid.uuid4())], error="boom")

    assert uow.source_ingestions.add_error.call_count == 2
    called_ids = {c.args[0] for c in uow.source_ingestions.add_error.call_args_list}
    assert called_ids == {ingestion_id, other_ingestion_id}
    for c in uow.source_ingestions.add_error.call_args_list:
        assert c.args[1] == "boom"
    uow.commit.assert_called_once()


def test_add_source_ingestion_error_noop_when_error_falsy() -> None:
    uow = MagicMock()

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _add_source_ingestion_error(source_ids=[str(uuid.uuid4())], error="")

    uow.sources.get_many_by_uuids.assert_not_called()


def test_add_source_ingestion_error_noop_when_no_ingestion_found() -> None:
    uow = MagicMock()
    uow.sources.get_many_by_uuids.return_value = [SimpleNamespace(source_ingestion_id=None)]

    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _add_source_ingestion_error(source_ids=[str(uuid.uuid4())], error="boom")

    uow.source_ingestions.add_error.assert_not_called()
    uow.commit.assert_not_called()


def test_add_source_ingestion_error_swallows_exceptions() -> None:
    with patch("app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db down")):
        _add_source_ingestion_error(source_ids=[str(uuid.uuid4())], error="boom")  # must not raise


class TestAddSourceIngestionErrorById:
    def test_noop_when_ingestion_id_none(self) -> None:
        with patch("app.db.unit_of_work.UnitOfWork") as mock_uow_cls:
            _add_source_ingestion_error_by_id(ingestion_id=None, error="boom")
        mock_uow_cls.assert_not_called()

    def test_noop_when_error_falsy(self) -> None:
        with patch("app.db.unit_of_work.UnitOfWork") as mock_uow_cls:
            _add_source_ingestion_error_by_id(ingestion_id="ing-1", error="")
        mock_uow_cls.assert_not_called()

    def test_appends_error(self) -> None:
        uow = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None
        ingestion_id = uuid.uuid4()

        with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
            _add_source_ingestion_error_by_id(ingestion_id=str(ingestion_id), error="boom")

        uow.source_ingestions.add_error.assert_called_once_with(ingestion_id, "boom")
        uow.commit.assert_called_once()

    def test_swallows_exceptions(self) -> None:
        with patch("app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db down")):
            _add_source_ingestion_error_by_id(ingestion_id="ing-1", error="boom")  # must not raise


class TestAddSourceIngestionStageById:
    def test_noop_when_ingestion_id_none(self) -> None:
        with patch("app.db.unit_of_work.UnitOfWork") as mock_uow_cls:
            _add_run_stage_by_id(
                ingestion_id=None, stage=SourceIngestionStage.MODULE_FEATURE_READY_FOR_REVIEW
            )
        mock_uow_cls.assert_not_called()

    def test_appends_stage(self) -> None:
        uow = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None
        ingestion_id = uuid.uuid4()

        with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
            _add_run_stage_by_id(
                ingestion_id=str(ingestion_id),
                stage=SourceIngestionStage.MODULE_FEATURE_READY_FOR_REVIEW,
            )

        uow.source_ingestions.add_stage.assert_called_once_with(
            ingestion_id, SourceIngestionStage.MODULE_FEATURE_READY_FOR_REVIEW.value
        )
        uow.commit.assert_called_once()

    def test_swallows_exceptions(self) -> None:
        with patch("app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db down")):
            _add_run_stage_by_id(
                ingestion_id="ing-1", stage=SourceIngestionStage.MODULE_FEATURE_READY_FOR_REVIEW
            )  # must not raise


# ── ZIP path-traversal guard (CWE-22) ───────────────────────────────────────


def _build_zip(entries: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _codebase_dir_for(project_id: str) -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "temp" / "source_codes" / project_id / "codebase"


@pytest.mark.parametrize(
    "malicious_name",
    [
        "../../evil.txt",
        "../evil.txt",
        "/etc/passwd",
        "sub/../../evil.txt",
    ],
)
def test_extract_zip_rejects_path_traversal_entries(malicious_name: str) -> None:
    project_id = f"test-zip-guard-{uuid.uuid4().hex}"
    codebase_dir = _codebase_dir_for(project_id)
    zip_bytes = _build_zip({"safe.txt": b"ok", malicious_name: b"malicious"})

    try:
        with pytest.raises(ValueError, match="Unsafe ZIP entry"):
            _extract_zip_to_codebase_folder(zip_bytes, project_id)
        # The unsafe archive must never be extracted (directory stays empty).
        assert not any(codebase_dir.iterdir())
    finally:
        shutil.rmtree(codebase_dir.parent, ignore_errors=True)


def test_extract_zip_accepts_safe_entries() -> None:
    project_id = f"test-zip-guard-{uuid.uuid4().hex}"
    codebase_dir = _codebase_dir_for(project_id)
    zip_bytes = _build_zip({"safe.txt": b"ok", "nested/file.py": b"print('hi')"})

    try:
        result_path = _extract_zip_to_codebase_folder(zip_bytes, project_id)
        assert Path(result_path) == codebase_dir
        assert (codebase_dir / "safe.txt").read_bytes() == b"ok"
        assert (codebase_dir / "nested" / "file.py").read_bytes() == b"print('hi')"
    finally:
        shutil.rmtree(codebase_dir.parent, ignore_errors=True)


# ── Cancellation checkpoints ────────────────────────────────────────────────
# Note: the global `_no_celery_dispatch` fixture (tests/conftest.py) patches
# `app.core.task_control.is_request_cancelled` to always return False, so
# these tests override it locally to exercise the "cancelled" branch.


def test_mark_cancelled_and_check_false_when_not_cancelled() -> None:
    assert (
        mark_cancelled_and_check(
            request_id="req-1",
            task_db_id="task-1",
            project_id="proj-1",
            task_type="source_process",
        )
        is False
    )


def test_mark_cancelled_and_check_false_when_request_id_missing() -> None:
    """No request_id means no Redis lookup at all — can't possibly be cancelled."""
    with patch("app.core.task_control.is_request_cancelled") as mock_is_cancelled:
        result = mark_cancelled_and_check(
            request_id=None,
            task_db_id="task-1",
            project_id="proj-1",
            task_type="source_process",
        )
    assert result is False
    mock_is_cancelled.assert_not_called()


def test_mark_cancelled_and_check_true_emits_event_and_returns_true() -> None:
    with (
        patch("app.core.task_control.is_request_cancelled", return_value=True),
        patch("app.workers._task_helpers.emit_task_event") as mock_emit,
    ):
        result = mark_cancelled_and_check(
            request_id="req-1",
            task_db_id="task-1",
            project_id="proj-1",
            task_type="source_process",
        )

    assert result is True
    mock_emit.assert_called_once()
    assert mock_emit.call_args.kwargs["status"] == "cancelled"


def test_mark_status_publishes_task_event_when_task_db_id_and_project_id_given() -> None:
    source = SimpleNamespace(status="queued", processing_error=None)
    uow = MagicMock()
    uow.sources.get_by_uuid.return_value = source
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with (
        patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
        patch("app.websockets.manager.publish_task_event_sync") as mock_publish,
    ):
        _mark_status(
            "00000000-0000-0000-0000-000000000001",
            SOURCE_STATUS_READY_FOR_REVIEW,
            task_db_id="task-1",
            project_id="proj-1",
        )

    mock_publish.assert_called_once()
    assert mock_publish.call_args.kwargs["task_db_id"] == "task-1"
    assert mock_publish.call_args.kwargs["progress"] == 100


def test_mark_status_source_not_found_is_a_noop() -> None:
    uow = MagicMock()
    uow.sources.get_by_uuid.return_value = None
    cm = MagicMock()
    cm.__enter__.return_value = uow
    cm.__exit__.return_value = None

    with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
        _mark_status("00000000-0000-0000-0000-000000000001", SOURCE_STATUS_READY_FOR_REVIEW)

    uow.commit.assert_not_called()


class TestUpdateSourceIngestionFieldsById:
    def test_noop_when_ingestion_id_missing(self):
        with patch("app.db.unit_of_work.UnitOfWork") as mock_uow_cls:
            _update_source_ingestion_fields_by_id(ingestion_id=None, fields={"status": "cancelled"})
        mock_uow_cls.assert_not_called()

    def test_noop_when_fields_empty(self):
        with patch("app.db.unit_of_work.UnitOfWork") as mock_uow_cls:
            _update_source_ingestion_fields_by_id(ingestion_id="ing-1", fields={})
        mock_uow_cls.assert_not_called()

    def test_updates_fields_and_commits(self):
        uow = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None
        ingestion_id = "00000000-0000-0000-0000-000000000002"

        with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
            _update_source_ingestion_fields_by_id(
                ingestion_id=ingestion_id, fields={"status": "cancelled"}
            )

        uow.source_ingestions.update_fields.assert_called_once()
        uow.commit.assert_called_once()

    def test_swallows_exception(self):
        with patch("app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db down")):
            _update_source_ingestion_fields_by_id(
                ingestion_id="00000000-0000-0000-0000-000000000002", fields={"status": "x"}
            )  # must not raise


class TestGetSourceIngestionSourceType:
    def test_returns_none_when_ingestion_id_missing(self):
        assert _get_source_ingestion_source_type(None) is None

    def test_returns_source_type(self):
        uow = MagicMock()
        uow.source_ingestions.get_by_id.return_value = SimpleNamespace(source_type="rfp")
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
            result = _get_source_ingestion_source_type("00000000-0000-0000-0000-000000000003")

        assert result == "rfp"

    def test_returns_none_when_ingestion_not_found(self):
        uow = MagicMock()
        uow.source_ingestions.get_by_id.return_value = None
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
            result = _get_source_ingestion_source_type("00000000-0000-0000-0000-000000000003")

        assert result is None

    def test_returns_none_on_exception(self):
        with patch("app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db down")):
            assert _get_source_ingestion_source_type("00000000-0000-0000-0000-000000000003") is None


class TestMarkSourcesAndIngestionCancelled:
    def test_marks_sources_and_updates_ingestion_by_id(self):
        with (
            patch("app.workers._task_helpers._mark_status") as mock_mark,
            patch(
                "app.workers._task_helpers._update_source_ingestion_fields_by_id"
            ) as mock_update_by_id,
            patch("app.workers._task_helpers._update_source_ingestion_fields") as mock_update,
        ):
            mark_sources_and_ingestion_cancelled(
                source_ids=["s1", "s2"],
                project_id="proj-1",
                task_type="source_process",
                stage="cancelled",
                ingestion_id="ing-1",
            )

        assert mock_mark.call_count == 2
        mock_update_by_id.assert_called_once()
        mock_update.assert_not_called()

    def test_falls_back_to_source_ids_when_no_ingestion_id(self):
        with (
            patch("app.workers._task_helpers._mark_status"),
            patch(
                "app.workers._task_helpers._update_source_ingestion_fields_by_id"
            ) as mock_update_by_id,
            patch("app.workers._task_helpers._update_source_ingestion_fields") as mock_update,
        ):
            mark_sources_and_ingestion_cancelled(
                source_ids=["s1"],
                project_id="proj-1",
                task_type="source_process",
                stage="cancelled",
            )

        mock_update_by_id.assert_not_called()
        mock_update.assert_called_once()

    def test_noop_updates_when_no_sources_and_no_ingestion_id(self):
        with (
            patch("app.workers._task_helpers._mark_status") as mock_mark,
            patch(
                "app.workers._task_helpers._update_source_ingestion_fields_by_id"
            ) as mock_update_by_id,
            patch("app.workers._task_helpers._update_source_ingestion_fields") as mock_update,
        ):
            mark_sources_and_ingestion_cancelled(
                source_ids=[], project_id="proj-1", task_type="source_process", stage="cancelled"
            )

        mock_mark.assert_not_called()
        mock_update_by_id.assert_not_called()
        mock_update.assert_not_called()


class TestMarkSourcesStatusByProject:
    def test_marks_every_resolved_source(self):
        uow = MagicMock()
        uow.sources.get_ids_by_project.return_value = [uuid.UUID(int=1), uuid.UUID(int=2)]
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch("app.workers._task_helpers._mark_status") as mock_mark,
        ):
            _mark_sources_status_by_project(
                project_id="00000000-0000-0000-0000-000000000004",
                status=SOURCE_STATUS_READY_FOR_REVIEW,
                stage="done",
            )

        assert mock_mark.call_count == 2

    def test_swallows_exception_resolving_source_ids(self):
        with (
            patch("app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db down")),
            patch("app.workers._task_helpers._mark_status") as mock_mark,
        ):
            _mark_sources_status_by_project(
                project_id="00000000-0000-0000-0000-000000000004",
                status=SOURCE_STATUS_READY_FOR_REVIEW,
                stage="done",
            )

        mock_mark.assert_not_called()


class TestHandleTaskException:
    def test_max_retries_reached_marks_failed_and_returns_result(self):
        task = MagicMock()
        task.request.retries = 3
        task.max_retries = 3

        with (
            patch("app.workers._task_helpers._increment_retry") as mock_incr,
            patch("app.workers._task_helpers._mark_status") as mock_mark,
        ):
            result = _handle_task_exception(
                task, "source-1", RuntimeError("boom"), "parse_document_task"
            )

        mock_incr.assert_called_once_with("source-1")
        mock_mark.assert_called_once()
        assert result == {"source_id": "source-1", "status": SOURCE_STATUS_FAILED, "error": "boom"}

    def test_retries_remaining_schedules_retry_and_notifies(self):
        task = MagicMock()
        task.request.retries = 0
        task.max_retries = 3
        task.retry.side_effect = RuntimeError("celery-retry-signal")

        with (
            patch("app.workers._task_helpers._increment_retry"),
            patch("app.websockets.manager.publish_task_event_sync") as mock_publish,
            pytest.raises(RuntimeError, match="celery-retry-signal"),
        ):
            _handle_task_exception(
                task,
                "source-1",
                RuntimeError("boom"),
                "parse_document_task",
                task_db_id="task-1",
                project_id="proj-1",
            )

        mock_publish.assert_called_once()
        task.retry.assert_called_once()

    def test_retries_remaining_without_task_db_id_skips_notify(self):
        task = MagicMock()
        task.request.retries = 0
        task.max_retries = 3
        task.retry.side_effect = RuntimeError("celery-retry-signal")

        with (
            patch("app.workers._task_helpers._increment_retry"),
            patch("app.websockets.manager.publish_task_event_sync") as mock_publish,
            pytest.raises(RuntimeError),
        ):
            _handle_task_exception(task, "source-1", RuntimeError("boom"), "parse_document_task")

        mock_publish.assert_not_called()


class TestEmitTaskEvent:
    def test_noop_when_task_db_id_missing(self):
        with patch("app.websockets.manager.publish_task_event_sync") as mock_publish:
            emit_task_event(
                task_db_id=None,
                project_id="proj-1",
                task_type="source_process",
                status="running",
                stage="parsing",
                progress=50,
            )

        mock_publish.assert_not_called()

    def test_publishes_when_task_db_id_given(self):
        with patch("app.websockets.manager.publish_task_event_sync") as mock_publish:
            emit_task_event(
                task_db_id="task-1",
                project_id="proj-1",
                task_type="source_process",
                status="running",
                stage="parsing",
                progress=50,
            )

        mock_publish.assert_called_once()


class TestUpsertNeo4jFileNode:
    async def test_upserts_when_source_found(self):
        uow = MagicMock()
        source = MagicMock()
        uow.sources.get_by_uuid.return_value = source
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None
        neo4j_repo = MagicMock()
        neo4j_repo.upsert_file_node = AsyncMock()

        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=cm),
            patch(
                "app.repositories.neo4j.source_repository.SourceRepository.orm_to_node",
                return_value="file-node",
            ),
        ):
            await _upsert_neo4j_file_node("00000000-0000-0000-0000-000000000005", neo4j_repo)

        neo4j_repo.upsert_file_node.assert_awaited_once_with("file-node")

    async def test_noop_when_source_not_found(self):
        uow = MagicMock()
        uow.sources.get_by_uuid.return_value = None
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None
        neo4j_repo = MagicMock()
        neo4j_repo.upsert_file_node = AsyncMock()

        with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
            await _upsert_neo4j_file_node("00000000-0000-0000-0000-000000000005", neo4j_repo)

        neo4j_repo.upsert_file_node.assert_not_awaited()


class TestDownloadSourceZipFromS3:
    async def test_downloads_and_returns_bytes(self):
        body = AsyncMock()
        body.read.return_value = b"zip-bytes"
        s3_client = AsyncMock()
        s3_client.get_object.return_value = {"Body": body}
        s3_client.__aenter__.return_value = s3_client
        s3_client.__aexit__.return_value = False

        session = MagicMock()
        session.client.return_value = s3_client

        with patch("app.clients.aws_session.get_aws_session", return_value=session):
            result = await _download_source_zip_from_s3("source-1", "sources/key.zip")

        assert result == b"zip-bytes"


class TestIsRetryableBoto:
    """CELERY_RETRY_POLICY.docx Section D reference: throttling/5xx retry
    once, deterministic client errors (auth, missing key, ...) do not."""

    @pytest.mark.parametrize(
        "code,http_status",
        [
            ("Throttling", 400),
            ("ThrottlingException", 400),
            ("SlowDown", 503),
            ("RequestTimeout", 400),
            ("ServiceUnavailable", 503),
            ("InternalError", 500),
            ("SomeOtherCode", 429),
            ("SomeOtherCode", 500),
            ("SomeOtherCode", 502),
            ("SomeOtherCode", 503),
            ("SomeOtherCode", 504),
        ],
    )
    def test_true_for_retryable_code_or_status(self, code: str, http_status: int) -> None:
        from botocore.exceptions import ClientError

        exc = ClientError(
            {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": http_status}},
            "GetObject",
        )
        assert is_retryable_boto(exc) is True

    @pytest.mark.parametrize(
        "code,http_status",
        [
            ("AccessDenied", 403),
            ("InvalidAccessKeyId", 403),
            ("NoSuchKey", 404),
            ("ExpiredToken", 400),
        ],
    )
    def test_false_for_deterministic_code_and_status(self, code: str, http_status: int) -> None:
        from botocore.exceptions import ClientError

        exc = ClientError(
            {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": http_status}},
            "GetObject",
        )
        assert is_retryable_boto(exc) is False


def test_retryable_module_infra_and_neo4j_infra_are_disjoint_exception_tuples() -> None:
    """Sanity check for the two tuples _process_single_module_task and
    _persist_single_module_task's except clauses are built from."""
    assert set(RETRYABLE_MODULE_INFRA).isdisjoint(RETRYABLE_NEO4J_INFRA)
    assert all(
        issubclass(exc, BaseException) for exc in (*RETRYABLE_MODULE_INFRA, *RETRYABLE_NEO4J_INFRA)
    )
