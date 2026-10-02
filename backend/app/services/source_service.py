"""Business logic for the Source domain.

Rules
─────
- All validation and invariant checks live here, not in the route handler.
- Service methods receive ``UnitOfWork`` — never a raw Session.
- S3 upload happens *before* the DB row is committed so the row is only
  written when storage has already succeeded.
- Stateless: instantiate at the call-site.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import io
from pathlib import Path
import uuid
from uuid import UUID

from fastapi import UploadFile, status as http_status

from app.clients.s3_client import (
    compute_sha256,
    delete_from_s3,
    derive_s3_key,
    download_from_s3,
    generate_presigned_url,
    upload_to_s3,
)
from app.core.config import settings
from app.core.constants import (
    BYTES_PER_MB,
    SOURCE_ALLOWED_MIME_TYPES,
    SOURCE_CODE_EXTENSIONS,
    SOURCE_DEFAULT_CONTENT_TYPE,
    SOURCE_DEFAULT_FILENAME,
    SOURCE_DEFAULT_FORMAT,
    SOURCE_MANIFEST_NAMES,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_QUEUED,
    SOURCE_STATUS_UPLOADED,
    SOURCE_TYPE_SOURCE_CODE,
    SOURCE_UPLOAD_BULK,
    SOURCE_UPLOAD_LINK,
    SOURCE_ZIP_MIME_TYPES,
)
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    StorageError,
    ValidationError as AppValidationError,
)
from app.core.messages import (
    MSG_PROJECT_NOT_FOUND,
    MSG_SOURCE_BULK_DUPLICATE,
    MSG_SOURCE_BULK_DUPLICATE_FILENAME,
    MSG_SOURCE_BULK_FILES_REQUIRED,
    MSG_SOURCE_BULK_NO_SOURCES_UPLOADED,
    MSG_SOURCE_BULK_TOO_MANY,
    MSG_SOURCE_BULK_TOTAL_TOO_LARGE,
    MSG_SOURCE_CODE_SINGLE_FILE,
    MSG_SOURCE_DELETE_FORBIDDEN,
    MSG_SOURCE_DUPLICATE,
    MSG_SOURCE_FILE_TOO_LARGE,
    MSG_SOURCE_NO_STORAGE_KEY,
    MSG_SOURCE_NOT_FOUND,
    MSG_SOURCE_STORAGE_FAILED,
    MSG_SOURCE_UNSUPPORTED_TYPE,
    MSG_SOURCE_ZIP_NOT_SOURCE_CODE,
)
from app.db.neo4j import get_neo4j_driver
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.source_model import Source
from app.repositories.neo4j.source_repository import SourceRepository as Neo4jSourceRepository
from app.schemas.source_schema import (
    BulkDeleteItemResult,
    BulkDeleteResponse,
    BulkUploadItemResult,
    BulkUploadResponse,
    IncrementalBulkUploadResponse,
    SourceListResponse,
    SourceResponse,
)
from app.services.project_service import ProjectService
from app.utils.common import generate_short_uuid, normalize_filename
from app.utils.logger import get_logger

logger = get_logger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_MAX_FILE_SIZE_BYTES = settings.SOURCE_MAX_FILE_SIZE_MB * BYTES_PER_MB
_BULK_ITEM_STATUS_DUPLICATE = "duplicate"
_DELETE_ITEM_STATUS_DELETED = "deleted"


def _assert_source_access(
    project_id: UUID,
    requester_id: UUID | None,
    requester_roles: list[str] | None,
    requester_tenant_id: UUID | None,
    level: str,
    uow: UnitOfWork,
) -> None:
    """Enforce project-content access for a source-related operation.

    ``requester_id is None`` means an internal/worker caller (e.g. the
    document parser pulling a file for AI processing) rather than an HTTP
    request — those call sites have no requester to check and are trusted by
    construction, so this is a no-op in that case.
    """
    if requester_id is None:
        return
    project = uow.projects.get_by_uuid(project_id)
    if project is None:
        raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
    ProjectService.assert_project_access(
        project=project,
        requester_id=requester_id,
        requester_roles=requester_roles or [],
        requester_tenant_id=requester_tenant_id,
        level=level,
        uow=uow,
    )


def _enqueue_processing(
    project_id: UUID,
    source_ids: list[UUID],
    user_id: UUID | None = None,
    skip_processing: bool = False,
    source_type: str | None = None,
    request_id: UUID | None = None,
) -> None:
    """Fire-and-forget: create a ProjectTask row, then enqueue process_source_task.

    Import is deferred to avoid a circular-import cycle at module load time
    (tasks → worker → celery → config → services).
    Failures are logged but never re-raised — the upload response must not
    be affected by a transient Celery/broker unavailability.

    *source_type* == "source_code" tags the ingestion with its own
    ``ingesting_sources`` checkpoint stage instead of the RFP-family
    ``module_feature``/``user_story`` stage values (both are
    ``SourceIngestionStage`` members) — the source-code pipeline generates
    modules, features, and user stories in one automated pass with no manual
    approval gate, so it has no use for those two RFP-specific phase markers.

    *request_id* is the bulk-upload batch id shared by every task/subtask
    this request fans out to — used for cooperative cancellation. Defaults
    to the new task's own id (via ``create_task``) when not provided.
    """
    if not source_ids:
        return

    task_id: UUID | None = None
    source_id_strs = [str(sid) for sid in source_ids]

    try:
        from app.core.enums.source_ingestion_stage import SourceIngestionStage  # noqa: PLC0415
        from app.services.project_task_service import (  # noqa: PLC0415
            CreateTaskParams,
            ProjectTaskService,
        )
        from app.services.source_ingestion_service import SourceIngestionService  # noqa: PLC0415
        from app.workers.process_source_tasks import process_source_task  # noqa: PLC0415

        with UnitOfWork() as stage_uow:
            if source_type == SOURCE_TYPE_SOURCE_CODE:
                SourceIngestionService.add_stage_by_source_ids(
                    stage_uow, source_ids, SourceIngestionStage.INGESTING_SOURCES
                )
            else:
                SourceIngestionService.add_stage_by_source_ids(
                    stage_uow, source_ids, SourceIngestionStage.GENERATING_MODULE_FEATURE
                )
            stage_uow.commit()

        task_service = ProjectTaskService()

        task_id, task_db_id = task_service.create_task(
            project_id=project_id,
            user_id=user_id,
            params=CreateTaskParams(
                task_type="source_process",
                status=SOURCE_STATUS_QUEUED,
                stage="source.process.queued",
                meta={"source_ids": source_id_strs},
            ),
            request_id=request_id,
        )

        async_result = process_source_task.apply_async(
            args=[
                str(project_id),
                source_id_strs,
                task_db_id,
                skip_processing,
                str(request_id) if request_id is not None else None,
            ]
        )

        task_service.set_celery_task_id(task_id, async_result.id)

        logger.info(
            "Enqueued process_source_task: project_id=%s source_count=%d task_id=%s task_db_id=%s skip_processing=%s",
            project_id,
            len(source_ids),
            async_result.id,
            task_db_id,
            skip_processing,
        )
    except Exception as exc:
        if task_id is not None:
            try:
                with UnitOfWork() as uow:
                    updated = uow.project_tasks.update_status(
                        task_id,
                        status=SOURCE_STATUS_FAILED,
                        stage="source.process.enqueue_failed",
                        error=str(exc),
                    )
                    if updated is not None:
                        uow.task_events.record(
                            task_id=task_id,
                            project_id=project_id,
                            task_type="source_process",
                            status=SOURCE_STATUS_FAILED,
                            progress=0,
                            stage="source.process.enqueue_failed",
                            meta={"source_ids": source_id_strs},
                            error=str(exc),
                        )
                    uow.commit()
            except Exception:
                logger.warning(
                    "Failed to mark enqueue error on ProjectTask for task_id=%s",
                    task_id,
                    exc_info=True,
                )

        logger.error(
            "Failed to enqueue processing for project_id=%s source_count=%d: %s",
            project_id,
            len(source_ids),
            exc,
            exc_info=True,
        )


@dataclass(slots=True)
class _PreparedSourceUpload:
    filename: str
    relative_path: str | None
    content_type: str
    file_type: str
    checksum: str
    data: bytes


@dataclass(slots=True)
class _SourceIngestionContextFields:
    """Optional stack / standard metadata captured at bulk-upload time.

    Only persisted onto the batch's SourceIngestion when source_type is
    "source_code" — see SourceIngestionRepository.create_ingestion.
    """

    source_language: str | None = None
    frontend_stack: str | None = None
    backend_stack: str | None = None
    infrastructure_stack: str | None = None
    architecture_stack: str | None = None
    database_stack: str | None = None
    coding_standard: str | None = None
    database_strategy: str | None = None
    architecture: str | None = None
    security: str | None = None
    source_layout_type: str | None = None


SourceIngestionContextFields = _SourceIngestionContextFields


def _validate_zip_is_source_code(data: bytes, filename: str) -> None:
    """Raise ``AppValidationError`` when *data* is a ZIP but contains no
    source-code files.

    Inspects only the ZIP central-directory (no file extraction) so this is
    fast regardless of archive size.
    """
    import zipfile

    if not zipfile.is_zipfile(io.BytesIO(data)):
        # Let the rest of the pipeline handle corrupt ZIPs naturally.
        return

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()

    for name in names:
        lower = name.lower()
        # Check by extension
        if "." in lower:
            ext = "." + lower.rsplit(".", 1)[-1]
            if ext in SOURCE_CODE_EXTENSIONS:
                return
        # Check by manifest file name (basename only)
        basename = lower.rsplit("/", 1)[-1]
        if basename in SOURCE_MANIFEST_NAMES:
            return

    raise AppValidationError(MSG_SOURCE_ZIP_NOT_SOURCE_CODE.format(filename=filename))


def _validate_file(filename: str, content_type: str, size: int) -> str:
    """Validate MIME type and file size; return the derived format label."""
    if content_type not in SOURCE_ALLOWED_MIME_TYPES:
        raise AppValidationError(
            MSG_SOURCE_UNSUPPORTED_TYPE.format(
                content_type=content_type,
                allowed=", ".join(sorted(SOURCE_ALLOWED_MIME_TYPES)),
            )
        )
    if size > _MAX_FILE_SIZE_BYTES:
        raise AppValidationError(
            MSG_SOURCE_FILE_TOO_LARGE.format(
                filename=filename,
                max_mb=settings.SOURCE_MAX_FILE_SIZE_MB,
            )
        )
    ext = filename.rsplit(".", 1)[-1].upper() if "." in filename else SOURCE_DEFAULT_FORMAT
    return ext


def _raise_unsupported_source_type(content_type: str) -> None:
    raise AppValidationError(
        MSG_SOURCE_UNSUPPORTED_TYPE.format(
            content_type=content_type,
            allowed=", ".join(sorted(SOURCE_ALLOWED_MIME_TYPES)),
        )
    )


def _is_valid_zip_payload(data: bytes) -> bool:
    import zipfile

    return zipfile.is_zipfile(io.BytesIO(data))


def _validate_file_content_type(content_type: str, data: bytes) -> str:
    """Validate client-declared MIME against file content and return normalized MIME.

    FastAPI's ``UploadFile.content_type`` is client supplied, so this adds
    lightweight server-side checks for common binary formats used by source uploads.
    """
    normalized = (content_type or SOURCE_DEFAULT_CONTENT_TYPE).split(";", 1)[0].strip().lower()

    if normalized in SOURCE_ZIP_MIME_TYPES and not _is_valid_zip_payload(data):
        _raise_unsupported_source_type(content_type)

    validators = {
        "application/pdf": lambda b: b.startswith(b"%PDF-"),
        "image/png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": lambda b: b.startswith(b"\xff\xd8\xff") and b.endswith(b"\xff\xd9"),
        "image/jpg": lambda b: b.startswith(b"\xff\xd8\xff") and b.endswith(b"\xff\xd9"),
        "image/webp": lambda b: len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP",
    }

    validator = validators.get(normalized)
    if validator is not None and not validator(data):
        _raise_unsupported_source_type(content_type)

    return normalized


def _split_upload_name(filename: str) -> tuple[str, str | None]:
    """Split client-uploaded filename into basename and relative folder path."""
    normalized = filename.replace("\\", "/").strip("/")
    if not normalized:
        return SOURCE_DEFAULT_FILENAME, None

    parts = [part for part in normalized.split("/") if part and part not in {".", ".."}]
    if not parts:
        return SOURCE_DEFAULT_FILENAME, None

    if len(parts) == 1:
        return parts[0], None

    relative_path = "/".join(parts)
    return parts[-1], relative_path


class SourceService:
    def _validate_bulk_upload_request(
        self,
        *,
        files: list[UploadFile],
        project_id: UUID,
        source_type: str,
        uow: UnitOfWork,
        requester_id: UUID | None = None,
        requester_roles: list[str] | None = None,
        requester_tenant_id: UUID | None = None,
    ) -> None:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        if requester_id is not None:
            ProjectService.assert_project_access(
                project=project,
                requester_id=requester_id,
                requester_roles=requester_roles or [],
                requester_tenant_id=requester_tenant_id,
                level="write",
                uow=uow,
            )
        ProjectService.assert_llm_api_key_configured(project, uow)

        if not files:
            raise AppValidationError(MSG_SOURCE_BULK_FILES_REQUIRED)

        if source_type == SOURCE_TYPE_SOURCE_CODE and len(files) > 1:
            raise AppValidationError(
                MSG_SOURCE_CODE_SINGLE_FILE.format(received=len(files))
            )

        max_files = settings.SOURCE_BULK_MAX_FILES
        if len(files) > max_files:
            raise AppValidationError(
                MSG_SOURCE_BULK_TOO_MANY.format(max_files=max_files, received=len(files))
            )

        # Cumulative size guard — independent of the per-file cap enforced later
        # in ``_validate_file``. Without this, ``max_files`` x per-file max could
        # still allow a single multi-gigabyte request (resource-exhaustion risk).
        # ``UploadFile.size`` is populated by Starlette's multipart parser before
        # any bytes are read, so this check is essentially free.
        max_total_bytes = settings.SOURCE_BULK_MAX_TOTAL_SIZE_MB * BYTES_PER_MB
        total_size = sum(file.size or 0 for file in files)
        if total_size > max_total_bytes:
            raise AppValidationError(
                MSG_SOURCE_BULK_TOTAL_TOO_LARGE.format(
                    max_mb=settings.SOURCE_BULK_MAX_TOTAL_SIZE_MB,
                )
            )

    async def _prepare_bulk_uploads(
        self,
        *,
        files: list[UploadFile],
        project_id: UUID,
        batch_id: uuid.UUID,
        uow: UnitOfWork,
    ) -> tuple[list[BulkUploadItemResult], list[_PreparedSourceUpload]]:
        results: list[BulkUploadItemResult] = []
        prepared_uploads: list[_PreparedSourceUpload] = []
        seen_checksums: set[str] = set()
        seen_filenames: set[str] = set()

        for file in files:
            raw_name = file.filename or SOURCE_DEFAULT_FILENAME
            filename, relative_path = _split_upload_name(raw_name)
            filename = normalize_filename(filename)
            raw = await file.read()

            validation_result = self._validate_and_prepare_bulk_file(
                filename=filename,
                relative_path=relative_path,
                raw=raw,
                content_type=file.content_type or SOURCE_DEFAULT_CONTENT_TYPE,
                project_id=project_id,
                batch_id=batch_id,
                seen_checksums=seen_checksums,
                seen_filenames=seen_filenames,
                uow=uow,
            )
            if isinstance(validation_result, BulkUploadItemResult):
                results.append(validation_result)
                continue

            prepared_uploads.append(validation_result)

        return results, prepared_uploads

    def _validate_and_prepare_bulk_file(
        self,
        *,
        filename: str,
        relative_path: str | None,
        raw: bytes,
        content_type: str,
        project_id: UUID,
        batch_id: uuid.UUID,
        seen_checksums: set[str],
        seen_filenames: set[str],
        uow: UnitOfWork,
    ) -> _PreparedSourceUpload | BulkUploadItemResult:
        # Validate per-file — catch errors so a single bad file never
        # aborts the rest of the batch. Failures are recorded as
        # status="failed" entries in the bulk response.
        try:
            normalized_content_type = _validate_file_content_type(
                content_type=content_type,
                data=raw,
            )
            file_type = _validate_file(filename, normalized_content_type, len(raw))
            if normalized_content_type in SOURCE_ZIP_MIME_TYPES:
                _validate_zip_is_source_code(raw, filename)
        except AppValidationError as exc:
            logger.warning(
                "Bulk upload validation failed: batch=%s file='%s' error=%s",
                batch_id,
                filename,
                exc,
            )
            return BulkUploadItemResult(
                filename=filename,
                relative_path=relative_path,
                status=SOURCE_STATUS_FAILED,
                status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
                error=str(exc),
            )

        existing_by_name = uow.sources.get_by_filename(filename, project_id)
        if existing_by_name is not None or filename.lower() in seen_filenames:
            logger.info(
                "Bulk upload filename duplicate skipped: batch=%s file='%s' existing_source_id=%s",
                batch_id,
                filename,
                existing_by_name.id if existing_by_name is not None else "(in-batch duplicate)",
            )
            return BulkUploadItemResult(
                filename=filename,
                relative_path=relative_path,
                status=_BULK_ITEM_STATUS_DUPLICATE,
                status_code=http_status.HTTP_409_CONFLICT,
                source_id=existing_by_name.id if existing_by_name is not None else None,
                error=MSG_SOURCE_BULK_DUPLICATE_FILENAME.format(filename=filename),
            )

        checksum = compute_sha256(raw)
        existing = uow.sources.get_by_checksum(checksum, project_id)
        if existing is not None or checksum in seen_checksums:
            logger.info(
                "Bulk upload duplicate skipped: batch=%s file='%s' existing_source_id=%s",
                batch_id,
                filename,
                existing.id if existing is not None else "(in-batch duplicate)",
            )
            return BulkUploadItemResult(
                filename=filename,
                relative_path=relative_path,
                status=_BULK_ITEM_STATUS_DUPLICATE,
                status_code=http_status.HTTP_409_CONFLICT,
                source_id=existing.id if existing is not None else None,
                error=MSG_SOURCE_BULK_DUPLICATE,
            )

        seen_checksums.add(checksum)
        seen_filenames.add(filename.lower())
        return _PreparedSourceUpload(
            filename=filename,
            relative_path=relative_path,
            content_type=normalized_content_type,
            file_type=file_type,
            checksum=checksum,
            data=raw,
        )

    async def _persist_prepared_uploads(
        self,
        *,
        prepared_uploads: list[_PreparedSourceUpload],
        project_id: UUID,
        batch_id: uuid.UUID,
        source_ingestion_id: UUID,
        source_type: str,
        uploader_id: UUID,
        uow: UnitOfWork,
    ) -> tuple[list[BulkUploadItemResult], list[Source], list[UUID]]:
        results: list[BulkUploadItemResult] = []
        uploaded_sources: list[Source] = []
        enqueue_source_ids: list[UUID] = []

        # S3 upload + DB row commit happen together per file inside _upload_one_source.
        # uow.sources is a SourceRepository (postgres); create/flush/commit happen there.
        for prepared in prepared_uploads:
            item_result, source = await self._upload_one_source(
                prepared,
                project_id=project_id,
                batch_id=batch_id,
                source_ingestion_id=source_ingestion_id,
                source_type=source_type,
                uploader_id=uploader_id,
                uow=uow,
            )
            results.append(item_result)

            if source is not None:
                uploaded_sources.append(source)
                enqueue_source_ids.append(source.id)

        logger.debug(
            "PostgreSQL persistence complete: batch=%s saved=%d via SourceRepository",
            batch_id,
            len(uploaded_sources),
        )
        return results, uploaded_sources, enqueue_source_ids

    async def _project_sources_to_neo4j(
        self,
        *,
        batch_id: uuid.UUID,
        uploaded_sources: list[Source],
    ) -> None:
        # PostgreSQL is the source of truth; Neo4j is eventual-consistency.
        # Failures here must never break the upload response.
        if not uploaded_sources:
            return

        neo4j_repo = Neo4jSourceRepository(get_neo4j_driver())
        try:
            await neo4j_repo.create_level1_sources(
                [Neo4jSourceRepository.orm_to_node(s) for s in uploaded_sources]
            )
        except Exception as exc:
            logger.warning(
                "Neo4j Level-1 source projection failed for batch=%s: %s",
                batch_id,
                exc,
                exc_info=True,
            )

    def _build_bulk_upload_response(
        self,
        *,
        batch_id: uuid.UUID,
        files: list[UploadFile],
        project_id: UUID,
        results: list[BulkUploadItemResult],
    ) -> BulkUploadResponse:
        succeeded = sum(1 for r in results if r.status == SOURCE_STATUS_UPLOADED)
        duplicates = sum(1 for r in results if r.status == _BULK_ITEM_STATUS_DUPLICATE)
        failed = len(results) - succeeded - duplicates

        if failed > 0:
            failed_items = [
                {"filename": r.filename, "error": r.error}
                for r in results
                if r.status == SOURCE_STATUS_FAILED
            ]
            logger.error(
                "Bulk upload batch=%s completed with %d failure(s): project_id=%s failed_items=%s",
                batch_id,
                failed,
                project_id,
                failed_items,
            )
        else:
            logger.info(
                "Bulk upload batch=%s completed: project_id=%s total=%d succeeded=%d duplicates=%d",
                batch_id,
                project_id,
                len(files),
                succeeded,
                duplicates,
            )

        return BulkUploadResponse(
            batch_id=batch_id,
            total=len(files),
            succeeded=succeeded,
            failed=failed,
            results=results,
        )

    # ── Download from S3 ──────────────────────────────────────────────────
    # This methond is used by the AI team to retrieve source files for processing.
    # It looks up the source record, validates it, and streams the file from S3 to a local path in the app's data/ folder.
    # The local path, content type, and original filename are returned for the caller's use.
    # This method raises NotFoundError if the source_id does not exist or is deleted, and ValidationError if the source has no S3 key.
    async def download_single_file_in_local(
        self,
        *,
        source_id: UUID,
        uow: UnitOfWork,
        requester_id: UUID | None = None,
        requester_roles: list[str] | None = None,
        requester_tenant_id: UUID | None = None,
    ) -> tuple[str, str, str]:
        """Look up the source record, download the file from S3, and store it
        in the local ``data/`` folder.
        Returns ``(local_file_path, content_type, original_filename)``.
        Raises ``NotFoundError`` when the source_id does not exist or has
        been soft-deleted, and ``ValidationError`` when the source has no
        S3 storage key.

        ``requester_id=None`` (the default) skips the project-access check —
        used by internal callers (e.g. DocumentService pulling a file for AI
        processing) that have no HTTP requester to check.
        """
        source = uow.sources.get_by_uuid(source_id)
        if source is None or source.is_deleted:
            raise NotFoundError(f"Source '{source_id}' not found.")
        _assert_source_access(
            source.project_id, requester_id, requester_roles, requester_tenant_id, "read", uow
        )
        if not source.storage_key:
            raise AppValidationError(f"Source '{source_id}' has no associated file in storage.")
        # Store downloaded files under a project-root-relative folder when configured.
        data_dir = Path(settings.SOURCE_LOCAL_DOWNLOAD_DIR)
        if not data_dir.is_absolute():
            data_dir = PROJECT_ROOT / data_dir
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise StorageError(
                f"Cannot create local download directory '{data_dir}': {exc}"
            ) from exc

        source_file_mid_short_uuid = generate_short_uuid()
        safe_name = Path(source.original_name).name.replace(
            " ", "-"
        )  # strip path traversal and spaces
        local_path = data_dir / f"{source_id}_{source_file_mid_short_uuid}_{safe_name}"
        content_type = await download_from_s3(source.storage_key, local_path)
        logger.info("File saved locally: path=%s", local_path)
        return str(local_path), content_type, source.original_name

    # ── Download from S3 ──────────────────────────────────────────────────

    def get_source_for_download(
        self,
        *,
        source_id: UUID,
        uow: UnitOfWork,
        requester_id: UUID | None = None,
        requester_roles: list[str] | None = None,
        requester_tenant_id: UUID | None = None,
    ) -> Source:
        """Validate that *source_id* exists, is not deleted, and has an S3 key.

        Returns the ORM ``Source`` record so the caller can read ``storage_key``,
        ``mime_type``, ``file_size_bytes``, and ``original_name`` directly.

        The caller is responsible for streaming the file from S3 — no data is
        downloaded or written to disk by this method.

        Raises ``NotFoundError`` when the source_id does not exist or has been
        soft-deleted, and ``ValidationError`` when the source has no S3 key.
        """
        source = uow.sources.get_by_uuid(source_id)
        if source is None or source.is_deleted:
            raise NotFoundError(MSG_SOURCE_NOT_FOUND.format(source_id=source_id))
        _assert_source_access(
            source.project_id, requester_id, requester_roles, requester_tenant_id, "read", uow
        )

        if not source.storage_key:
            raise AppValidationError(MSG_SOURCE_NO_STORAGE_KEY.format(source_id=source_id))

        return source

    # ── Bulk upload ────────────────────────────────────────────────────────

    async def upload_bulk(
        self,
        *,
        files: list[UploadFile],
        project_id: UUID,
        description: str | None,
        source_type: str,
        context: SourceIngestionContextFields | None = None,
        uploader_id: UUID,
        uow: UnitOfWork,
        user_message: str = "",
        skip_processing: bool = False,
        requester_roles: list[str] | None = None,
        requester_tenant_id: UUID | None = None,
    ) -> BulkUploadResponse:
        """Validate and upload multiple files in one request.

        Validation for every file is completed before any upload starts.
        This avoids partial writes caused by invalid payloads discovered late.
        """
        context = context or SourceIngestionContextFields()
        self._validate_bulk_upload_request(
            files=files,
            project_id=project_id,
            source_type=source_type,
            uow=uow,
            requester_id=uploader_id,
            requester_roles=requester_roles,
            requester_tenant_id=requester_tenant_id,
        )

        batch_id = uuid.uuid4()

        # Per-file validation (type / size / zip-is-source-code / duplicate)
        # runs FIRST — before any SourceIngestion row is created. A request in
        # which every file fails validation must not leave an empty ingestion
        # behind; the row is created only once at least one file has passed and
        # there is real data to attach to it (see below).
        initial_results, prepared_uploads = await self._prepare_bulk_uploads(
            files=files,
            project_id=project_id,
            batch_id=batch_id,
            uow=uow,
        )

        persisted_results: list[BulkUploadItemResult] = []
        uploaded_sources: list[Source] = []
        enqueue_source_ids: list[UUID] = []

        if prepared_uploads:
            # One SourceIngestion row per bulk-upload request, whether it carries
            # one file or many. Carries the batch-level metadata (description,
            # stack fields, skip_processing) that used to be duplicated onto every
            # Source row. Flushed (not committed) here — it's in the same
            # transaction as the Source rows that reference it via FK below, and
            # the surrounding UnitOfWork commits everything together on request exit.
            ingestion = uow.source_ingestions.create_ingestion(
                project_id=project_id,
                source_type=source_type,
                description=description,
                source_language=context.source_language,
                frontend_stack=context.frontend_stack,
                backend_stack=context.backend_stack,
                infrastructure_stack=context.infrastructure_stack,
                architecture_stack=context.architecture_stack,
                database_stack=context.database_stack,
                coding_standard=context.coding_standard,
                database_strategy=context.database_strategy,
                architecture=context.architecture,
                security=context.security,
                source_layout_type=context.source_layout_type,
                user_message=user_message or None,
                skip_processing=skip_processing,
            )

            (
                persisted_results,
                uploaded_sources,
                enqueue_source_ids,
            ) = await self._persist_prepared_uploads(
                prepared_uploads=prepared_uploads,
                project_id=project_id,
                batch_id=batch_id,
                source_ingestion_id=ingestion.id,
                source_type=source_type,
                uploader_id=uploader_id,
                uow=uow,
            )
            if not uploaded_sources:
                # Every prepared file (already past type/size/duplicate
                # checks) failed during S3 upload or DB persist — the
                # ingestion row created above must not be left at its
                # default status="running" with nothing attached, or it
                # would block every later upload for this project via
                # raise_if_pipeline_running.
                uow.source_ingestions.update_fields(
                    ingestion.id,
                    status=SourceIngestionStatus.FAILED.value,
                    completed_at=datetime.now(UTC),
                )
                uow.source_ingestions.add_error(ingestion.id, MSG_SOURCE_BULK_NO_SOURCES_UPLOADED)
        results = [*initial_results, *persisted_results]

        await self._project_sources_to_neo4j(
            batch_id=batch_id,
            uploaded_sources=uploaded_sources,
        )

        # Always enqueue the root source dispatcher with the full source list.
        # It owns MIME-based routing and queued-status transition. batch_id is
        # threaded through as request_id — every task spawned by this one
        # upload request (root dispatcher + all fan-out leaves) shares it, so
        # a single cancellation flag covers the whole batch.
        _enqueue_processing(
            project_id,
            enqueue_source_ids,
            user_id=uploader_id,
            skip_processing=skip_processing,
            source_type=source_type,
            request_id=batch_id,
        )
        return self._build_bulk_upload_response(
            batch_id=batch_id,
            files=files,
            project_id=project_id,
            results=results,
        )

    async def upload_bulk_or_incremental(
        self,
        *,
        is_incremental: bool,
        files: list[UploadFile],
        project_id: UUID,
        description: str | None,
        source_type: str,
        context: SourceIngestionContextFields | None,
        user_message: str,
        skip_processing: bool,
        context_mode: str,
        uploader_id: UUID,
        uow: UnitOfWork,
        requester_roles: list[str] | None = None,
        requester_tenant_id: UUID | None = None,
    ) -> BulkUploadResponse | IncrementalBulkUploadResponse:
        """Route a bulk-upload request to the standard or incremental pipeline.

        ``is_incremental=True`` takes the same path as the dedicated
        ``/upload/bulk/incremental`` endpoint (``IncrementalBulkUploadService``)
        — kept as a flag here too so older clients posting straight to
        ``/upload/bulk`` don't need to change URLs.
        """
        if is_incremental:
            from app.services.incremental_source_service import (  # noqa: PLC0415
                IncrementalBulkUploadService,
            )

            return await IncrementalBulkUploadService().upload_and_enqueue(
                files=files,
                project_id=project_id,
                description=description,
                source_type=source_type,
                user_message=user_message,
                skip_processing=skip_processing,
                context_mode=context_mode,
                uploader_id=uploader_id,
                uow=uow,
                requester_roles=requester_roles,
                requester_tenant_id=requester_tenant_id,
            )

        return await self.upload_bulk(
            files=files,
            project_id=project_id,
            description=description,
            source_type=source_type,
            context=context,
            user_message=user_message,
            skip_processing=skip_processing,
            uploader_id=uploader_id,
            uow=uow,
            requester_roles=requester_roles,
            requester_tenant_id=requester_tenant_id,
        )

    # ── Per-item upload helper ────────────────────────────────────────────

    async def _upload_one_source(
        self,
        prepared: _PreparedSourceUpload,
        *,
        project_id: UUID,
        batch_id: uuid.UUID,
        source_ingestion_id: UUID,
        source_type: str,
        uploader_id: UUID,
        uow: UnitOfWork,
    ) -> tuple[BulkUploadItemResult, Source | None]:
        """Upload one pre-validated file to S3 and persist its metadata row.

        Always returns a ``BulkUploadItemResult`` — never raises — so that a
        single-item failure never aborts the rest of the batch.

        Consistency contract
        ────────────────────
        S3 upload fails
            Nothing written to S3, nothing written to DB.  Consistent.
            Result: status = ``failed``, HTTP 502.

        S3 upload succeeds, DB commit fails
            S3 object is immediately deleted (compensation), the UnitOfWork
            transaction is rolled back, and this item is returned as
            ``failed`` (HTTP 500).  The batch continues for remaining items.

        Presigned URL generation fails
            Non-fatal: ``storage_url`` is stored as ``None``.  The URL can be
            regenerated on the next read.  The row is still committed.
        """
        source_id = uuid.uuid4()
        s3_key = derive_s3_key(
            str(project_id),
            str(source_id),
            prepared.filename,
            prepared.relative_path,
        )

        # ── Phase 1: store in S3 ──────────────────────────────────────────
        # StorageError is caught here because each file is independent in a
        # bulk upload — a single storage failure must not abort remaining
        # files.  All other exception types propagate to the global handler.
        try:
            await upload_to_s3(
                file_bytes=prepared.data,
                object_key=s3_key,
                content_type=prepared.content_type,
            )
        except StorageError as exc:
            logger.warning(
                "Bulk upload S3 failure: file='%s' key=%s error=%s",
                prepared.filename,
                s3_key,
                exc,
            )
            return BulkUploadItemResult(
                filename=prepared.filename,
                relative_path=prepared.relative_path,
                status=SOURCE_STATUS_FAILED,
                status_code=http_status.HTTP_502_BAD_GATEWAY,
                error=MSG_SOURCE_STORAGE_FAILED.format(filename=prepared.filename),
            ), None

        # ── PostgreSQL: persist via SourceRepository ─────────────────────
        # create() stages, flushes, and refreshes so DB-assigned fields
        # (created_at, updated_at) are available before the commit.
        source = uow.sources.create(
            Source(
                id=source_id,
                project_id=project_id,
                original_name=prepared.filename,
                relative_path=prepared.relative_path,
                storage_key=s3_key,
                file_size_bytes=len(prepared.data),
                mime_type=prepared.content_type,
                file_type=prepared.file_type,
                upload_type=SOURCE_UPLOAD_BULK,
                batch_id=batch_id,
                source_ingestion_id=source_ingestion_id,
                source_type=source_type,
                status=SOURCE_STATUS_UPLOADED,
                checksum_sha256=prepared.checksum,
                created_by=uploader_id,
            )
        )

        # ── S3: generate presigned URL (best-effort) ──────────────────────
        # The object is already session-tracked after create(); setting
        # storage_url directly on the instance is picked up at commit time
        # without re-adding to the session.
        try:
            source.storage_url = await generate_presigned_url(s3_key)
        except StorageError:
            source.storage_url = None
            logger.warning(
                "Presigned URL generation failed for key=%s; storing without URL.", s3_key
            )

        # ── PostgreSQL: commit with S3 compensation on failure ────────────
        # If commit fails, compensate S3 + rollback so this file is marked
        # failed while the batch can continue processing other files.
        try:
            uow.commit()
        except Exception as db_exc:
            logger.error(
                "DB commit failed for bulk source '%s' (key=%s): %s — compensating S3 delete.",
                prepared.filename,
                s3_key,
                db_exc,
                exc_info=True,
            )
            try:
                await delete_from_s3(s3_key)
            except Exception:
                logger.warning(
                    "S3 compensation delete failed for bulk source '%s' (key=%s)",
                    prepared.filename,
                    s3_key,
                    exc_info=True,
                )
            try:
                uow.rollback()
            except Exception:
                logger.warning(
                    "UnitOfWork rollback failed after DB commit error for bulk source '%s'",
                    prepared.filename,
                    exc_info=True,
                )
            return BulkUploadItemResult(
                filename=prepared.filename,
                relative_path=prepared.relative_path,
                status=SOURCE_STATUS_FAILED,
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                error=(
                    f"'{prepared.filename}' could not be saved due to a database error. "
                    "All other files in this batch were unaffected."
                ),
            ), None

        logger.info("Bulk source uploaded: id=%s batch=%s", source.id, batch_id)

        return BulkUploadItemResult(
            filename=prepared.filename,
            relative_path=prepared.relative_path,
            status=SOURCE_STATUS_UPLOADED,
            status_code=http_status.HTTP_201_CREATED,
            source_id=source.id,
        ), source

    # ── Link upload ────────────────────────────────────────────────────────

    async def upload_from_link(
        self,
        *,
        link_url: str,
        project_id: UUID,
        description: str | None,
        source_type: str,
        uploader_id: UUID,
        uow: UnitOfWork,
        requester_roles: list[str] | None = None,
        requester_tenant_id: UUID | None = None,
    ) -> SourceResponse:
        """Download content from *link_url* and persist it as a Source.

        Behaviour by URL type
        ─────────────────────
        - **GitHub repository** URL → downloads the default-branch ZIP archive
          (source code path; ``upload_type = 'link'``, ``file_type = 'ZIP'``).
        - **GitHub blob / raw file** URL → downloads the individual file.
        - **Generic HTTPS URL** (SharePoint, Nextcloud, direct link) → downloads
          the response body and stores it as a single file.

        Raises ``ConflictError`` if an identical file (same SHA-256) already
        exists within the project.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        ProjectService.assert_project_access(
            project=project,
            requester_id=uploader_id,
            requester_roles=requester_roles or [],
            requester_tenant_id=requester_tenant_id,
            level="write",
            uow=uow,
        )

        # One SourceIngestion per link upload (a batch of exactly one file),
        # mirroring upload_bulk — carries the description that used to live
        # directly on the Source row.
        ingestion = uow.source_ingestions.create_ingestion(
            project_id=project_id, source_type=source_type, description=description
        )

        from app.utils.link_downloader import download_from_url

        downloaded = await download_from_url(link_url)
        # GitHub repo ZIPs are already guaranteed source code by the downloader.
        # Only validate non-archive ZIPs (e.g. a .zip uploaded via direct link).
        if not downloaded.is_archive and downloaded.content_type in SOURCE_ZIP_MIME_TYPES:
            _validate_zip_is_source_code(downloaded.data, downloaded.filename)
        checksum = compute_sha256(downloaded.data)

        existing = uow.sources.get_by_checksum(checksum, project_id)
        if existing is not None:
            raise ConflictError(MSG_SOURCE_DUPLICATE.format(source_id=existing.id))

        normalized_name = normalize_filename(downloaded.filename)
        source_id = uuid.uuid4()
        s3_key = derive_s3_key(str(project_id), str(source_id), normalized_name)

        await upload_to_s3(
            file_bytes=downloaded.data,
            object_key=s3_key,
            content_type=downloaded.content_type,
        )

        source = Source(
            id=source_id,
            project_id=project_id,
            original_name=normalized_name,
            relative_path=None,
            storage_key=s3_key,
            file_size_bytes=len(downloaded.data),
            mime_type=downloaded.content_type,
            file_type=downloaded.file_type,
            upload_type=SOURCE_UPLOAD_LINK,
            source_ingestion_id=ingestion.id,
            source_type=source_type,
            status=SOURCE_STATUS_UPLOADED,
            checksum_sha256=checksum,
            link_url=link_url,
            created_by=uploader_id,
        )
        uow.add(source)
        uow.flush()
        uow.refresh(source)

        # Presigned URL is best-effort: failure is non-fatal.
        # Avoids orphaning the S3 object if presign fails after a successful upload.
        try:
            source.storage_url = await generate_presigned_url(s3_key)
        except StorageError:
            source.storage_url = None
            logger.warning(
                "Presigned URL generation failed for key=%s; storing without URL.", s3_key
            )
        uow.add(source)

        # Explicit commit with S3 compensation on DB failure.
        # Without this, a DB failure on UoW auto-commit would orphan the S3 object
        # because the exception would bypass any compensation logic.
        try:
            uow.commit()
        except Exception as db_exc:
            logger.error(
                "DB commit failed for link source '%s' (key=%s): %s — compensating S3 delete.",
                downloaded.filename,
                s3_key,
                db_exc,
                exc_info=True,
            )
            await delete_from_s3(s3_key)
            raise

        logger.info(
            "Source uploaded from link: id=%s project=%s archive=%s",
            source.id,
            project_id,
            downloaded.is_archive,
        )

        # ── Neo4j projection (via Neo4jSourceRepository) ───────────────────────
        # PostgreSQL commit already succeeded above; this is best-effort.
        neo4j_repo = Neo4jSourceRepository(get_neo4j_driver())
        try:
            await neo4j_repo.create_level1_sources([Neo4jSourceRepository.orm_to_node(source)])
        except Exception as exc:
            logger.warning(
                "Neo4j metadata projection failed for link source id=%s: %s",
                source.id,
                exc,
                exc_info=True,
            )

        # Always enqueue the root source dispatcher with the source list.
        # It owns MIME-based routing and queued-status transition.
        _enqueue_processing(project_id, [source.id], user_id=uploader_id, source_type=source_type)

        # await send_event(
        #     data={
        #         "event": EVENT_SOURCE_UPLOADED,
        #         "source_id": str(source.id),
        #         "project_id": str(project_id),
        #         "file_type": downloaded.file_type,
        #         "storage_key": s3_key,
        #         "link_url": link_url,
        #     },
        #     queue_url=settings.AWS_SQS_QUEUE_URL,
        # )

        return SourceResponse.model_validate(source)

    # ── Read ───────────────────────────────────────────────────────────────

    def get_source(
        self,
        source_id: UUID,
        uow: UnitOfWork,
        requester_id: UUID | None = None,
        requester_roles: list[str] | None = None,
        requester_tenant_id: UUID | None = None,
    ) -> SourceResponse:
        source = uow.sources.get_by_uuid(source_id)
        if source is None:
            raise NotFoundError(MSG_SOURCE_NOT_FOUND.format(source_id=source_id))
        _assert_source_access(
            source.project_id, requester_id, requester_roles, requester_tenant_id, "read", uow
        )
        return SourceResponse.model_validate(source)

    def list_sources(
        self,
        *,
        project_id: UUID,
        skip: int,
        limit: int,
        status: str | None,
        file_type: str | None,
        upload_type: str | None,
        uow: UnitOfWork,
        requester_id: UUID | None = None,
        requester_roles: list[str] | None = None,
        requester_tenant_id: UUID | None = None,
    ) -> SourceListResponse:
        _assert_source_access(
            project_id, requester_id, requester_roles, requester_tenant_id, "read", uow
        )
        items, total = uow.sources.get_paginated(
            project_id=project_id,
            skip=skip,
            limit=limit,
            status=status,
            file_type=file_type,
            upload_type=upload_type,
        )
        return SourceListResponse(
            items=[SourceResponse.model_validate(s) for s in items],
            total=total,
            skip=skip,
            limit=limit,
        )

    # ── Soft delete ────────────────────────────────────────────────────────

    async def delete_source(
        self,
        *,
        source_id: UUID,
        requester_id: UUID,
        requester_roles: list[str],
        uow: UnitOfWork,
        requester_tenant_id: UUID | None = None,
    ) -> None:
        source = uow.sources.get_by_uuid(source_id)
        if source is None:
            raise NotFoundError(MSG_SOURCE_NOT_FOUND.format(source_id=source_id))

        # The uploader may always delete their own source; anyone else needs
        # project-level "write" access (owner/super_admin/tenant-admin, or an
        # assigned project Member) — replaces the old bare ROLE_ADMIN check,
        # which had no tenant/project scoping and no super_admin bypass.
        if source.created_by != requester_id:
            _assert_source_access(
                source.project_id,
                requester_id,
                requester_roles,
                requester_tenant_id,
                "write",
                uow,
            )

        storage_key = source.storage_key
        source.is_deleted = True
        source.deleted_at = datetime.now(UTC)
        uow.add(source)

        # Delete fragment_embeddings rows from PostgreSQL before committing so
        # the removal is part of the same transaction.
        embedding_count = uow.fragment_embeddings.delete_by_source_id(source_id)
        uow.commit()
        logger.info(
            "Source soft-deleted: id=%s by=%s (embeddings removed: %d)",
            source_id,
            requester_id,
            embedding_count,
        )

        # Remove the Source node and all owned nodes from Neo4j (via Neo4jSourceRepository).
        # Best-effort: the PostgreSQL soft-delete is authoritative.
        try:
            await Neo4jSourceRepository(get_neo4j_driver()).delete_source_node(source_id)
            logger.info(
                "Neo4j source subgraph removed (source/modules/features/fragments): id=%s",
                source_id,
            )
        except Exception:
            logger.warning(
                "Neo4j cleanup failed for source id=%s; node may persist.",
                source_id,
                exc_info=True,
            )

        # S3 cleanup is best-effort for soft-deletes: the DB record is the
        # authoritative state.  A storage failure here must not surface as an
        # error to the caller — the logical delete has already succeeded.
        if storage_key:
            try:
                await delete_from_s3(storage_key)
            except StorageError:
                logger.warning(
                    "S3 cleanup failed for soft-deleted source id=%s key=%s; "
                    "object will persist until next cleanup.",
                    source_id,
                    storage_key,
                )

    async def bulk_delete_sources(
        self,
        *,
        source_ids: list[UUID],
        requester_id: UUID,
        requester_roles: list[str],
        uow: UnitOfWork,
        requester_tenant_id: UUID | None = None,
    ) -> BulkDeleteResponse:
        """Soft-delete multiple sources in one request.

        Each source is handled independently — a failure on one does not
        prevent the others from being processed.  Returns a per-source
        outcome summary.
        """
        results: list[BulkDeleteItemResult] = []

        for source_id in source_ids:
            source = uow.sources.get_by_uuid(source_id)
            if source is None:
                results.append(
                    BulkDeleteItemResult(
                        source_id=source_id,
                        status="not_found",
                        status_code=http_status.HTTP_404_NOT_FOUND,
                        error=MSG_SOURCE_NOT_FOUND.format(source_id=source_id),
                    )
                )
                continue

            # The uploader may always delete their own source; anyone else
            # needs project-level "write" access — see delete_source above.
            if source.created_by != requester_id:
                try:
                    _assert_source_access(
                        source.project_id,
                        requester_id,
                        requester_roles,
                        requester_tenant_id,
                        "write",
                        uow,
                    )
                except ForbiddenError:
                    results.append(
                        BulkDeleteItemResult(
                            source_id=source_id,
                            status="forbidden",
                            status_code=http_status.HTTP_403_FORBIDDEN,
                            error=MSG_SOURCE_DELETE_FORBIDDEN,
                        )
                    )
                    continue

            storage_key = source.storage_key
            source.is_deleted = True
            source.deleted_at = datetime.now(UTC)
            uow.add(source)

            # Delete fragment_embeddings rows from PostgreSQL in the same txn.
            embedding_count = uow.fragment_embeddings.delete_by_source_id(source_id)
            uow.commit()

            # Remove the Source node and all owned nodes from Neo4j (via Neo4jSourceRepository).
            try:
                await Neo4jSourceRepository(get_neo4j_driver()).delete_source_node(source_id)
                logger.info(
                    "Neo4j source subgraph removed (source/modules/features/fragments): id=%s",
                    source_id,
                )
            except Exception:
                logger.warning(
                    "Neo4j cleanup failed for source id=%s; node may persist.",
                    source_id,
                    exc_info=True,
                )

            # S3 cleanup is best-effort: the DB soft-delete already succeeded.
            if storage_key:
                try:
                    await delete_from_s3(storage_key)
                except StorageError:
                    logger.warning(
                        "S3 cleanup failed for soft-deleted source id=%s key=%s; "
                        "object will persist until next cleanup.",
                        source_id,
                        storage_key,
                    )
            results.append(
                BulkDeleteItemResult(
                    source_id=source_id,
                    status=_DELETE_ITEM_STATUS_DELETED,
                    status_code=http_status.HTTP_204_NO_CONTENT,
                )
            )
            logger.info(
                "Bulk source soft-deleted: id=%s by=%s (embeddings removed: %d)",
                source_id,
                requester_id,
                embedding_count,
            )

        succeeded = sum(1 for r in results if r.status == _DELETE_ITEM_STATUS_DELETED)
        failed = len(results) - succeeded
        return BulkDeleteResponse(
            total=len(source_ids),
            succeeded=succeeded,
            failed=failed,
            results=results,
        )
