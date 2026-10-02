"""Unit tests for SourceService."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError as AppValidationError,
)
from app.services.source_service import (
    SourceService,
    _enqueue_processing,
    _validate_file,
    _validate_file_content_type,
    _validate_zip_is_source_code,
)
from tests.conftest import SourceFileParams, make_source

# ── _validate_file ─────────────────────────────────────────────────────────


class TestValidateFile:
    def test_valid_pdf(self):
        fmt = _validate_file("doc.pdf", "application/pdf", 1024)
        assert fmt == "PDF"

    def test_valid_csv(self):
        fmt = _validate_file("data.csv", "text/csv", 100)
        assert fmt == "CSV"

    def test_unsupported_mime_raises(self):
        with pytest.raises(AppValidationError, match="Unsupported file type"):
            _validate_file("app.exe", "application/x-executable", 100)

    def test_exceeds_max_size_raises(self):
        huge = 999 * 1024 * 1024  # well over default cap
        with pytest.raises(AppValidationError, match="exceeds the maximum"):
            _validate_file("big.pdf", "application/pdf", huge)

    def test_no_extension_returns_default(self):
        fmt = _validate_file("noext", "text/plain", 100)
        assert fmt == "UNKNOWN"


class TestValidateFileContentType:
    def test_pdf_header_mismatch_raises(self):
        with pytest.raises(AppValidationError, match="Unsupported file type"):
            _validate_file_content_type("application/pdf", b"not-a-pdf")

    def test_pdf_header_valid(self):
        content_type = _validate_file_content_type(
            "application/pdf",
            b"%PDF-1.7\n%...",
        )
        assert content_type == "application/pdf"

    def test_zip_mismatch_raises(self):
        with pytest.raises(AppValidationError, match="Unsupported file type"):
            _validate_file_content_type("application/zip", b"plain-text")


# ── _validate_zip_is_source_code ───────────────────────────────────────────


from datetime import UTC
import io as _io
import zipfile as _zipfile


def _make_zip(*filenames: str) -> bytes:
    """Build an in-memory ZIP containing empty entries for each filename."""
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as zf:
        for name in filenames:
            zf.writestr(name, b"")
    return buf.getvalue()


class TestValidateZipIsSourceCode:
    def test_passes_for_python_file(self):
        _validate_zip_is_source_code(_make_zip("project/main.py"), "project.zip")

    def test_passes_for_javascript_file(self):
        _validate_zip_is_source_code(_make_zip("src/index.js"), "app.zip")

    def test_passes_for_typescript_file(self):
        _validate_zip_is_source_code(_make_zip("src/app.ts"), "app.zip")

    def test_passes_for_java_file(self):
        _validate_zip_is_source_code(_make_zip("com/example/Main.java"), "project.zip")

    def test_passes_for_go_file(self):
        _validate_zip_is_source_code(_make_zip("cmd/main.go"), "project.zip")

    def test_passes_for_package_json_manifest(self):
        _validate_zip_is_source_code(_make_zip("package.json", "dist/bundle.js"), "node-app.zip")

    def test_passes_for_requirements_txt_manifest(self):
        _validate_zip_is_source_code(_make_zip("requirements.txt"), "py-app.zip")

    def test_passes_for_pom_xml_manifest(self):
        _validate_zip_is_source_code(_make_zip("pom.xml"), "maven-app.zip")

    def test_raises_for_zip_with_only_images(self):
        with pytest.raises(AppValidationError, match="does not appear to contain source code"):
            _validate_zip_is_source_code(
                _make_zip("photo1.jpg", "photo2.png", "thumbs/photo3.webp"),
                "photos.zip",
            )

    def test_raises_for_zip_with_only_documents(self):
        with pytest.raises(AppValidationError, match="does not appear to contain source code"):
            _validate_zip_is_source_code(
                _make_zip("report.pdf", "notes.docx", "data.xlsx"),
                "docs.zip",
            )

    def test_raises_for_empty_zip(self):
        with pytest.raises(AppValidationError, match="does not appear to contain source code"):
            _validate_zip_is_source_code(_make_zip(), "empty.zip")

    def test_does_not_raise_for_corrupt_zip(self):
        """Corrupt ZIPs should not raise — let downstream handling deal with it."""
        _validate_zip_is_source_code(b"not-a-zip", "bad.zip")


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_upload_file(
    filename: str = "test.pdf",
    content_type: str = "application/pdf",
    data: bytes = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n",
) -> MagicMock:
    """Build a mock ``UploadFile``."""
    f = AsyncMock()
    f.filename = filename
    f.content_type = content_type
    f.size = len(data)
    f.read = AsyncMock(return_value=data)
    return f


def _auto_stamp(entity: object) -> object:
    """Side-effect for ``uow.refresh`` (link flow) and ``uow.sources.create`` (bulk flow).

    Populates DB-assigned fields on *entity* in-place and returns it so the
    function works as a side-effect for both ``refresh`` (return value ignored)
    and ``sources.create`` (return value used as the persisted instance).
    """
    from datetime import datetime

    if not getattr(entity, "id", None):
        entity.id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    entity.created_at = now
    entity.updated_at = now
    # Replicate SQLAlchemy INSERT defaults that the real DB would set
    if getattr(entity, "is_deleted", None) is None:
        entity.is_deleted = False
    # provide uploader relationship for SourceResponse validator
    if hasattr(entity, "created_by"):
        uploader = MagicMock()
        uploader.id = entity.created_by
        uploader.name = "Mock User"
        entity.uploader = uploader
    return entity


# ── get_source_for_download ────────────────────────────────────────────────


class TestGetSourceForDownload:
    @pytest.mark.asyncio
    async def test_returns_source_record(self, uow):
        """Returns the ORM Source when source exists and has a storage key."""
        source = make_source(storage_key="sources/proj/file.pdf")
        uow.sources.get_by_uuid.return_value = source

        result = SourceService().get_source_for_download(source_id=source.id, uow=uow)

        assert result is source
        assert result.storage_key == "sources/proj/file.pdf"
        assert result.original_name == source.original_name

    @pytest.mark.asyncio
    async def test_not_found_raises_not_found_error(self, uow):
        uow.sources.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            SourceService().get_source_for_download(source_id=uuid.uuid4(), uow=uow)

    @pytest.mark.asyncio
    async def test_soft_deleted_raises_not_found_error(self, uow):
        source = make_source(is_deleted=True)
        uow.sources.get_by_uuid.return_value = source

        with pytest.raises(NotFoundError):
            SourceService().get_source_for_download(source_id=source.id, uow=uow)

    @pytest.mark.asyncio
    async def test_no_storage_key_raises_validation_error(self, uow):
        source = make_source(storage_key=None)
        uow.sources.get_by_uuid.return_value = source

        with pytest.raises(AppValidationError, match="no associated file"):
            SourceService().get_source_for_download(source_id=source.id, uow=uow)


# ── download_single_file_in_local ───────────────────────────────────────────


class TestDownloadSingleFileInLocal:
    @pytest.mark.asyncio
    @patch("app.services.source_service.generate_short_uuid", return_value="midabc12")
    @patch("app.services.source_service.download_from_s3", new_callable=AsyncMock)
    async def test_download_success(self, mock_download_from_s3, _mock_short_uuid, uow):
        import tempfile

        source = make_source(
            original_name="spec.pdf",
            storage_key="sources/project/spec.pdf",
            is_deleted=False,
        )
        uow.sources.get_by_uuid.return_value = source
        mock_download_from_s3.return_value = "application/pdf"

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("app.services.source_service.settings") as mock_settings:
                mock_settings.SOURCE_LOCAL_DOWNLOAD_DIR = "temp/sources"
                with patch("app.services.source_service.PROJECT_ROOT", Path(tmp_dir)):
                    (
                        local_path,
                        content_type,
                        original_name,
                    ) = await SourceService().download_single_file_in_local(
                        source_id=source.id,
                        uow=uow,
                    )

        assert content_type == "application/pdf"
        assert original_name == "spec.pdf"
        assert local_path.endswith(f"{source.id}_midabc12_spec.pdf")
        assert Path(local_path).parent == Path(tmp_dir) / "temp" / "sources"

        awaited_args = mock_download_from_s3.await_args
        assert awaited_args.args[0] == "sources/project/spec.pdf"
        assert isinstance(awaited_args.args[1], Path)
        assert awaited_args.args[1].name == f"{source.id}_midabc12_spec.pdf"

    @pytest.mark.asyncio
    async def test_download_not_found_raises(self, uow):
        uow.sources.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError, match="not found"):
            await SourceService().download_single_file_in_local(
                source_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_download_soft_deleted_raises(self, uow):
        source = make_source(is_deleted=True)
        uow.sources.get_by_uuid.return_value = source

        with pytest.raises(NotFoundError, match="not found"):
            await SourceService().download_single_file_in_local(
                source_id=source.id,
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_download_no_storage_key_raises(self, uow):
        source = make_source(storage_key=None)
        uow.sources.get_by_uuid.return_value = source

        with pytest.raises(AppValidationError, match="no associated file in storage"):
            await SourceService().download_single_file_in_local(
                source_id=source.id,
                uow=uow,
            )


# ── upload_bulk ────────────────────────────────────────────────────────────


class TestUploadBulk:
    @pytest.mark.asyncio
    @patch("app.services.source_service.generate_presigned_url", new_callable=AsyncMock)
    @patch("app.services.source_service.upload_to_s3", new_callable=AsyncMock)
    @patch("app.services.source_service.compute_sha256")
    async def test_bulk_all_succeed(self, mock_sha, mock_s3, mock_url, uow):
        mock_s3.return_value = "key"
        mock_url.return_value = "https://url"
        uow.sources.get_by_checksum.return_value = None
        uow.sources.create.side_effect = _auto_stamp
        mock_sha.side_effect = ["unique_hash_1", "unique_hash_2", "unique_hash_3"]

        files = [_make_upload_file(filename=f"f{i}.pdf") for i in range(3)]

        result = await SourceService().upload_bulk(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.total == 3
        assert result.succeeded == 3
        assert result.failed == 0

    @pytest.mark.asyncio
    @patch("app.services.source_service.generate_presigned_url", new_callable=AsyncMock)
    @patch("app.services.source_service.upload_to_s3", new_callable=AsyncMock)
    @patch("app.services.source_service.compute_sha256", return_value="hash-folder")
    async def test_bulk_folder_relative_path_is_persisted(self, mock_sha, mock_s3, mock_url, uow):
        mock_s3.return_value = "key"
        mock_url.return_value = "https://url"
        uow.sources.get_by_checksum.return_value = None
        uow.sources.create.side_effect = _auto_stamp

        file = _make_upload_file(filename="docs/specs/requirements.csv", content_type="text/csv")

        result = await SourceService().upload_bulk(
            files=[file],
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 1
        assert result.results[0].relative_path == "docs/specs/requirements.csv"
        persisted_source = uow.sources.create.call_args[0][0]
        assert persisted_source.relative_path == "docs/specs/requirements.csv"

    @pytest.mark.asyncio
    async def test_bulk_no_files_raises_validation_error(self, uow):
        with pytest.raises(AppValidationError, match="At least one file is required"):
            await SourceService().upload_bulk(
                files=[],
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                uploader_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_bulk_too_many_files_raises(self, uow):
        files = [_make_upload_file() for _ in range(999)]

        with pytest.raises(AppValidationError, match="at most"):
            await SourceService().upload_bulk(
                files=files,
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                uploader_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    async def test_source_code_upload_rejects_multiple_files(self, uow):
        files = [
            _make_upload_file(filename="first.zip", content_type="application/zip"),
            _make_upload_file(filename="second.zip", content_type="application/zip"),
        ]

        with pytest.raises(AppValidationError, match="only one file"):
            await SourceService().upload_bulk(
                files=files,
                project_id=uuid.uuid4(),
                description=None,
                source_type="source_code",
                uploader_id=uuid.uuid4(),
                uow=uow,
            )


    @pytest.mark.asyncio
    async def test_bulk_cumulative_size_exceeded_raises(self, uow):
        from app.core.config import settings
        from app.core.constants import BYTES_PER_MB

        oversized_bytes = settings.SOURCE_BULK_MAX_TOTAL_SIZE_MB * BYTES_PER_MB + 1
        files = [_make_upload_file(filename="huge.pdf")]
        files[0].size = oversized_bytes

        with pytest.raises(AppValidationError, match="exceeds the maximum"):
            await SourceService().upload_bulk(
                files=files,
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                uploader_id=uuid.uuid4(),
                uow=uow,
            )

    @pytest.mark.asyncio
    @patch("app.services.source_service.compute_sha256", return_value="dup_hash")
    async def test_bulk_duplicate_marked_as_duplicate(self, mock_sha, uow):
        existing = make_source()
        uow.sources.get_by_checksum.return_value = existing

        files = [_make_upload_file()]

        result = await SourceService().upload_bulk(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.total == 1
        assert result.succeeded == 0
        assert result.results[0].status == "duplicate"

    @pytest.mark.asyncio
    async def test_bulk_duplicate_filename_marked_as_duplicate(self, uow):
        """Same filename, different content — must still be blocked as a
        duplicate even though the checksum lookup finds nothing."""
        existing = make_source()
        uow.sources.get_by_filename.return_value = existing

        files = [_make_upload_file(filename="requirements.pdf")]

        result = await SourceService().upload_bulk(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.total == 1
        assert result.succeeded == 0
        assert result.results[0].status == "duplicate"
        assert result.results[0].source_id == existing.id

    @pytest.mark.asyncio
    @patch("app.services.source_service.generate_presigned_url", new_callable=AsyncMock)
    @patch("app.services.source_service.upload_to_s3", new_callable=AsyncMock)
    @patch("app.services.source_service.compute_sha256")
    async def test_bulk_duplicate_filename_within_batch_marked_as_duplicate(
        self, mock_sha, mock_s3, mock_url, uow
    ):
        """Two files in the same batch sharing a filename (case-insensitive)
        but with different content are both flagged — the second as a
        duplicate of the first, even though no DB row exists yet."""
        mock_s3.return_value = "key"
        mock_url.return_value = "https://url"
        uow.sources.create.side_effect = _auto_stamp
        mock_sha.side_effect = ["hash_1", "hash_2"]
        files = [
            _make_upload_file(filename="Requirements.pdf"),
            _make_upload_file(filename="requirements.pdf"),
        ]

        result = await SourceService().upload_bulk(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.total == 2
        assert result.succeeded == 1
        statuses = [r.status for r in result.results]
        assert statuses.count("uploaded") == 1
        assert statuses.count("duplicate") == 1
        dup_item = next(r for r in result.results if r.status == "duplicate")
        assert dup_item.source_id is None

    @pytest.mark.asyncio
    @patch("app.services.source_service.compute_sha256", return_value="dup_hash")
    async def test_bulk_duplicate_blocks_source_originally_uploaded_via_incremental_path(
        self, mock_sha, uow
    ):
        """Dedup is project-wide, not scoped to the upload path: a source
        created by the incremental upload flow (upload_type="bulk-incremental")
        must still block a matching regular bulk upload, since
        ``get_by_checksum``/``get_by_filename`` filter only by project_id,
        never upload_type."""
        existing = make_source(file_params=SourceFileParams(upload_type="bulk-incremental"))
        uow.sources.get_by_checksum.return_value = existing

        files = [_make_upload_file()]

        result = await SourceService().upload_bulk(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.total == 1
        assert result.succeeded == 0
        assert result.results[0].status == "duplicate"
        assert result.results[0].source_id == existing.id

    @pytest.mark.asyncio
    async def test_bulk_rejects_spoofed_pdf_content(self, uow):
        files = [
            _make_upload_file(
                filename="spoofed.pdf",
                content_type="application/pdf",
                data=b"MZ-not-a-real-pdf",
            )
        ]

        result = await SourceService().upload_bulk(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.total == 1
        assert result.succeeded == 0
        assert result.failed == 1
        assert result.results[0].status == "failed"
        assert result.results[0].error is not None
        assert "Unsupported" in result.results[0].error

    @pytest.mark.asyncio
    @patch("app.services.source_service._enqueue_processing")
    @patch("app.services.source_service.generate_presigned_url", new_callable=AsyncMock)
    @patch("app.services.source_service.upload_to_s3", new_callable=AsyncMock)
    @patch("app.services.source_service.compute_sha256")
    async def test_bulk_calls_enqueue_processing_with_uploaded_source_ids(
        self,
        mock_sha,
        mock_s3,
        mock_url,
        mock_enqueue,
        uow,
    ):
        mock_s3.return_value = "key"
        mock_url.return_value = "https://url"
        uow.sources.get_by_checksum.return_value = None
        uow.sources.create.side_effect = _auto_stamp
        mock_sha.side_effect = ["hash-a", "hash-b"]

        files = [
            _make_upload_file(filename="f1.pdf"),
            _make_upload_file(filename="f2.pdf"),
        ]
        project_id = uuid.uuid4()

        result = await SourceService().upload_bulk(
            files=files,
            project_id=project_id,
            description=None,
            source_type="rfp",
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 2
        mock_enqueue.assert_called_once()
        called_project_id, called_source_ids = mock_enqueue.call_args.args
        assert called_project_id == project_id
        assert len(called_source_ids) == 2

        expected_source_ids = {
            item.source_id for item in result.results if item.status == "uploaded"
        }
        assert set(called_source_ids) == expected_source_ids

    @pytest.mark.asyncio
    @patch("app.services.source_service._enqueue_processing")
    async def test_bulk_calls_enqueue_processing_with_empty_source_ids_on_full_validation_failure(
        self, mock_enqueue, uow
    ):
        files = [
            _make_upload_file(
                filename="bad.pdf",
                content_type="application/pdf",
                data=b"not-a-real-pdf",
            )
        ]
        project_id = uuid.uuid4()

        result = await SourceService().upload_bulk(
            files=files,
            project_id=project_id,
            description=None,
            source_type="rfp",
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 0
        assert result.failed == 1
        mock_enqueue.assert_called_once_with(
            project_id,
            [],
            user_id=mock_enqueue.call_args.kwargs["user_id"],
            skip_processing=False,
            source_type="rfp",
            request_id=mock_enqueue.call_args.kwargs["request_id"],
        )

    # ── S3 failure handling ───────────────────────────────────────────────

    @pytest.mark.asyncio
    @patch("app.services.source_service.generate_presigned_url", new_callable=AsyncMock)
    @patch("app.services.source_service.upload_to_s3", new_callable=AsyncMock)
    async def test_s3_failure_recorded_as_failed_item(self, mock_s3, mock_url, uow):
        """StorageError on S3 upload records item as failed and continues batch."""
        from app.core.exceptions import StorageError

        mock_url.return_value = "https://url"
        uow.sources.get_by_checksum.return_value = None
        uow.sources.create.side_effect = _auto_stamp
        # First file fails S3, second succeeds
        mock_s3.side_effect = [StorageError("bucket unavailable"), "key2"]

        with patch("app.services.source_service.compute_sha256") as mock_sha:
            mock_sha.side_effect = ["hash_fail", "hash_ok"]
            files = [
                _make_upload_file(filename="bad.pdf"),
                _make_upload_file(filename="good.pdf"),
            ]
            result = await SourceService().upload_bulk(
                files=files,
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                uploader_id=uuid.uuid4(),
                uow=uow,
            )

        assert result.total == 2
        assert result.succeeded == 1
        assert result.failed == 1
        failed_item = next(r for r in result.results if r.filename == "bad.pdf")
        ok_item = next(r for r in result.results if r.filename == "good.pdf")
        assert failed_item.status == "failed"
        assert failed_item.status_code == 502
        assert "bad.pdf" in failed_item.error
        assert ok_item.status == "uploaded"
        assert ok_item.status_code == 201

    @pytest.mark.asyncio
    @patch("app.services.source_service.upload_to_s3", new_callable=AsyncMock)
    @patch("app.services.source_service.compute_sha256", return_value="hash-all-fail")
    async def test_all_files_fail_persist_marks_ingestion_failed_not_left_running(
        self, mock_sha, mock_s3, uow
    ):
        """A file passing type/size/duplicate checks still leaves the ingestion
        row created (unlike a validation-only rejection); if every such file
        then fails during S3/DB persist, that ingestion must be closed out as
        failed rather than left stuck at status="running" with 0 sources.
        """
        from app.core.enums.source_ingestion_status import SourceIngestionStatus
        from app.core.exceptions import StorageError

        uow.sources.get_by_checksum.return_value = None
        mock_s3.side_effect = StorageError("bucket unavailable")

        files = [_make_upload_file(filename="a.pdf")]

        result = await SourceService().upload_bulk(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
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

    @pytest.mark.asyncio
    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    @patch("app.services.source_service.generate_presigned_url", new_callable=AsyncMock)
    @patch("app.services.source_service.upload_to_s3", new_callable=AsyncMock)
    @patch("app.services.source_service.compute_sha256")
    async def test_db_commit_failure_marks_item_failed_and_continues_batch(
        self, mock_sha, mock_s3, mock_url, mock_del, uow
    ):
        """DB commit failure should fail only that item and continue the batch."""
        mock_sha.side_effect = ["hash-fail", "hash-ok"]
        mock_s3.side_effect = ["key-fail", "key-ok"]
        mock_url.return_value = "https://url"
        uow.sources.get_by_checksum.return_value = None
        uow.sources.create.side_effect = _auto_stamp
        uow.commit.side_effect = [RuntimeError("DB connection lost"), None]

        files = [
            _make_upload_file(filename="doc-fail.pdf"),
            _make_upload_file(filename="doc-ok.pdf"),
        ]

        result = await SourceService().upload_bulk(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.total == 2
        assert result.succeeded == 1
        assert result.failed == 1
        failed_item = next(r for r in result.results if r.status_code == 500)
        ok_item = next(r for r in result.results if r.status_code == 201)
        assert failed_item.status == "failed"
        assert failed_item.status_code == 500
        assert "database error" in (failed_item.error or "").lower()
        assert ok_item.status == "uploaded"
        assert ok_item.status_code == 201

        # S3 compensation must be called for the failed DB commit item.
        mock_del.assert_awaited_once()
        uow.rollback.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.source_service.generate_presigned_url", new_callable=AsyncMock)
    @patch("app.services.source_service.upload_to_s3", new_callable=AsyncMock)
    @patch("app.services.source_service.compute_sha256", return_value="hash-url-fail")
    async def test_presigned_url_failure_is_non_fatal(self, mock_sha, mock_s3, mock_url, uow):
        """StorageError on presigned URL generation must not fail the item."""
        from app.core.exceptions import StorageError

        mock_s3.return_value = "key"
        mock_url.side_effect = StorageError("presign error")
        uow.sources.get_by_checksum.return_value = None
        uow.sources.create.side_effect = _auto_stamp

        files = [_make_upload_file(filename="ok.pdf")]

        result = await SourceService().upload_bulk(
            files=files,
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.succeeded == 1
        assert result.results[0].status == "uploaded"
        # storage_url is None but item still committed
        committed_source = uow.sources.create.call_args[0][0]
        assert committed_source.storage_url is None


class TestUploadBulkOrIncremental:
    """The is_incremental routing decision itself — the standard/incremental
    upload paths' own behavior is already covered by TestUploadBulk and
    tests/test_incremental_source_service.py."""

    @pytest.mark.asyncio
    async def test_false_routes_to_standard_upload_bulk(self, uow):
        files = [_make_upload_file(filename="f.pdf")]
        project_id = uuid.uuid4()
        uploader_id = uuid.uuid4()

        with (
            patch(
                "app.services.source_service.SourceService.upload_bulk", new=AsyncMock()
            ) as mock_upload_bulk,
            patch(
                "app.services.incremental_source_service.IncrementalBulkUploadService.upload_and_enqueue",
                new=AsyncMock(),
            ) as mock_incremental,
        ):
            await SourceService().upload_bulk_or_incremental(
                is_incremental=False,
                files=files,
                project_id=project_id,
                description="desc",
                source_type="rfp",
                context=None,
                user_message="",
                skip_processing=False,
                context_mode="full",
                uploader_id=uploader_id,
                uow=uow,
            )

        mock_upload_bulk.assert_awaited_once_with(
            files=files,
            project_id=project_id,
            description="desc",
            source_type="rfp",
            context=None,
            user_message="",
            skip_processing=False,
            uploader_id=uploader_id,
            uow=uow,
            requester_roles=None,
            requester_tenant_id=None,
        )
        mock_incremental.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_true_routes_to_incremental_service(self, uow):
        files = [_make_upload_file(filename="f.pdf")]
        project_id = uuid.uuid4()
        uploader_id = uuid.uuid4()

        with (
            patch(
                "app.services.source_service.SourceService.upload_bulk", new=AsyncMock()
            ) as mock_upload_bulk,
            patch(
                "app.services.incremental_source_service.IncrementalBulkUploadService.upload_and_enqueue",
                new=AsyncMock(),
            ) as mock_incremental,
        ):
            await SourceService().upload_bulk_or_incremental(
                is_incremental=True,
                files=files,
                project_id=project_id,
                description="desc",
                source_type="rfp",
                context=None,
                user_message="context",
                skip_processing=True,
                context_mode="subset",
                uploader_id=uploader_id,
                uow=uow,
                requester_roles=["member"],
                requester_tenant_id=None,
            )

        mock_incremental.assert_awaited_once_with(
            files=files,
            project_id=project_id,
            description="desc",
            source_type="rfp",
            user_message="context",
            skip_processing=True,
            context_mode="subset",
            uploader_id=uploader_id,
            uow=uow,
            requester_roles=["member"],
            requester_tenant_id=None,
        )
        mock_upload_bulk.assert_not_awaited()


class TestEnqueueProcessing:
    @patch("app.workers.process_source_tasks.process_source_task.apply_async")
    @patch("app.services.project_task_service.ProjectTaskService.set_celery_task_id")
    @patch("app.services.project_task_service.ProjectTaskService.create_task")
    @patch("app.services.source_ingestion_service.SourceIngestionService.add_stage_by_source_ids")
    @patch("app.services.source_service.UnitOfWork")
    def test_forwards_skip_processing_to_dispatch_task(
        self,
        mock_uow_cls,
        mock_add_stage_by_source_ids,
        mock_create_task,
        mock_set_celery_task_id,
        mock_apply_async,
    ):
        task_id = uuid.uuid4()
        task_db_id = str(uuid.uuid4())
        project_id = uuid.uuid4()
        source_id = uuid.uuid4()
        user_id = uuid.uuid4()

        stage_uow = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = stage_uow
        cm.__exit__.return_value = None
        mock_uow_cls.return_value = cm

        mock_create_task.return_value = (task_id, task_db_id)
        mock_apply_async.return_value = MagicMock(id="celery-task-1")

        _enqueue_processing(
            project_id,
            [source_id],
            user_id=user_id,
            skip_processing=True,
        )

        mock_apply_async.assert_called_once_with(
            args=[str(project_id), [str(source_id)], task_db_id, True, None]
        )
        mock_set_celery_task_id.assert_called_once_with(task_id, "celery-task-1")

    @patch("app.workers.process_source_tasks.process_source_task.apply_async")
    @patch("app.services.project_task_service.ProjectTaskService.set_celery_task_id")
    @patch("app.services.project_task_service.ProjectTaskService.create_task")
    @patch("app.services.source_ingestion_service.SourceIngestionService.add_stage_by_source_ids")
    @patch("app.services.source_service.UnitOfWork")
    def test_source_code_upload_tags_ingestion_with_ingesting_sources_stage(
        self,
        mock_uow_cls,
        mock_add_stage_by_source_ids,
        mock_create_task,
        mock_set_celery_task_id,
        mock_apply_async,
    ):
        """source_code uploads use their own SourceIngestionStage.INGESTING_SOURCES
        checkpoint instead of the RFP-family module_feature/user_story stage
        values — the source-code pipeline has no use for those two
        RFP-specific phase markers."""
        from app.core.enums.source_ingestion_stage import SourceIngestionStage

        task_id = uuid.uuid4()
        task_db_id = str(uuid.uuid4())
        project_id = uuid.uuid4()
        source_id = uuid.uuid4()

        stage_uow = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = stage_uow
        cm.__exit__.return_value = None
        mock_uow_cls.return_value = cm

        mock_create_task.return_value = (task_id, task_db_id)
        mock_apply_async.return_value = MagicMock(id="celery-task-1")

        _enqueue_processing(
            project_id,
            [source_id],
            user_id=uuid.uuid4(),
            source_type="source_code",
        )

        assert mock_add_stage_by_source_ids.call_args_list == [
            ((stage_uow, [source_id], SourceIngestionStage.INGESTING_SOURCES), {}),
        ]

    @patch("app.workers.process_source_tasks.process_source_task.apply_async")
    @patch("app.services.project_task_service.ProjectTaskService.set_celery_task_id")
    @patch("app.services.project_task_service.ProjectTaskService.create_task")
    @patch("app.services.source_ingestion_service.SourceIngestionService.add_stage_by_source_ids")
    @patch("app.services.source_service.UnitOfWork")
    def test_non_source_code_upload_tags_ingestion_with_module_feature_stage(
        self,
        mock_uow_cls,
        mock_add_stage_by_source_ids,
        mock_create_task,
        mock_set_celery_task_id,
        mock_apply_async,
    ):
        """RFP-family uploads keep the pre-existing
        SourceIngestionStage.GENERATING_MODULE_FEATURE tagging, unaffected by
        the source_code-only INGESTING_SOURCES change."""
        from app.core.enums.source_ingestion_stage import SourceIngestionStage

        task_id = uuid.uuid4()
        task_db_id = str(uuid.uuid4())
        project_id = uuid.uuid4()
        source_id = uuid.uuid4()

        stage_uow = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = stage_uow
        cm.__exit__.return_value = None
        mock_uow_cls.return_value = cm

        mock_create_task.return_value = (task_id, task_db_id)
        mock_apply_async.return_value = MagicMock(id="celery-task-1")

        _enqueue_processing(
            project_id,
            [source_id],
            user_id=uuid.uuid4(),
            source_type="rfp",
        )

        assert mock_add_stage_by_source_ids.call_args_list == [
            ((stage_uow, [source_id], SourceIngestionStage.GENERATING_MODULE_FEATURE), {}),
        ]

    @patch("app.workers.process_source_tasks.process_source_task.apply_async")
    @patch("app.services.project_task_service.ProjectTaskService.set_celery_task_id")
    @patch("app.services.project_task_service.ProjectTaskService.create_task")
    @patch("app.services.source_ingestion_service.SourceIngestionService.add_stage_by_source_ids")
    @patch("app.services.source_service.UnitOfWork")
    def test_marks_project_task_failed_when_dispatch_fails(
        self,
        mock_uow_cls,
        mock_add_stage_by_source_ids,
        mock_create_task,
        mock_set_celery_task_id,
        mock_apply_async,
    ):
        task_id = uuid.uuid4()
        project_id = uuid.uuid4()
        source_id = uuid.uuid4()

        mock_create_task.return_value = (task_id, str(task_id))
        mock_apply_async.side_effect = RuntimeError("broker unavailable")

        fail_uow = MagicMock()
        fail_uow.project_tasks.update_status.return_value = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = fail_uow
        cm.__exit__.return_value = None
        mock_uow_cls.return_value = cm

        _enqueue_processing(project_id, [source_id], user_id=uuid.uuid4())

        fail_uow.project_tasks.update_status.assert_called_once_with(
            task_id,
            status="failed",
            stage="source.process.enqueue_failed",
            error="broker unavailable",
        )
        fail_uow.task_events.record.assert_called_once()
        # commit is called twice: once for the stage-tagging step, and once
        # when the exception handler records the failed task status.
        assert fail_uow.commit.call_count == 2
        mock_set_celery_task_id.assert_not_called()


# ── upload_from_link ───────────────────────────────────────────────────────


class TestUploadFromLink:
    @pytest.mark.asyncio
    @patch("app.services.source_service.generate_presigned_url", new_callable=AsyncMock)
    @patch("app.services.source_service.upload_to_s3", new_callable=AsyncMock)
    @patch("app.services.source_service.compute_sha256", return_value="link_sha")
    @patch("app.utils.link_downloader.download_from_url", new_callable=AsyncMock)
    async def test_upload_link_success(self, mock_download, mock_sha, mock_s3, mock_url, uow):
        from app.utils.link_downloader import DownloadedFile

        mock_download.return_value = DownloadedFile(
            filename="repo-HEAD.zip",
            content_type="application/zip",
            file_type="ZIP",
            data=b"zip-bytes",
            is_archive=True,
        )
        mock_s3.return_value = "key"
        mock_url.return_value = "https://url"
        uow.sources.get_by_checksum.return_value = None
        uow.refresh.side_effect = _auto_stamp

        result = await SourceService().upload_from_link(
            link_url="https://github.com/owner/repo",
            project_id=uuid.uuid4(),
            description=None,
            source_type="rfp",
            uploader_id=uuid.uuid4(),
            uow=uow,
        )

        assert result.original_name == "repo_HEAD.zip"
        assert result.file_type == "ZIP"

    @pytest.mark.asyncio
    @patch("app.services.source_service.compute_sha256", return_value="link_sha")
    @patch("app.utils.link_downloader.download_from_url", new_callable=AsyncMock)
    async def test_upload_link_duplicate_raises_conflict(self, mock_download, mock_sha, uow):
        from app.utils.link_downloader import DownloadedFile

        mock_download.return_value = DownloadedFile(
            filename="file.pdf",
            content_type="application/pdf",
            file_type="PDF",
            data=b"data",
            is_archive=False,
        )
        existing = make_source()
        uow.sources.get_by_checksum.return_value = existing

        with pytest.raises(ConflictError, match="identical file"):
            await SourceService().upload_from_link(
                link_url="https://example.com/file.pdf",
                project_id=uuid.uuid4(),
                description=None,
                source_type="rfp",
                uploader_id=uuid.uuid4(),
                uow=uow,
            )


# ── get_source ─────────────────────────────────────────────────────────────


class TestGetSource:
    def test_get_success(self, uow):
        source = make_source()
        uow.sources.get_by_uuid.return_value = source

        result = SourceService().get_source(source.id, uow)

        assert result.id == source.id

    def test_get_not_found(self, uow):
        uow.sources.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            SourceService().get_source(uuid.uuid4(), uow)


# ── list_sources ───────────────────────────────────────────────────────────


class TestListSources:
    def test_list_returns_paginated(self, uow):
        sources = [make_source() for _ in range(3)]
        uow.sources.get_paginated.return_value = (sources, 3)

        result = SourceService().list_sources(
            project_id=uuid.uuid4(),
            skip=0,
            limit=10,
            status=None,
            file_type=None,
            upload_type=None,
            uow=uow,
        )

        assert result.total == 3
        assert len(result.items) == 3

    def test_list_with_filters(self, uow):
        uow.sources.get_paginated.return_value = ([], 0)

        SourceService().list_sources(
            project_id=uuid.uuid4(),
            skip=0,
            limit=10,
            status="uploaded",
            file_type="PDF",
            upload_type="single",
            uow=uow,
        )

        call_kwargs = uow.sources.get_paginated.call_args.kwargs
        assert call_kwargs["status"] == "uploaded"
        assert call_kwargs["file_type"] == "PDF"
        assert call_kwargs["upload_type"] == "single"


# ── delete_source ──────────────────────────────────────────────────────────


class TestDeleteSource:
    @pytest.mark.asyncio
    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    async def test_delete_by_uploader(self, mock_s3_del, uow):
        uploader_id = uuid.uuid4()
        source = make_source(created_by=uploader_id, storage_key="sources/proj/file.pdf")
        uow.sources.get_by_uuid.return_value = source

        await SourceService().delete_source(
            source_id=source.id,
            requester_id=uploader_id,
            requester_roles=["pm"],
            uow=uow,
        )

        assert source.is_deleted is True
        assert source.status == "uploaded"
        uow.commit.assert_called_once()
        mock_s3_del.assert_awaited_once_with("sources/proj/file.pdf")

    @pytest.mark.asyncio
    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    async def test_delete_by_admin(self, mock_s3_del, uow):
        source = make_source(storage_key="sources/proj/file.pdf")
        uow.sources.get_by_uuid.return_value = source

        await SourceService().delete_source(
            source_id=source.id,
            requester_id=uuid.uuid4(),
            requester_roles=["admin"],
            uow=uow,
        )

        assert source.is_deleted is True
        mock_s3_del.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    async def test_delete_not_found(self, mock_s3_del, uow):
        uow.sources.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            await SourceService().delete_source(
                source_id=uuid.uuid4(),
                requester_id=uuid.uuid4(),
                requester_roles=[],
                uow=uow,
            )
        mock_s3_del.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    async def test_delete_forbidden(self, mock_s3_del, uow):
        source = make_source()
        uow.sources.get_by_uuid.return_value = source
        # Not the project owner either — otherwise the owner bypass in
        # ProjectService.assert_project_access would let this through.
        uow.projects.get_by_uuid.return_value = MagicMock(owner_id=uuid.uuid4(), tenant_id=None)

        with pytest.raises(ForbiddenError):
            await SourceService().delete_source(
                source_id=source.id,
                requester_id=uuid.uuid4(),  # not the uploader
                requester_roles=["pm"],
                uow=uow,
            )
        mock_s3_del.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    async def test_s3_failure_propagates(self, mock_s3_del, uow):
        uploader_id = uuid.uuid4()
        source = make_source(created_by=uploader_id, storage_key="sources/proj/file.pdf")
        uow.sources.get_by_uuid.return_value = source
        mock_s3_del.side_effect = RuntimeError("S3 unavailable")

        with pytest.raises(RuntimeError, match="S3 unavailable"):
            await SourceService().delete_source(
                source_id=source.id,
                requester_id=uploader_id,
                requester_roles=["pm"],
                uow=uow,
            )
        assert source.is_deleted is True
        uow.commit.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    async def test_no_storage_key_skips_s3_delete(self, mock_s3_del, uow):
        uploader_id = uuid.uuid4()
        source = make_source(created_by=uploader_id, storage_key=None)
        uow.sources.get_by_uuid.return_value = source

        await SourceService().delete_source(
            source_id=source.id,
            requester_id=uploader_id,
            requester_roles=["pm"],
            uow=uow,
        )
        mock_s3_del.assert_not_awaited()


# ── bulk_delete_sources ────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestBulkDeleteSources:
    async def _call(self, uow, source_ids, requester_id=None, roles=None):
        if requester_id is None:
            requester_id = uuid.uuid4()
        return await SourceService().bulk_delete_sources(
            source_ids=source_ids,
            requester_id=requester_id,
            requester_roles=roles or ["pm"],
            uow=uow,
        )

    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    async def test_deletes_owned_sources(self, mock_s3_del, uow):
        requester_id = uuid.uuid4()
        sources = [make_source(created_by=requester_id, storage_key=f"s/{i}") for i in range(3)]
        uow.sources.get_by_uuid.side_effect = sources

        result = await self._call(uow, [s.id for s in sources], requester_id=requester_id)

        assert result.total == 3
        assert result.succeeded == 3
        assert result.failed == 0
        for source in sources:
            assert source.is_deleted is True
            assert source.status == "uploaded"
        assert mock_s3_del.await_count == 3

    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    async def test_admin_can_delete_any_source(self, mock_s3_del, uow):
        source = make_source(created_by=uuid.uuid4(), storage_key="s/key")
        uow.sources.get_by_uuid.return_value = source

        result = await self._call(uow, [source.id], roles=["admin"])

        assert result.succeeded == 1
        assert source.is_deleted is True
        mock_s3_del.assert_awaited_once_with("s/key")

    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    async def test_not_found_recorded_as_failure(self, mock_s3_del, uow):
        uow.sources.get_by_uuid.return_value = None
        missing_id = uuid.uuid4()

        result = await self._call(uow, [missing_id])

        assert result.total == 1
        assert result.succeeded == 0
        assert result.failed == 1
        assert result.results[0].status == "not_found"
        assert result.results[0].status_code == 404
        mock_s3_del.assert_not_awaited()

    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    async def test_forbidden_recorded_as_failure(self, mock_s3_del, uow):
        source = make_source(created_by=uuid.uuid4())
        uow.sources.get_by_uuid.return_value = source
        # Not the project owner either — otherwise the owner bypass in
        # ProjectService.assert_project_access would let this through.
        uow.projects.get_by_uuid.return_value = MagicMock(owner_id=uuid.uuid4(), tenant_id=None)

        result = await self._call(uow, [source.id], requester_id=uuid.uuid4(), roles=["pm"])

        assert result.succeeded == 0
        assert result.failed == 1
        assert result.results[0].status == "forbidden"
        assert result.results[0].status_code == 403
        mock_s3_del.assert_not_awaited()

    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    async def test_partial_success(self, mock_s3_del, uow):
        requester_id = uuid.uuid4()
        owned = make_source(created_by=requester_id, storage_key="s/owned")
        other = make_source(created_by=uuid.uuid4())
        uow.sources.get_by_uuid.side_effect = [owned, other]
        # `other` isn't uploaded by requester_id, so bulk_delete_sources falls
        # through to the project-access check for it — make sure it's not
        # the project owner either, otherwise the owner bypass in
        # ProjectService.assert_project_access would let it through too.
        uow.projects.get_by_uuid.return_value = MagicMock(owner_id=uuid.uuid4(), tenant_id=None)

        result = await self._call(uow, [owned.id, other.id], requester_id=requester_id)

        assert result.total == 2
        assert result.succeeded == 1
        assert result.failed == 1
        assert result.results[0].status == "deleted"
        assert result.results[1].status == "forbidden"
        mock_s3_del.assert_awaited_once_with("s/owned")

    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    async def test_commit_called_per_deleted_source(self, mock_s3_del, uow):
        requester_id = uuid.uuid4()
        sources = [make_source(created_by=requester_id) for _ in range(2)]
        uow.sources.get_by_uuid.side_effect = sources

        await self._call(uow, [s.id for s in sources], requester_id=requester_id)

        assert uow.commit.call_count == 2

    @patch("app.services.source_service.delete_from_s3", new_callable=AsyncMock)
    async def test_s3_failure_propagates(self, mock_s3_del, uow):
        requester_id = uuid.uuid4()
        source = make_source(created_by=requester_id, storage_key="s/key")
        uow.sources.get_by_uuid.return_value = source
        mock_s3_del.side_effect = RuntimeError("S3 unavailable")

        with pytest.raises(RuntimeError, match="S3 unavailable"):
            await self._call(uow, [source.id], requester_id=requester_id)
        assert source.is_deleted is True
        uow.commit.assert_called_once()
