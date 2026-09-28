"""Unit tests for IncrementalBulkUploadService."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.config import settings
from app.core.constants import (
    BYTES_PER_MB,
    CONTEXT_MODE_FULL,
    SOURCE_DEFAULT_FORMAT,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_UPLOADED,
)
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    StorageError,
    ValidationError as AppValidationError,
)
from app.services.incremental_source_service import (
    _ITEM_STATUS_DUPLICATE,
    SOURCE_KIND_IMAGE,
    SOURCE_KIND_PDF,
    IncrementalBulkUploadService,
    _BatchState,
    _classify_mime,
    _compensate_s3,
    _derive_file_type,
    _enqueue_incremental_update,
    _UploadedSource,
)
from tests.conftest import make_source

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_upload_file(
    filename: str = "test.pdf",
    content_type: str = "application/pdf",
    data: bytes = b"pdf-bytes",
) -> MagicMock:
    """Build a mock ``UploadFile``.

    Every attribute the service touches (``filename``, ``content_type``,
    ``size``, ``read``) is set explicitly — an unconfigured MagicMock
    attribute returns a fresh Mock rather than None/0, which breaks the
    size-sum/len() arithmetic in the service with a confusing TypeError.
    """
    f = AsyncMock()
    f.filename = filename
    f.content_type = content_type
    f.size = len(data)
    f.read = AsyncMock(return_value=data)
    return f


@pytest.fixture(autouse=True)
def _pipeline_ok(monkeypatch):
    """Default: no incremental/source-code pipeline currently running.

    ``SourceIngestionService.raise_if_pipeline_running`` is imported lazily
    inside ``upload_and_enqueue`` itself, so it must be patched at its own
    source module (see tests/.claude/rules/tests.md's guidance for lazy
    imports). Tests exercising the conflict path override this via their own
    ``monkeypatch`` call.
    """
    monkeypatch.setattr(
        "app.services.source_ingestion_service.SourceIngestionService.raise_if_pipeline_running",
        lambda uow, project_id: None,
    )


@pytest.fixture
def infra(monkeypatch, uow):
    """Patch every I/O boundary ``upload_and_enqueue`` touches.

    Covers S3 (upload/presign/delete), Neo4j projection, ProjectTask
    creation, and the Celery dispatch of ``incremental_update_task`` — none
    of which may hit a real service in a unit test.
    """
    uow.sources.create.side_effect = lambda s: s
    uow.sources.get_by_checksum.return_value = None

    mock_upload = AsyncMock(return_value="s3-key")
    mock_presign = AsyncMock(return_value="https://example.com/presigned")
    mock_delete = AsyncMock()
    monkeypatch.setattr("app.services.incremental_source_service.upload_to_s3", mock_upload)
    monkeypatch.setattr(
        "app.services.incremental_source_service.generate_presigned_url", mock_presign
    )
    monkeypatch.setattr("app.services.incremental_source_service.delete_from_s3", mock_delete)

    # get_neo4j_driver is called eagerly as an argument to Neo4jSourceRepository(...)
    # even when that class itself is mocked below, so it must be patched too.
    monkeypatch.setattr("app.services.incremental_source_service.get_neo4j_driver", MagicMock())
    neo4j_repo = MagicMock()
    neo4j_repo.create_level1_sources = AsyncMock()
    neo4j_repo_cls = MagicMock(return_value=neo4j_repo)
    monkeypatch.setattr(
        "app.services.incremental_source_service.Neo4jSourceRepository", neo4j_repo_cls
    )

    # Default: the existing backlog is fully approved, so the pre-upload
    # approval gate never blocks tests that aren't specifically exercising it.
    module_feature_repo = MagicMock()
    module_feature_repo.are_all_modules_approved = AsyncMock(return_value=True)
    module_feature_repo.are_all_features_approved = AsyncMock(return_value=True)
    module_feature_repo_cls = MagicMock(return_value=module_feature_repo)
    monkeypatch.setattr(
        "app.services.incremental_source_service.ModuleFeatureRepository",
        module_feature_repo_cls,
    )

    user_story_repo = MagicMock()
    user_story_repo.are_all_user_stories_approved = AsyncMock(return_value=True)
    user_story_repo_cls = MagicMock(return_value=user_story_repo)
    monkeypatch.setattr(
        "app.services.incremental_source_service.UserStoryRepository", user_story_repo_cls
    )

    create_task_mock = MagicMock(return_value=(uuid.uuid4(), str(uuid.uuid4())))
    set_celery_mock = MagicMock()
    monkeypatch.setattr(
        "app.services.project_task_service.ProjectTaskService.create_task", create_task_mock
    )
    monkeypatch.setattr(
        "app.services.project_task_service.ProjectTaskService.set_celery_task_id",
        set_celery_mock,
    )

    apply_async_mock = MagicMock(return_value=MagicMock(id="celery-xyz"))
    monkeypatch.setattr(
        "app.workers.incremental_task.incremental_update_task.apply_async", apply_async_mock
    )

    return {
        "upload_to_s3": mock_upload,
        "presign": mock_presign,
        "delete_s3": mock_delete,
        "neo4j_repo_cls": neo4j_repo_cls,
        "neo4j_repo": neo4j_repo,
        "module_feature_repo_cls": module_feature_repo_cls,
        "module_feature_repo": module_feature_repo,
        "user_story_repo_cls": user_story_repo_cls,
        "user_story_repo": user_story_repo,
        "create_task": create_task_mock,
        "set_celery_task_id": set_celery_mock,
        "apply_async": apply_async_mock,
    }


# ── _classify_mime / _derive_file_type ──────────────────────────────────────


class TestClassifyMime:
    def test_pdf_mime_classified_as_pdf(self):
        assert _classify_mime("application/pdf") == SOURCE_KIND_PDF

    def test_image_mime_classified_as_image(self):
        assert _classify_mime("image/png") == SOURCE_KIND_IMAGE
        assert _classify_mime("image/jpeg") == SOURCE_KIND_IMAGE
        assert _classify_mime("image/webp") == SOURCE_KIND_IMAGE


class TestDeriveFileType:
    def test_extension_present_returns_uppercased_extension(self):
        assert _derive_file_type("report.pdf", "application/pdf") == "PDF"
        assert _derive_file_type("photo.PNG", "image/png") == "PNG"

    def test_no_extension_falls_back_to_content_type_map(self):
        assert _derive_file_type("noext", "application/pdf") == "PDF"
        assert _derive_file_type("noext", "image/webp") == "WEBP"

    def test_no_extension_unknown_content_type_returns_default(self):
        assert _derive_file_type("noext", "application/octet-stream") == SOURCE_DEFAULT_FORMAT


# ── _BatchState ──────────────────────────────────────────────────────────────


class TestBatchState:
    def test_add_fail_appends_failed_item(self):
        state = _BatchState()
        state.add_fail("a.pdf", 422, "bad file")

        assert len(state.item_results) == 1
        item = state.item_results[0]
        assert item.status == SOURCE_STATUS_FAILED
        assert item.status_code == 422
        assert item.error == "bad file"

    def test_add_duplicate_appends_duplicate_item(self):
        state = _BatchState()
        existing_id = uuid.uuid4()
        state.add_duplicate("a.pdf", existing_id, "dup")

        item = state.item_results[0]
        assert item.status == "duplicate"
        assert item.source_id == existing_id

    def test_add_success_appends_uploaded_item_and_tracks_source(self):
        state = _BatchState()
        source = SimpleNamespace(original_name="a.pdf", id=uuid.uuid4())
        state.add_success(source, SOURCE_KIND_PDF)

        assert state.item_results[0].status == SOURCE_STATUS_UPLOADED
        assert state.item_results[0].source_id == source.id
        assert len(state.uploaded_sources) == 1
        assert state.uploaded_sources[0].source is source
        assert state.uploaded_sources[0].kind == SOURCE_KIND_PDF


# ── upload_and_enqueue ───────────────────────────────────────────────────────


class TestUploadAndEnqueue:
    @pytest.mark.asyncio
    async def test_success_single_pdf_file(self, uow, infra):
        files = [_make_upload_file(filename="a.pdf", data=b"pdf-content-a")]
        project_id = uuid.uuid4()

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=project_id,
            description="desc",
            source_type="rfp",
            user_message="please update",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert isinstance(result.batch_id, uuid.UUID)
        assert result.total == 1
        assert result.succeeded == 1
        assert result.failed == 0
        assert result.results[0].status == SOURCE_STATUS_UPLOADED
        assert result.task_id == infra["create_task"].return_value[1]

        infra["neo4j_repo"].create_level1_sources.assert_awaited_once()
        infra["apply_async"].assert_called_once()
        uow.source_ingestions.add_stage.assert_called_once_with(
            ANY, SourceIngestionStage.GENERATING_REQUIREMENTS.value
        )

    @pytest.mark.asyncio
    async def test_success_multiple_files_mixed_pdf_and_image(self, uow, infra):
        files = [
            _make_upload_file(filename="a.pdf", data=b"pdf-content"),
            _make_upload_file(filename="b.png", content_type="image/png", data=b"image-content"),
        ]
        project_id = uuid.uuid4()

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=project_id,
            description=None,
            source_type="rfp",
            user_message="please update",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 2
        pdf_source_id = result.results[0].source_id
        img_source_id = result.results[1].source_id
        task_db_id = infra["create_task"].return_value[1]

        infra["apply_async"].assert_called_once_with(
            args=[
                str(project_id),
                [str(pdf_source_id)],
                [str(img_source_id)],
                "please update",
                False,
                task_db_id,
                CONTEXT_MODE_FULL,
            ]
        )

    @pytest.mark.asyncio
    async def test_project_not_found_raises_not_found_error(self, uow):
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            await IncrementalBulkUploadService().upload_and_enqueue(
                files=[_make_upload_file()],
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                user_message="",
                skip_processing=False,
                uploader_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_forbidden_access_raises_forbidden_error(self, uow):
        with (
            patch(
                "app.services.incremental_source_service.ProjectService.assert_project_access",
                side_effect=ForbiddenError("no access"),
            ),
            pytest.raises(ForbiddenError),
        ):
            await IncrementalBulkUploadService().upload_and_enqueue(
                files=[_make_upload_file()],
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                user_message="",
                skip_processing=False,
                uploader_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_llm_api_key_not_configured_raises_validation_error(self, uow):
        with (
            patch(
                "app.services.incremental_source_service.ProjectService.assert_llm_api_key_configured",
                side_effect=AppValidationError("no llm key configured"),
            ),
            pytest.raises(AppValidationError, match="no llm key configured"),
        ):
            await IncrementalBulkUploadService().upload_and_enqueue(
                files=[_make_upload_file()],
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                user_message="",
                skip_processing=False,
                uploader_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_no_existing_rfp_source_raises_validation_error(self, uow):
        uow.sources.exists_by_project_and_source_type.return_value = False

        with pytest.raises(AppValidationError, match="requires at least one RFP source"):
            await IncrementalBulkUploadService().upload_and_enqueue(
                files=[_make_upload_file()],
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                user_message="",
                skip_processing=False,
                uploader_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_pipeline_running_raises_conflict_error(self, uow, monkeypatch):
        monkeypatch.setattr(
            "app.services.source_ingestion_service.SourceIngestionService.raise_if_pipeline_running",
            MagicMock(side_effect=ConflictError("a pipeline is already running")),
        )

        with pytest.raises(ConflictError):
            await IncrementalBulkUploadService().upload_and_enqueue(
                files=[_make_upload_file()],
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                user_message="",
                skip_processing=False,
                uploader_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_modules_not_approved_raises_validation_error(self, uow, infra):
        infra["module_feature_repo"].are_all_modules_approved = AsyncMock(return_value=False)

        with pytest.raises(AppValidationError, match="all modules to be approved"):
            await IncrementalBulkUploadService().upload_and_enqueue(
                files=[_make_upload_file()],
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                user_message="",
                skip_processing=False,
                uploader_id=uuid.uuid4(),
                uow=uow,
            )
        infra["module_feature_repo"].are_all_features_approved.assert_not_awaited()
        infra["user_story_repo"].are_all_user_stories_approved.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_features_not_approved_raises_validation_error(self, uow, infra):
        infra["module_feature_repo"].are_all_features_approved = AsyncMock(return_value=False)

        with pytest.raises(AppValidationError, match="all features to be approved"):
            await IncrementalBulkUploadService().upload_and_enqueue(
                files=[_make_upload_file()],
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                user_message="",
                skip_processing=False,
                uploader_id=uuid.uuid4(),
                uow=uow,
            )
        infra["user_story_repo"].are_all_user_stories_approved.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_stories_not_approved_raises_validation_error(self, uow, infra):
        infra["user_story_repo"].are_all_user_stories_approved = AsyncMock(return_value=False)

        with pytest.raises(AppValidationError, match="all user stories to be approved"):
            await IncrementalBulkUploadService().upload_and_enqueue(
                files=[_make_upload_file()],
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                user_message="",
                skip_processing=False,
                uploader_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_no_files_raises_validation_error(self, uow, infra):
        with pytest.raises(AppValidationError, match="At least one file is required"):
            await IncrementalBulkUploadService().upload_and_enqueue(
                files=[],
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                user_message="",
                skip_processing=False,
                uploader_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_too_many_files_raises_validation_error(self, uow, infra):
        files = [
            _make_upload_file(filename=f"f{i}.pdf")
            for i in range(settings.SOURCE_BULK_MAX_FILES + 1)
        ]

        with pytest.raises(AppValidationError, match="at most"):
            await IncrementalBulkUploadService().upload_and_enqueue(
                files=files,
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                user_message="",
                skip_processing=False,
                uploader_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_cumulative_size_exceeded_raises_validation_error(self, uow, infra):
        files = [_make_upload_file(filename="huge.pdf")]
        files[0].size = settings.SOURCE_BULK_MAX_TOTAL_SIZE_MB * BYTES_PER_MB + 1

        with pytest.raises(AppValidationError, match="exceeds the maximum"):
            await IncrementalBulkUploadService().upload_and_enqueue(
                files=files,
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                user_message="",
                skip_processing=False,
                uploader_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_per_file_too_large_marked_failed(self, uow, infra, monkeypatch):
        """The per-file cap is enforced against actual bytes read, not `.size`."""
        monkeypatch.setattr(settings, "SOURCE_MAX_FILE_SIZE_MB", 0)
        files = [_make_upload_file(filename="big.pdf", data=b"some-bytes")]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 0
        assert result.failed == 1
        assert result.results[0].status == SOURCE_STATUS_FAILED
        assert result.results[0].status_code == 422
        assert "exceeds the maximum" in result.results[0].error

    @pytest.mark.asyncio
    async def test_unsupported_mime_marked_failed(self, uow, infra):
        files = [
            _make_upload_file(
                filename="virus.exe", content_type="application/x-msdownload", data=b"MZ"
            )
        ]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 0
        assert result.failed == 1
        assert result.results[0].status == SOURCE_STATUS_FAILED
        assert result.results[0].status_code == 422
        assert "Unsupported file type" in result.results[0].error

    @pytest.mark.asyncio
    async def test_duplicate_existing_in_db_marked_duplicate(self, uow, infra):
        existing = make_source()
        uow.sources.get_by_checksum.return_value = existing
        files = [_make_upload_file(filename="dup.pdf")]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 0
        assert result.results[0].status == "duplicate"
        assert result.results[0].source_id == existing.id

    @pytest.mark.asyncio
    async def test_duplicate_within_batch_marked_duplicate(self, uow, infra):
        files = [
            _make_upload_file(filename="a.pdf", data=b"identical-bytes"),
            _make_upload_file(filename="b.pdf", data=b"identical-bytes"),
        ]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.total == 2
        assert result.succeeded == 1
        statuses = [r.status for r in result.results]
        assert statuses.count(SOURCE_STATUS_UPLOADED) == 1
        assert statuses.count("duplicate") == 1
        dup_item = next(r for r in result.results if r.status == "duplicate")
        assert dup_item.source_id is None

    @pytest.mark.asyncio
    async def test_duplicate_blocks_source_originally_uploaded_via_non_incremental_path(
        self, uow, infra
    ):
        """Dedup is project-wide, not scoped to the upload path: a source
        created by the regular (non-incremental) bulk upload — upload_type
        defaults to "single" in ``make_source`` — must still block a
        matching incremental upload, since ``get_by_checksum``/
        ``get_by_filename`` filter only by project_id, never upload_type."""
        existing = make_source()
        assert existing.upload_type == "single"
        uow.sources.get_by_checksum.return_value = existing
        files = [_make_upload_file(filename="dup.pdf")]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 0
        assert result.results[0].status == "duplicate"
        assert result.results[0].source_id == existing.id

    @pytest.mark.asyncio
    async def test_duplicate_filename_existing_in_db_marked_duplicate(self, uow, infra):
        """Same filename, different content — blocked even though the
        checksum lookup finds nothing."""
        existing = make_source()
        uow.sources.get_by_filename.return_value = existing
        files = [_make_upload_file(filename="requirements.pdf")]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 0
        assert result.results[0].status == "duplicate"
        assert result.results[0].source_id == existing.id

    @pytest.mark.asyncio
    async def test_duplicate_filename_within_batch_marked_duplicate(self, uow, infra):
        """Two files sharing a filename (case-insensitive) but different
        content in the same batch — the second is flagged as a duplicate."""
        files = [
            _make_upload_file(filename="Requirements.pdf", data=b"content-a"),
            _make_upload_file(filename="requirements.pdf", data=b"content-b"),
        ]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.total == 2
        assert result.succeeded == 1
        statuses = [r.status for r in result.results]
        assert statuses.count(SOURCE_STATUS_UPLOADED) == 1
        assert statuses.count("duplicate") == 1
        dup_item = next(r for r in result.results if r.status == "duplicate")
        assert dup_item.source_id is None

    @pytest.mark.asyncio
    async def test_s3_upload_failure_recorded_as_failed_item(self, uow, infra):
        infra["upload_to_s3"].side_effect = [StorageError("bucket unavailable"), "key-ok"]
        files = [
            _make_upload_file(filename="bad.pdf", data=b"1"),
            _make_upload_file(filename="good.pdf", data=b"2"),
        ]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.total == 2
        assert result.succeeded == 1
        assert result.failed == 1
        failed_item = next(r for r in result.results if r.filename == "bad.pdf")
        ok_item = next(r for r in result.results if r.filename == "good.pdf")
        assert failed_item.status == SOURCE_STATUS_FAILED
        assert failed_item.status_code == 502
        assert ok_item.status == SOURCE_STATUS_UPLOADED

    @pytest.mark.asyncio
    async def test_presigned_url_failure_is_non_fatal(self, uow, infra):
        infra["presign"].side_effect = StorageError("presign error")
        files = [_make_upload_file(filename="ok.pdf")]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 1
        assert result.results[0].status == SOURCE_STATUS_UPLOADED
        committed_source = uow.sources.create.call_args[0][0]
        assert committed_source.storage_url is None

    @pytest.mark.asyncio
    async def test_db_commit_failure_marks_item_failed_and_compensates_s3(self, uow, infra):
        uow.commit.side_effect = [RuntimeError("db connection lost"), None, None]
        files = [
            _make_upload_file(filename="doc-fail.pdf", data=b"fail-content"),
            _make_upload_file(filename="doc-ok.pdf", data=b"ok-content"),
        ]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )
        # Give the fire-and-forget compensation task a chance to run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert result.total == 2
        assert result.succeeded == 1
        assert result.failed == 1
        failed_item = next(r for r in result.results if r.status_code == 500)
        ok_item = next(r for r in result.results if r.status_code == 201)
        assert "database error" in (failed_item.error or "").lower()
        assert ok_item.status == SOURCE_STATUS_UPLOADED
        infra["delete_s3"].assert_awaited_once()
        uow.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_rollback_failure_after_commit_failure_is_swallowed(self, uow, infra):
        """A rollback() that itself raises must not escape the batch loop."""
        uow.commit.side_effect = [RuntimeError("db connection lost"), None]
        uow.rollback.side_effect = RuntimeError("rollback also failed")
        files = [_make_upload_file(filename="doc-fail.pdf", data=b"fail-content-2")]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert result.failed == 1
        assert result.results[0].status_code == 500

    @pytest.mark.asyncio
    async def test_all_files_failed_pre_validation_skips_ingestion_and_task(self, uow, infra):
        """Nothing survives pre-validation: no SourceIngestion, no ProjectTask, no dispatch.

        This is the "duplicate/invalid upload must not start Source Ingestion"
        contract — validation results are still fully reported, but no DB row
        or background task is created for a batch that has nothing to do.
        """
        files = [
            _make_upload_file(
                filename="bad.exe", content_type="application/x-msdownload", data=b"MZ"
            )
        ]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 0
        assert result.failed == 1
        assert result.task_id is None
        infra["neo4j_repo_cls"].assert_not_called()
        infra["apply_async"].assert_not_called()
        infra["set_celery_task_id"].assert_not_called()
        infra["create_task"].assert_not_called()
        uow.source_ingestions.add_stage.assert_not_called()
        # Regression: a batch with nothing to upload must not leave a
        # SourceIngestion row behind at all — an empty status="running" row
        # would permanently block every later bulk-upload call for the
        # project via raise_if_pipeline_running (which rejects ANY running
        # ingestion), even one containing only valid, non-duplicate files.
        uow.source_ingestions.create_ingestion.assert_not_called()
        uow.source_ingestions.update_fields.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_duplicates_skips_ingestion_and_task(self, uow, infra):
        """Same contract as above, exercised via the duplicate-detection path specifically."""
        existing_source = MagicMock(id=uuid.uuid4())
        uow.sources.get_by_checksum.return_value = existing_source
        files = [_make_upload_file(filename="dup.pdf", data=b"dup-content")]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 0
        assert result.results[0].status == _ITEM_STATUS_DUPLICATE
        assert result.task_id is None
        uow.source_ingestions.create_ingestion.assert_not_called()
        infra["create_task"].assert_not_called()

    @pytest.mark.asyncio
    async def test_all_persist_failures_marks_ingestion_failed_not_left_running(self, uow, infra):
        """Files pass pre-validation (so the ingestion IS created) but every S3
        upload then fails — the ingestion must still be closed out as failed
        rather than left stuck at status="running" with 0 sources attached.
        """
        infra["upload_to_s3"].side_effect = StorageError("bucket unavailable")
        files = [_make_upload_file(filename="a.pdf", data=b"content-a")]

        result = await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 0
        ingestion_id = uow.source_ingestions.create_ingestion.return_value.id
        uow.source_ingestions.create_ingestion.assert_called_once()
        uow.source_ingestions.update_fields.assert_called_once_with(
            ingestion_id, status=SourceIngestionStatus.FAILED.value, completed_at=ANY
        )
        uow.source_ingestions.add_error.assert_called_once()
        # A ProjectTask row is still created here — real work was attempted
        # (S3 was called), unlike the pre-validation-rejected cases above.
        infra["create_task"].assert_called_once()

    @pytest.mark.asyncio
    async def test_ingestion_created_with_incremental_flag_and_params(self, uow, infra):
        project_id = uuid.uuid4()
        files = [_make_upload_file()]

        await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=project_id,
            description="a description",
            source_type="rfp",
            user_message="a message",
            skip_processing=True,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        uow.source_ingestions.create_ingestion.assert_called_once_with(
            project_id=project_id,
            source_type="rfp",
            description="a description",
            is_incremental=True,
            user_message="a message",
            skip_processing=True,
        )

    @pytest.mark.asyncio
    async def test_empty_user_message_normalized_to_none(self, uow, infra):
        files = [_make_upload_file()]

        await IncrementalBulkUploadService().upload_and_enqueue(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            user_message="",
            skip_processing=False,
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        call_kwargs = uow.source_ingestions.create_ingestion.call_args.kwargs
        assert call_kwargs["user_message"] is None


# ── _project_sources_to_neo4j ────────────────────────────────────────────────


class TestProjectSourcesToNeo4j:
    @pytest.mark.asyncio
    async def test_skips_when_no_uploaded_sources(self, monkeypatch):
        neo4j_repo_cls = MagicMock()
        monkeypatch.setattr(
            "app.services.incremental_source_service.Neo4jSourceRepository", neo4j_repo_cls
        )

        await IncrementalBulkUploadService()._project_sources_to_neo4j(
            batch_id=uuid.uuid4(), uploaded_sources=[]
        )

        neo4j_repo_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_projects_uploaded_sources(self, monkeypatch):
        source = make_source()
        uploaded = [_UploadedSource(source=source, kind=SOURCE_KIND_PDF)]

        repo_instance = MagicMock()
        repo_instance.create_level1_sources = AsyncMock()
        neo4j_repo_cls = MagicMock(return_value=repo_instance)
        monkeypatch.setattr(
            "app.services.incremental_source_service.Neo4jSourceRepository", neo4j_repo_cls
        )
        monkeypatch.setattr("app.services.incremental_source_service.get_neo4j_driver", MagicMock())

        await IncrementalBulkUploadService()._project_sources_to_neo4j(
            batch_id=uuid.uuid4(), uploaded_sources=uploaded
        )

        repo_instance.create_level1_sources.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_neo4j_failure_is_swallowed(self, monkeypatch):
        source = make_source()
        uploaded = [_UploadedSource(source=source, kind=SOURCE_KIND_PDF)]

        repo_instance = MagicMock()
        repo_instance.create_level1_sources = AsyncMock(side_effect=RuntimeError("neo4j down"))
        monkeypatch.setattr(
            "app.services.incremental_source_service.Neo4jSourceRepository",
            MagicMock(return_value=repo_instance),
        )
        monkeypatch.setattr("app.services.incremental_source_service.get_neo4j_driver", MagicMock())

        # Best-effort: must not raise even though the repo call blew up.
        await IncrementalBulkUploadService()._project_sources_to_neo4j(
            batch_id=uuid.uuid4(), uploaded_sources=uploaded
        )


# ── _enqueue_incremental_update ──────────────────────────────────────────────


class TestEnqueueIncrementalUpdate:
    @patch("app.services.project_task_service.ProjectTaskService.set_celery_task_id")
    def test_skips_dispatch_when_no_source_ids(self, mock_set_celery):
        _enqueue_incremental_update(
            project_id=uuid.uuid4(),
            pdf_source_ids=[],
            image_source_ids=[],
            user_message="msg",
            skip_processing=False,
            context_mode=CONTEXT_MODE_FULL,
            task_db_id="task-db-id",
            task_id=uuid.uuid4(),
        )

        mock_set_celery.assert_not_called()

    @patch("app.workers.incremental_task.incremental_update_task.apply_async")
    @patch("app.services.project_task_service.ProjectTaskService.set_celery_task_id")
    def test_dispatches_with_correct_args_and_sets_celery_task_id(
        self, mock_set_celery, mock_apply_async
    ):
        mock_apply_async.return_value = MagicMock(id="celery-123")
        project_id = uuid.uuid4()
        task_id = uuid.uuid4()

        _enqueue_incremental_update(
            project_id=project_id,
            pdf_source_ids=["p1"],
            image_source_ids=["i1"],
            user_message="msg",
            skip_processing=True,
            context_mode=CONTEXT_MODE_FULL,
            task_db_id="task-db-id",
            task_id=task_id,
        )

        mock_apply_async.assert_called_once_with(
            args=[str(project_id), ["p1"], ["i1"], "msg", True, "task-db-id", CONTEXT_MODE_FULL]
        )
        mock_set_celery.assert_called_once_with(task_id, "celery-123")

    @patch("app.workers.incremental_task.incremental_update_task.apply_async")
    @patch("app.services.project_task_service.ProjectTaskService.set_celery_task_id")
    def test_dispatch_failure_is_caught_and_not_raised(self, mock_set_celery, mock_apply_async):
        mock_apply_async.side_effect = RuntimeError("broker unavailable")

        _enqueue_incremental_update(
            project_id=uuid.uuid4(),
            pdf_source_ids=["p1"],
            image_source_ids=[],
            user_message="msg",
            skip_processing=False,
            context_mode=CONTEXT_MODE_FULL,
            task_db_id="task-db-id",
            task_id=uuid.uuid4(),
        )

        mock_set_celery.assert_not_called()


# ── _compensate_s3 ────────────────────────────────────────────────────────────


class TestCompensateS3:
    @pytest.mark.asyncio
    async def test_schedules_and_completes_delete(self, monkeypatch):
        mock_delete = AsyncMock()
        monkeypatch.setattr("app.services.incremental_source_service.delete_from_s3", mock_delete)

        _compensate_s3("some/key")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        mock_delete.assert_awaited_once_with("some/key")

    @pytest.mark.asyncio
    async def test_delete_failure_is_swallowed(self, monkeypatch):
        mock_delete = AsyncMock(side_effect=RuntimeError("s3 down"))
        monkeypatch.setattr("app.services.incremental_source_service.delete_from_s3", mock_delete)

        # Must not raise synchronously, and the scheduled failure must not
        # propagate either.
        _compensate_s3("some/key")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    def test_uses_run_until_complete_when_no_loop_running(self, monkeypatch):
        """Covers the (rare outside a request/task context) synchronous branch."""
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        monkeypatch.setattr("asyncio.get_event_loop", MagicMock(return_value=mock_loop))

        _compensate_s3("some/key")

        mock_loop.run_until_complete.assert_called_once()

    def test_get_event_loop_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            "asyncio.get_event_loop", MagicMock(side_effect=RuntimeError("no event loop"))
        )

        # Must not raise.
        _compensate_s3("some/key")
