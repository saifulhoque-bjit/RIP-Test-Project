"""Business logic for the incremental bulk upload endpoint.

Route-handler responsibilities (synchronous, returns immediately)
─────────────────────────────────────────────────────────────────
1. Validate the project exists.
2. For each file: check MIME type, upload to S3, persist the Source row in
   PostgreSQL.  Per-file failures never abort the rest of the batch.
3. Project new source nodes to Neo4j (best-effort).
4. Create a ProjectTask row so the frontend can track progress.
5. Enqueue ``incremental_update_task`` (Celery) with the uploaded source IDs.
6. Return immediately with the batch summary + task_id.

All heavy AI work (parsing, fragment storage, backlog retrieval,
run_incremental_update LLM pipeline) is done inside the Celery worker.
Progress is delivered to connected WebSocket clients via Redis pub/sub.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import uuid
from uuid import UUID

from fastapi import UploadFile, status as http_status

from app.clients.s3_client import (
    compute_sha256,
    delete_from_s3,
    derive_s3_key,
    generate_presigned_url,
    upload_to_s3,
)
from app.core.config import settings
from app.core.constants import (
    BYTES_PER_MB,
    CONTEXT_MODE_FULL,
    INCREMENTAL_UPDATE_STATUS_QUEUED,
    INCREMENTAL_UPDATE_TASK_TYPE,
    SOURCE_DEFAULT_FORMAT,
    SOURCE_INCREMENTAL_ALLOWED_MIME_TYPES,
    SOURCE_INCREMENTAL_PDF_MIME_TYPE,
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_UPLOADED,
    SOURCE_UPLOAD_INCREMENTAL,
)
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.enums.source_type import SourceType
from app.core.exceptions import NotFoundError, StorageError, ValidationError as AppValidationError
from app.core.messages import (
    MSG_PROJECT_NOT_FOUND,
    MSG_SOURCE_BULK_DUPLICATE,
    MSG_SOURCE_BULK_DUPLICATE_FILENAME,
    MSG_SOURCE_BULK_FILES_REQUIRED,
    MSG_SOURCE_BULK_TOO_MANY,
    MSG_SOURCE_BULK_TOTAL_TOO_LARGE,
    MSG_SOURCE_FILE_TOO_LARGE,
    MSG_SOURCE_INCREMENTAL_FEATURES_NOT_APPROVED,
    MSG_SOURCE_INCREMENTAL_MODULES_NOT_APPROVED,
    MSG_SOURCE_INCREMENTAL_NO_SOURCES_UPLOADED,
    MSG_SOURCE_INCREMENTAL_REQUIRES_RFP_SOURCE,
    MSG_SOURCE_INCREMENTAL_UNSUPPORTED_TYPE,
    MSG_SOURCE_INCREMENTAL_USER_STORIES_NOT_APPROVED,
    MSG_SOURCE_STORAGE_FAILED,
)
from app.db.neo4j import get_neo4j_driver
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.project_task_model import CreateTaskParams
from app.models.postgres.source_model import Source
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
from app.repositories.neo4j.source_repository import SourceRepository as Neo4jSourceRepository
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.schemas.source_schema import IncrementalBulkUploadResponse, IncrementalUploadItemResult
from app.services.project_service import ProjectService
from app.services.project_task_service import ProjectTaskService
from app.utils.common import normalize_filename
from app.utils.logger import get_logger

logger = get_logger(__name__)

_ITEM_STATUS_DUPLICATE = "duplicate"
SOURCE_KIND_PDF = "pdf"
SOURCE_KIND_IMAGE = "image"


@dataclass(slots=True)
class _UploadedSource:
    source: Source
    kind: str  # SOURCE_KIND_PDF | SOURCE_KIND_IMAGE


@dataclass(slots=True)
class _FileContext:
    """Pre-validated metadata for one uploaded file."""

    filename: str
    raw: bytes
    content_type: str
    checksum: str
    kind: str
    file_type: str


@dataclass
class _BatchState:
    """Accumulates results while processing a batch of files."""

    item_results: list[IncrementalUploadItemResult] = field(default_factory=list)
    uploaded_sources: list[_UploadedSource] = field(default_factory=list)
    seen_checksums: set[str] = field(default_factory=set)
    seen_filenames: set[str] = field(default_factory=set)

    def add_fail(self, filename: str, code: int, error: str) -> None:
        self.item_results.append(
            IncrementalUploadItemResult(
                filename=filename,
                status=SOURCE_STATUS_FAILED,
                status_code=code,
                error=error,
            )
        )

    def add_duplicate(self, filename: str, source_id: UUID | None, error: str) -> None:
        self.item_results.append(
            IncrementalUploadItemResult(
                filename=filename,
                status=_ITEM_STATUS_DUPLICATE,
                status_code=http_status.HTTP_409_CONFLICT,
                source_id=source_id,
                error=error,
            )
        )

    def add_success(self, source: Source, kind: str) -> None:
        self.item_results.append(
            IncrementalUploadItemResult(
                filename=source.original_name,
                status=SOURCE_STATUS_UPLOADED,
                status_code=http_status.HTTP_201_CREATED,
                source_id=source.id,
            )
        )
        self.uploaded_sources.append(_UploadedSource(source=source, kind=kind))


def _classify_mime(mime: str) -> str:
    return SOURCE_KIND_PDF if mime == SOURCE_INCREMENTAL_PDF_MIME_TYPE else SOURCE_KIND_IMAGE


def _derive_file_type(filename: str, content_type: str) -> str:
    if "." in filename:
        return filename.rsplit(".", 1)[-1].upper()
    _fallback = {
        "application/pdf": "PDF",
        "image/jpeg": "JPEG",
        "image/jpg": "JPG",
        "image/png": "PNG",
        "image/webp": "WEBP",
    }
    return _fallback.get(content_type, SOURCE_DEFAULT_FORMAT)


class IncrementalBulkUploadService:
    """Upload files and enqueue the incremental AI pipeline as a Celery task."""

    async def _validate_bulk_upload_request(
        self,
        *,
        files: list[UploadFile],
        project_id: UUID,
        uow: UnitOfWork,
        requester_id: UUID | None = None,
        requester_roles: list[str] | None = None,
        requester_tenant_id: UUID | None = None,
    ) -> None:
        """Validate an incremental bulk-upload request before any file is processed.

        Mirrors ``SourceService._validate_bulk_upload_request`` — project
        existence/access/LLM-key checks, then request-shape checks (files
        required, file-count cap, cumulative size cap) — plus incremental-only
        guards: an initial RFP source must already exist for the project (an
        incremental update has no backlog to update otherwise), no other
        generation pipeline may already be running for this project (an
        incremental update reads and mutates the same live backlog), and the
        entire existing backlog (modules, then features, then user stories)
        must already be fully approved before a new incremental update can be
        started.
        """
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

        if not uow.sources.exists_by_project_and_source_type(project_id, SourceType.RFP.value):
            raise AppValidationError(MSG_SOURCE_INCREMENTAL_REQUIRES_RFP_SOURCE)

        from app.services.source_ingestion_service import SourceIngestionService  # noqa: PLC0415

        SourceIngestionService.raise_if_pipeline_running(uow, project_id)

        module_feature_repo = ModuleFeatureRepository(get_neo4j_driver())
        user_story_repo = UserStoryRepository(get_neo4j_driver())

        if not await module_feature_repo.are_all_modules_approved(project_id):
            raise AppValidationError(MSG_SOURCE_INCREMENTAL_MODULES_NOT_APPROVED)
        if not await module_feature_repo.are_all_features_approved(project_id):
            raise AppValidationError(MSG_SOURCE_INCREMENTAL_FEATURES_NOT_APPROVED)
        if not await user_story_repo.are_all_user_stories_approved(project_id):
            raise AppValidationError(MSG_SOURCE_INCREMENTAL_USER_STORIES_NOT_APPROVED)

        if not files:
            raise AppValidationError(MSG_SOURCE_BULK_FILES_REQUIRED)

        max_files = settings.SOURCE_BULK_MAX_FILES
        if len(files) > max_files:
            raise AppValidationError(
                MSG_SOURCE_BULK_TOO_MANY.format(max_files=max_files, received=len(files))
            )

        # Cumulative size guard — mirrors SourceService.upload_bulk. Without this,
        # an unbounded number of large PDFs/images could be summed into a single
        # multi-gigabyte request (resource-exhaustion risk).
        max_total_bytes = settings.SOURCE_BULK_MAX_TOTAL_SIZE_MB * BYTES_PER_MB
        total_size = sum(file.size or 0 for file in files)
        if total_size > max_total_bytes:
            raise AppValidationError(
                MSG_SOURCE_BULK_TOTAL_TOO_LARGE.format(
                    max_mb=settings.SOURCE_BULK_MAX_TOTAL_SIZE_MB,
                )
            )

    async def upload_and_enqueue(
        self,
        *,
        files: list[UploadFile],
        project_id: UUID,
        description: str | None,
        source_type: str,
        user_message: str,
        skip_processing: bool,
        context_mode: str = CONTEXT_MODE_FULL,
        uploader_id: UUID,
        uow: UnitOfWork,
        requester_roles: list[str] | None = None,
        requester_tenant_id: UUID | None = None,
    ) -> IncrementalBulkUploadResponse:
        await self._validate_bulk_upload_request(
            files=files,
            project_id=project_id,
            uow=uow,
            requester_id=uploader_id,
            requester_roles=requester_roles,
            requester_tenant_id=requester_tenant_id,
        )

        batch_id = uuid.uuid4()
        state = _BatchState()

        # Pre-validate + dedupe every file BEFORE creating any DB row — mirrors
        # SourceService.upload_bulk. Type/size failures and duplicates (against
        # both existing DB sources and other files in this same batch) are all
        # recorded in state.item_results here; nothing is uploaded to S3 or
        # persisted yet.
        prepared_files: list[_FileContext] = []
        for file in files:
            ctx = await self._prepare_one_file(
                file=file,
                project_id=project_id,
                batch_id=batch_id,
                state=state,
                uow=uow,
            )
            if ctx is not None:
                prepared_files.append(ctx)

        if not prepared_files:
            # Nothing survived pre-validation (all duplicates/unsupported/too
            # large) — no SourceIngestion is created and no background task is
            # enqueued, so this request leaves no trace that could block a
            # later, valid upload via raise_if_pipeline_running. The per-file
            # results below are the complete picture for this request.
            logger.info(
                "[INCREMENTAL] Batch rejected at pre-validation: batch=%s project=%s "
                "total=%d failed=%d",
                batch_id,
                project_id,
                len(files),
                len(state.item_results),
            )
            return IncrementalBulkUploadResponse(
                batch_id=batch_id,
                task_id=None,
                total=len(files),
                succeeded=0,
                failed=len(state.item_results),
                results=state.item_results,
            )

        # One SourceIngestion row per bulk-upload request, whether it carries
        # one file or many — mirrors SourceService.upload_bulk. Carries the
        # batch-level metadata (description, is_incremental, user_message,
        # skip_processing) that used to be duplicated onto every Source row.
        # Flushed (not committed) here; it's in the same transaction as the
        # Source rows that reference it via FK below, and later per-file
        # commits (or the surrounding UnitOfWork on request exit) persist it
        # durably.
        ingestion = uow.source_ingestions.create_ingestion(
            project_id=project_id,
            source_type=source_type,
            description=description,
            is_incremental=True,
            user_message=user_message or None,
            skip_processing=skip_processing,
        )

        for ctx in prepared_files:
            await self._upload_and_persist(
                ctx=ctx,
                project_id=project_id,
                source_type=source_type,
                uploader_id=uploader_id,
                batch_id=batch_id,
                source_ingestion_id=ingestion.id,
                state=state,
                uow=uow,
            )

        await self._project_sources_to_neo4j(
            batch_id=batch_id, uploaded_sources=state.uploaded_sources
        )

        task_db_id, task_id = self._create_and_enqueue_task(
            project_id=project_id,
            uploader_id=uploader_id,
            batch_id=batch_id,
            ingestion_id=ingestion.id,
            uploaded_sources=state.uploaded_sources,
            user_message=user_message,
            skip_processing=skip_processing,
            context_mode=context_mode,
            uow=uow,
        )

        succeeded = sum(1 for r in state.item_results if r.status == SOURCE_STATUS_UPLOADED)
        duplicates = sum(1 for r in state.item_results if r.status == _ITEM_STATUS_DUPLICATE)
        failed = len(state.item_results) - succeeded - duplicates

        logger.info(
            "[INCREMENTAL] Batch complete: batch=%s project=%s "
            "total=%d succeeded=%d duplicates=%d failed=%d task_id=%s",
            batch_id,
            project_id,
            len(files),
            succeeded,
            duplicates,
            failed,
            task_db_id,
        )

        return IncrementalBulkUploadResponse(
            batch_id=batch_id,
            task_id=task_db_id,
            total=len(files),
            succeeded=succeeded,
            failed=failed,
            results=state.item_results,
        )

    async def _prepare_one_file(
        self,
        *,
        file: UploadFile,
        project_id: UUID,
        batch_id: UUID,
        state: _BatchState,
        uow: UnitOfWork,
    ) -> _FileContext | None:
        """Validate and dedupe one file, without touching S3 or the DB.

        Returns the prepared ``_FileContext`` when the file is safe to
        upload/persist, or ``None`` after recording a failure/duplicate
        result on *state*.
        """
        raw_name = file.filename or "upload"
        filename = normalize_filename(raw_name)
        raw = await file.read()
        content_type = (
            (file.content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
        )

        max_file_bytes = settings.SOURCE_MAX_FILE_SIZE_MB * BYTES_PER_MB
        if len(raw) > max_file_bytes:
            state.add_fail(
                filename,
                http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                MSG_SOURCE_FILE_TOO_LARGE.format(
                    filename=filename, max_mb=settings.SOURCE_MAX_FILE_SIZE_MB
                ),
            )
            logger.warning(
                "[INCREMENTAL] Rejected oversized file: file='%s' size=%d batch=%s",
                filename,
                len(raw),
                batch_id,
            )
            return None

        if content_type not in SOURCE_INCREMENTAL_ALLOWED_MIME_TYPES:
            state.add_fail(
                filename,
                http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                MSG_SOURCE_INCREMENTAL_UNSUPPORTED_TYPE.format(content_type=content_type),
            )
            logger.warning(
                "[INCREMENTAL] Rejected unsupported MIME: file='%s' mime=%s batch=%s",
                filename,
                content_type,
                batch_id,
            )
            return None

        existing_by_name = uow.sources.get_by_filename(filename, project_id)
        if existing_by_name is not None or filename.lower() in state.seen_filenames:
            state.add_duplicate(
                filename,
                existing_by_name.id if existing_by_name else None,
                MSG_SOURCE_BULK_DUPLICATE_FILENAME.format(filename=filename),
            )
            logger.info(
                "[INCREMENTAL] Filename duplicate skipped: file='%s' batch=%s", filename, batch_id
            )
            return None

        checksum = compute_sha256(raw)
        existing = uow.sources.get_by_checksum(checksum, project_id)
        if existing is not None or checksum in state.seen_checksums:
            state.add_duplicate(
                filename, existing.id if existing else None, MSG_SOURCE_BULK_DUPLICATE
            )
            logger.info("[INCREMENTAL] Duplicate skipped: file='%s' batch=%s", filename, batch_id)
            return None

        state.seen_checksums.add(checksum)
        state.seen_filenames.add(filename.lower())
        return _FileContext(
            filename=filename,
            raw=raw,
            content_type=content_type,
            checksum=checksum,
            kind=_classify_mime(content_type),
            file_type=_derive_file_type(filename, content_type),
        )

    async def _upload_and_persist(
        self,
        *,
        ctx: _FileContext,
        project_id: UUID,
        source_type: str,
        uploader_id: UUID,
        batch_id: UUID,
        source_ingestion_id: UUID,
        state: _BatchState,
        uow: UnitOfWork,
    ) -> None:
        source_id = uuid.uuid4()
        s3_key = derive_s3_key(str(project_id), str(source_id), ctx.filename)

        try:
            await upload_to_s3(file_bytes=ctx.raw, object_key=s3_key, content_type=ctx.content_type)
        except StorageError:
            state.add_fail(
                ctx.filename,
                http_status.HTTP_502_BAD_GATEWAY,
                MSG_SOURCE_STORAGE_FAILED.format(filename=ctx.filename),
            )
            logger.warning(
                "[INCREMENTAL] S3 upload failed: file='%s' key=%s batch=%s",
                ctx.filename,
                s3_key,
                batch_id,
            )
            return

        source = uow.sources.create(
            Source(
                id=source_id,
                project_id=project_id,
                original_name=ctx.filename,
                relative_path=None,
                storage_key=s3_key,
                file_size_bytes=len(ctx.raw),
                mime_type=ctx.content_type,
                file_type=ctx.file_type,
                upload_type=SOURCE_UPLOAD_INCREMENTAL,
                batch_id=batch_id,
                source_ingestion_id=source_ingestion_id,
                source_type=source_type,
                status=SOURCE_STATUS_UPLOADED,
                checksum_sha256=ctx.checksum,
                created_by=uploader_id,
            )
        )

        try:
            source.storage_url = await generate_presigned_url(s3_key)
        except StorageError:
            source.storage_url = None
            logger.warning("[INCREMENTAL] Presigned URL failed for key=%s; continuing.", s3_key)

        try:
            uow.commit()
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] DB commit failed: file='%s' key=%s error=%s",
                ctx.filename,
                s3_key,
                exc,
                exc_info=True,
            )
            _compensate_s3(s3_key)
            try:
                uow.rollback()
            except Exception:
                pass
            state.add_fail(
                ctx.filename,
                http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                f"'{ctx.filename}' could not be saved due to a database error.",
            )
            return

        state.add_success(source, ctx.kind)
        logger.info(
            "[INCREMENTAL] Uploaded: source_id=%s file='%s' kind=%s batch=%s",
            source.id,
            ctx.filename,
            ctx.kind,
            batch_id,
        )

    def _create_and_enqueue_task(
        self,
        *,
        project_id: UUID,
        uploader_id: UUID,
        batch_id: UUID,
        ingestion_id: UUID,
        uploaded_sources: list[_UploadedSource],
        user_message: str,
        skip_processing: bool,
        context_mode: str,
        uow: UnitOfWork,
    ) -> tuple[str, UUID]:
        """Create a ProjectTask row and dispatch the Celery worker.

        Returns ``(task_db_id, task_uuid)`` so the caller can include
        ``task_db_id`` in the HTTP response for WebSocket tracking.
        """
        source_id_strs = [str(us.source.id) for us in uploaded_sources]
        pdf_ids = [str(us.source.id) for us in uploaded_sources if us.kind == SOURCE_KIND_PDF]
        img_ids = [str(us.source.id) for us in uploaded_sources if us.kind == SOURCE_KIND_IMAGE]

        if uploaded_sources:
            # The incremental pipeline runs module_feature and user_story
            # updates in one automated pass (no manual approval gate), so it
            # doesn't distinguish those phases the way a normal RFP run does
            # — tag the collapsed GENERATING_REQUIREMENTS stage instead (see
            # FEEDBACK_OR_INCREMENTAL_STAGES_STATUS_MAP). The RFP-specific
            # GENERATING_USER_STORY "generation already in progress" guards
            # (source_ingestion_service.py, user_story_service.py) are scoped
            # to source_type RFP/ADDITIONAL_RFP only, so they never consult
            # an incremental ingestion's stages regardless.
            uow.source_ingestions.add_stage(
                ingestion_id, SourceIngestionStage.GENERATING_REQUIREMENTS.value
            )
        else:
            # Nothing survived per-file validation/dedup — the ingestion row
            # created up front (before per-file processing) would otherwise
            # stay status="running" forever with no source ever attached to
            # it, and every subsequent bulk-upload call for this project
            # (valid files included) would then be rejected by
            # raise_if_pipeline_running, which blocks on ANY running
            # ingestion project-wide.
            uow.source_ingestions.update_fields(
                ingestion_id,
                status=SourceIngestionStatus.FAILED.value,
                completed_at=datetime.now(UTC),
            )
            uow.source_ingestions.add_error(
                ingestion_id, MSG_SOURCE_INCREMENTAL_NO_SOURCES_UPLOADED
            )

        # Commit now: the Source/SourceIngestion rows created above must be
        # durably visible to process_source_task's own separate DB session
        # before it starts reading them, and ProjectTaskService.create_task()
        # below opens its own separate session and commits immediately too.
        uow.commit()

        task_id, task_db_id = ProjectTaskService().create_task(
            project_id=project_id,
            user_id=uploader_id,
            params=CreateTaskParams(
                task_type=INCREMENTAL_UPDATE_TASK_TYPE,
                status=INCREMENTAL_UPDATE_STATUS_QUEUED,
                stage="incremental_update.queued",
                meta={
                    "batch_id": str(batch_id),
                    "source_ids": source_id_strs,
                    "pdf_count": len(pdf_ids),
                    "image_count": len(img_ids),
                },
            ),
        )

        _enqueue_incremental_update(
            project_id=project_id,
            pdf_source_ids=pdf_ids,
            image_source_ids=img_ids,
            user_message=user_message,
            skip_processing=skip_processing,
            context_mode=context_mode,
            task_db_id=task_db_id,
            task_id=task_id,
        )

        return task_db_id, task_id

    async def _project_sources_to_neo4j(
        self,
        *,
        batch_id: UUID,
        uploaded_sources: list[_UploadedSource],
    ) -> None:
        if not uploaded_sources:
            return
        neo4j_repo = Neo4jSourceRepository(get_neo4j_driver())
        try:
            await neo4j_repo.create_level1_sources(
                [Neo4jSourceRepository.orm_to_node(us.source) for us in uploaded_sources]
            )
            logger.info(
                "[INCREMENTAL] Neo4j sources projected: batch=%s count=%d",
                batch_id,
                len(uploaded_sources),
            )
        except Exception as exc:
            logger.warning(
                "[INCREMENTAL] Neo4j source projection failed: batch=%s error=%s",
                batch_id,
                exc,
                exc_info=True,
            )


def _compensate_s3(s3_key: str) -> None:
    """Best-effort S3 cleanup after a DB commit failure."""
    import asyncio  # noqa: PLC0415

    async def _delete() -> None:
        try:
            await delete_from_s3(s3_key)
        except Exception:
            logger.warning("[INCREMENTAL] S3 compensation delete failed for key=%s", s3_key)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_delete())
        else:
            loop.run_until_complete(_delete())
    except Exception:
        logger.warning("[INCREMENTAL] Could not schedule S3 compensation delete for key=%s", s3_key)


def _enqueue_incremental_update(
    *,
    project_id: UUID,
    pdf_source_ids: list[str],
    image_source_ids: list[str],
    user_message: str,
    skip_processing: bool,
    context_mode: str,
    task_db_id: str,
    task_id: UUID,
) -> None:
    """Fire-and-forget: dispatch the incremental update Celery task.

    Mirrors the pattern used by ``_enqueue_processing`` in source_service.py.
    Failures are logged and never re-raised so the upload response is not
    affected by a transient broker unavailability.
    """
    if not pdf_source_ids and not image_source_ids:
        logger.info(
            "[INCREMENTAL] No uploadable sources — skipping Celery dispatch (task_db_id=%s)",
            task_db_id,
        )
        return

    try:
        from app.workers.incremental_task import incremental_update_task  # noqa: PLC0415

        async_result = incremental_update_task.apply_async(
            args=[
                str(project_id),
                pdf_source_ids,
                image_source_ids,
                user_message,
                skip_processing,
                task_db_id,
                context_mode,
            ]
        )
        ProjectTaskService().set_celery_task_id(task_id, async_result.id)
        logger.info(
            "[INCREMENTAL] Enqueued incremental_update_task: project=%s "
            "pdf_count=%d image_count=%d celery_id=%s task_db_id=%s",
            project_id,
            len(pdf_source_ids),
            len(image_source_ids),
            async_result.id,
            task_db_id,
        )
    except Exception as exc:
        logger.error(
            "[INCREMENTAL] Failed to enqueue incremental_update_task: "
            "project=%s task_db_id=%s error=%s",
            project_id,
            task_db_id,
            exc,
            exc_info=True,
        )
