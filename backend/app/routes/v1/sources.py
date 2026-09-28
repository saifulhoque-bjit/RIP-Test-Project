"""Route handlers for /sources — v1.

Endpoint summary
────────────────
Any authenticated user:
    GET    /sources/download/{source_id}        — stream file from S3 to client
    GET    /sources/download/local/{source_id}  — prepare local copy for AI jobs
    POST   /sources/upload/bulk                 — upload multiple files
    POST   /sources/upload/link                 — ingest source from a URL
    GET    /sources                             — paginated, filterable source list
    GET    /sources/ingestion                   — paginated, filterable source-ingestion list
    DELETE /sources/delete/bulk                 — soft-delete multiple sources
    GET    /sources/{source_id}                 — fetch a single source
    DELETE /sources/{source_id}                 — soft-delete a source

Design rules
────────────
- Zero business logic here — all decisions live in SourceService.
- Annotated aliases declared once at module level; reused across handlers.
- Exception handling is centralised in app/core/exception_handlers.py.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from app.clients.s3_client import iter_s3_chunks
from app.core.enums.context_mode import ContextMode
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.enums.source_type import SourceType
from app.core.messages import (
    DESC_SOURCE_DESCRIPTION,
    DESC_SOURCE_FILES,
    DESC_SOURCE_FILTER_FORMAT,
    DESC_SOURCE_FILTER_PROJECT,
    DESC_SOURCE_FILTER_STATUS,
    DESC_SOURCE_FILTER_UPLOAD_TYPE,
    DESC_SOURCE_INGESTION_FILTER_PROJECT,
    DESC_SOURCE_INGESTION_FILTER_SOURCE_TYPE,
    DESC_SOURCE_INGESTION_FILTER_STATUS,
    DESC_SOURCE_PROJECT_ID,
    DESC_SOURCE_TYPE,
    MSG_SOURCE_AI_DOWNLOAD_READY,
    MSG_SOURCE_BULK_DELETE_PROCESSED,
    MSG_SOURCE_BULK_PROCESSED,
    MSG_SOURCE_INCREMENTAL_BULK_PROCESSED,
    MSG_SOURCE_LINK_UPLOADED,
    SUMMARY_SOURCE_AI_DOWNLOAD,
    SUMMARY_SOURCE_BULK_DELETE,
    SUMMARY_SOURCE_BULK_UPLOAD,
    SUMMARY_SOURCE_DELETE,
    SUMMARY_SOURCE_DOWNLOAD,
    SUMMARY_SOURCE_GET,
    SUMMARY_SOURCE_INCREMENTAL_BULK_UPLOAD,
    SUMMARY_SOURCE_INGESTION_LIST,
    SUMMARY_SOURCE_LINK_UPLOAD,
    SUMMARY_SOURCE_LIST,
)
from app.core.rate_limiter import (
    limiter,
    source_delete_limit,
    source_download_limit,
    source_upload_limit,
)
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_uow
from app.models.postgres.user_model import User
from app.schemas.source_ingestion_schema import SourceIngestionListResponse
from app.schemas.source_schema import (
    AiSourceDownloadResponse,
    BulkDeleteRequest,
    BulkDeleteResponse,
    BulkUploadResponse,
    IncrementalBulkUploadResponse,
    LinkUploadRequest,
    SourceListResponse,
    SourceResponse,
)
from app.services.incremental_source_service import IncrementalBulkUploadService
from app.services.source_ingestion_service import SourceIngestionService
from app.services.source_service import SourceIngestionContextFields, SourceService
from app.utils.log_context import bind_log_context
from app.utils.logger import get_logger
from app.utils.openapi import BULK_UPLOAD_OPENAPI_EXTRA, INCREMENTAL_BULK_UPLOAD_OPENAPI_EXTRA
from app.utils.pagination import PaginationParams
from app.utils.response import ApiResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/sources", tags=["Sources"])

# ── Annotated dependency aliases ────────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
CurrentPaging = Annotated[PaginationParams, Depends(PaginationParams)]

ProjectIdFilter = Annotated[UUID, Query(description=DESC_SOURCE_FILTER_PROJECT)]
StatusFilter = Annotated[str | None, Query(description=DESC_SOURCE_FILTER_STATUS)]
FileTypeFilter = Annotated[str | None, Query(description=DESC_SOURCE_FILTER_FORMAT)]
UploadTypeFilter = Annotated[str | None, Query(description=DESC_SOURCE_FILTER_UPLOAD_TYPE)]
IngestionProjectFilter = Annotated[UUID, Query(description=DESC_SOURCE_INGESTION_FILTER_PROJECT)]
IngestionStatusFilter = Annotated[
    SourceIngestionStatus | None,
    Query(description=DESC_SOURCE_INGESTION_FILTER_STATUS),
]
IngestionSourceTypeFilter = Annotated[
    SourceType | None,
    Query(description=DESC_SOURCE_INGESTION_FILTER_SOURCE_TYPE),
]

# ── Bulk-upload form aliases ─────────────────────────────────────────────────

BulkFiles = Annotated[list[UploadFile], File(description=DESC_SOURCE_FILES)]
BulkProjectId = Annotated[UUID, Form(description=DESC_SOURCE_PROJECT_ID)]
BulkSourceType = Annotated[
    Literal["rfp", "additional_rfp", "source_code", "meeting_notes", "requirement_update"],
    Form(description=DESC_SOURCE_TYPE),
]
BulkDescription = Annotated[str | None, Form(max_length=2_000, description=DESC_SOURCE_DESCRIPTION)]
BulkSourceLanguage = Annotated[str | None, Form(max_length=255)]
BulkFrontendStack = Annotated[str | None, Form(max_length=255)]
BulkBackendStack = Annotated[str | None, Form(max_length=255)]
BulkInfraStack = Annotated[str | None, Form(max_length=255)]
BulkArchStack = Annotated[str | None, Form(max_length=255)]
BulkDatabaseStack = Annotated[str | None, Form(max_length=255)]
BulkCodingStandard = Annotated[str | None, Form(max_length=255)]
BulkDatabaseStrategy = Annotated[str | None, Form(max_length=255)]
BulkArchitecture = Annotated[str | None, Form(max_length=255)]
BulkSecurity = Annotated[str | None, Form(max_length=255)]
BulkSourceLayoutType = Annotated[
    Literal["modular", "flat", "auto"] | None,
    Form(
        description=(
            "Layout shape of the analyzed source code. Send the value shown "
            "after '=>': Modular => modular, Non Modular => flat, "
            "Unknown => auto. Only applies when source_type is 'source_code'."
        )
    ),
]
BulkSkipProcessing = Annotated[
    Literal["True", "False"],
    Form(
        description=(
            "Skip full pipeline processing and use sample artifacts/module results when available. "
            "Send as string: True or False. Default is False."
        )
    ),
]
BulkUserMessage = Annotated[
    str,
    Form(
        max_length=10_000,
        description="Optional user message providing additional context for the incremental update. Only used when is_incremental is True.",
    ),
]
BulkIsIncremental = Annotated[
    Literal["True", "False"],
    Form(
        description=(
            "When True, routes the upload through the incremental update pipeline. "
            "Send as string: True or False. Default is False."
        )
    ),
]
BulkContextMode = Annotated[
    Literal["full", "subset"],
    Form(
        description=(
            "Backlog context given to the incremental update LLM. 'full' sends the "
            "complete backlog; 'subset' sends a selector-filtered slice. Only used "
            "when is_incremental is True. Default is 'full'."
        )
    ),
]


IncrementalFiles = Annotated[
    list[UploadFile],
    File(
        description="PDF and/or image files (JPEG, PNG, WebP) to parse and use for the incremental backlog update."
    ),
]
IncrementalProjectId = Annotated[UUID, Form(description=DESC_SOURCE_PROJECT_ID)]
IncrementalSourceType = Annotated[
    Literal["rfp", "additional_rfp", "source_code", "meeting_notes", "requirement_update"],
    Form(description=DESC_SOURCE_TYPE),
]
IncrementalDescription = Annotated[
    str | None, Form(max_length=2_000, description=DESC_SOURCE_DESCRIPTION)
]
IncrementalUserMessage = Annotated[
    str,
    Form(
        max_length=10_000,
        description="Optional user message providing additional context for the incremental update.",
    ),
]
IncrementalSkipProc = Annotated[
    Literal["True", "False"],
    Form(
        description=(
            "Skip the AI pipeline and return cached output when available. "
            "Send as string: True or False. Default is False."
        )
    ),
]
IncrementalContextMode = Annotated[
    Literal["full", "subset"],
    Form(
        description=(
            "Backlog context given to the incremental update LLM. 'full' sends the "
            "complete backlog; 'subset' sends a selector-filtered slice. Default is 'full'."
        )
    ),
]


def _parse_bool_form_field(value: Literal["True", "False"]) -> bool:
    return value == "True"


def _parse_bulk_source_context(
    source_language: BulkSourceLanguage = None,
    frontend_stack: BulkFrontendStack = None,
    backend_stack: BulkBackendStack = None,
    infrastructure_stack: BulkInfraStack = None,
    architecture_stack: BulkArchStack = None,
    database_stack: BulkDatabaseStack = None,
    coding_standard: BulkCodingStandard = None,
    database_strategy: BulkDatabaseStrategy = None,
    architecture: BulkArchitecture = None,
    security: BulkSecurity = None,
    source_layout_type: BulkSourceLayoutType = None,
) -> SourceIngestionContextFields:
    return SourceIngestionContextFields(
        source_language=source_language,
        frontend_stack=frontend_stack,
        backend_stack=backend_stack,
        infrastructure_stack=infrastructure_stack,
        architecture_stack=architecture_stack,
        database_stack=database_stack,
        coding_standard=coding_standard,
        database_strategy=database_strategy,
        architecture=architecture,
        security=security,
        source_layout_type=source_layout_type,
    )


BulkSourceContext = Annotated[SourceIngestionContextFields, Depends(_parse_bulk_source_context)]


# ── Handlers ─────────────────────────────────────────────────────────────────


@router.get(
    "/download/{source_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_SOURCE_DOWNLOAD,
)
@limiter.limit(source_download_limit)
async def download_single_file_from_s3(
    request: Request,
    source_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
) -> StreamingResponse:
    """GET /sources/download/{source_id} — stream a file from S3 to the client.

    Files are streamed directly from S3 in configurable chunks and never
    written to local disk, so container storage is not consumed regardless
    of file size.
    """
    source = SourceService().get_source_for_download(
        source_id=source_id,
        uow=uow,
        requester_id=current_user.id,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
    )
    return StreamingResponse(
        iter_s3_chunks(source.storage_key),
        media_type=source.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{source.original_name}"',
            "Content-Length": str(source.file_size_bytes),
        },
    )


@router.get(
    "/download/local/{source_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_SOURCE_AI_DOWNLOAD,
)
@limiter.limit(source_download_limit)
async def download_single_file_in_local(
    request: Request,
    source_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
) -> ApiResponse[AiSourceDownloadResponse]:
    """GET /sources/download/local/{source_id} — prepare local file for AI jobs."""
    local_path, content_type, original_name = await SourceService().download_single_file_in_local(
        source_id=source_id,
        uow=uow,
        requester_id=current_user.id,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
    )
    payload = AiSourceDownloadResponse(
        local_path=local_path,
        content_type=content_type,
        original_name=original_name,
    )
    return ApiResponse.ok(data=payload, message=MSG_SOURCE_AI_DOWNLOAD_READY)


@router.post(
    "/upload/bulk",
    status_code=status.HTTP_207_MULTI_STATUS,
    summary=SUMMARY_SOURCE_BULK_UPLOAD,
    openapi_extra=BULK_UPLOAD_OPENAPI_EXTRA,
)
@limiter.limit(source_upload_limit)
async def upload_bulk(
    request: Request,
    current_user: CurrentUser,
    uow: CurrentUow,
    files: BulkFiles,
    project_id: BulkProjectId,
    source_type: BulkSourceType,
    source_context: BulkSourceContext,
    description: BulkDescription = None,
    user_message: BulkUserMessage = "",
    is_incremental: BulkIsIncremental = "False",
    skip_processing: BulkSkipProcessing = "False",
    context_mode: BulkContextMode = ContextMode.FULL.value,
) -> ApiResponse[BulkUploadResponse | IncrementalBulkUploadResponse]:
    """POST /sources/upload/bulk — upload multiple files in one request.

    When ``is_incremental`` is ``True`` the upload is routed through the
    incremental update pipeline (same behaviour as ``/upload/bulk/incremental``)
    — the routing decision itself lives in
    :meth:`SourceService.upload_bulk_or_incremental`, not here.
    """
    with bind_log_context(project_id=str(project_id)):
        result = await SourceService().upload_bulk_or_incremental(
            is_incremental=_parse_bool_form_field(is_incremental),
            files=files,
            project_id=project_id,
            description=description,
            source_type=source_type,
            context=source_context,
            user_message=user_message,
            skip_processing=_parse_bool_form_field(skip_processing),
            context_mode=context_mode,
            uploader_id=current_user.id,
            uow=uow,
            requester_roles=current_user.role_names,
            requester_tenant_id=current_user.tenant_id,
        )
        message = (
            MSG_SOURCE_INCREMENTAL_BULK_PROCESSED
            if isinstance(result, IncrementalBulkUploadResponse)
            else MSG_SOURCE_BULK_PROCESSED
        )
        return ApiResponse.ok(data=result, message=message)


@router.post(
    "/upload/bulk/incremental",
    status_code=status.HTTP_207_MULTI_STATUS,
    summary=SUMMARY_SOURCE_INCREMENTAL_BULK_UPLOAD,
    openapi_extra=INCREMENTAL_BULK_UPLOAD_OPENAPI_EXTRA,
)
@limiter.limit(source_upload_limit)
async def upload_bulk_incremental(
    request: Request,
    current_user: CurrentUser,
    uow: CurrentUow,
    files: IncrementalFiles,
    project_id: IncrementalProjectId,
    source_type: IncrementalSourceType,
    description: IncrementalDescription = None,
    user_message: IncrementalUserMessage = "",
    skip_processing: IncrementalSkipProc = "False",
    context_mode: IncrementalContextMode = ContextMode.FULL.value,
) -> ApiResponse[IncrementalBulkUploadResponse]:
    """POST /sources/upload/bulk/incremental — upload PDF/image files and queue
    the incremental backlog update pipeline as a background Celery task.

    Accepted file types: PDF, JPEG, JPG, PNG, WebP.

    Returns immediately with ``task_id``.  Connect to
    ``/ws/projects/{project_id}`` to receive real-time progress updates
    (stages: ``incremental_update.started`` → ``incremental_update.completed``).
    """
    skip_processing_bool = _parse_bool_form_field(skip_processing)

    result = await IncrementalBulkUploadService().upload_and_enqueue(
        files=files,
        project_id=project_id,
        description=description,
        source_type=source_type,
        user_message=user_message,
        skip_processing=skip_processing_bool,
        context_mode=context_mode,
        uploader_id=current_user.id,
        uow=uow,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
    )
    return ApiResponse.ok(data=result, message=MSG_SOURCE_INCREMENTAL_BULK_PROCESSED)


@router.post(
    "/upload/link",
    status_code=status.HTTP_201_CREATED,
    summary=SUMMARY_SOURCE_LINK_UPLOAD,
)
@limiter.limit(source_upload_limit)
async def upload_from_link(
    request: Request,
    payload: LinkUploadRequest,
    current_user: CurrentUser,
    uow: CurrentUow,
) -> ApiResponse[SourceResponse]:
    """POST /sources/upload/link — download and store a source from a public URL.

    Supported URL types: GitHub repos/files, SharePoint, Nextcloud, direct HTTPS.
    Only HTTPS URLs are accepted.
    """
    result = await SourceService().upload_from_link(
        link_url=str(payload.link_url),
        project_id=payload.project_id,
        description=payload.description,
        source_type=payload.source_type.value,
        uploader_id=current_user.id,
        uow=uow,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
    )
    return ApiResponse.ok(data=result, message=MSG_SOURCE_LINK_UPLOADED)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_SOURCE_LIST,
)
def list_sources(
    project_id: ProjectIdFilter,
    uow: CurrentUow,
    current_user: CurrentUser,
    pagination: CurrentPaging,
    status_filter: StatusFilter = None,
    file_type: FileTypeFilter = None,
    upload_type: UploadTypeFilter = None,
) -> ApiResponse[SourceListResponse]:
    """GET /sources — paginated, filterable list of sources for a project."""
    result = SourceService().list_sources(
        project_id=project_id,
        skip=pagination.skip,
        limit=pagination.limit,
        status=status_filter,
        file_type=file_type,
        upload_type=upload_type,
        uow=uow,
        requester_id=current_user.id,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
    )
    return ApiResponse.ok(data=result)


@router.get(
    "/ingestion",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_SOURCE_INGESTION_LIST,
)
def list_source_ingestions(
    project_id: IngestionProjectFilter,
    uow: CurrentUow,
    current_user: CurrentUser,
    pagination: CurrentPaging,
    status_filter: IngestionStatusFilter = None,
    source_type_filter: IngestionSourceTypeFilter = None,
) -> ApiResponse[SourceIngestionListResponse]:
    """GET /sources/ingestion — paginated, project-scoped ingestion listing with filters."""
    result = SourceIngestionService().list_source_ingestions(
        project_id=project_id,
        skip=pagination.skip,
        limit=pagination.limit,
        status=status_filter.value if status_filter else None,
        source_type=source_type_filter.value if source_type_filter else None,
        uow=uow,
        requester_id=current_user.id,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
    )
    return ApiResponse.ok(data=result)


@router.delete(
    "/delete/bulk",
    status_code=status.HTTP_207_MULTI_STATUS,
    summary=SUMMARY_SOURCE_BULK_DELETE,
)
@limiter.limit(source_delete_limit)
async def bulk_delete_sources(
    request: Request,
    payload: BulkDeleteRequest,
    current_user: CurrentUser,
    uow: CurrentUow,
) -> ApiResponse[BulkDeleteResponse]:
    """DELETE /sources/delete/bulk — soft-delete multiple sources in one request."""
    result = await SourceService().bulk_delete_sources(
        source_ids=payload.source_ids,
        requester_id=current_user.id,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
        uow=uow,
    )
    return ApiResponse.ok(data=result, message=MSG_SOURCE_BULK_DELETE_PROCESSED)


@router.get(
    "/{source_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_SOURCE_GET,
)
def get_source(
    source_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
) -> ApiResponse[SourceResponse]:
    """GET /sources/{source_id} — fetch a single source record."""
    result = SourceService().get_source(
        source_id=source_id,
        uow=uow,
        requester_id=current_user.id,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
    )
    return ApiResponse.ok(data=result)


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary=SUMMARY_SOURCE_DELETE,
)
@limiter.limit(source_delete_limit)
async def delete_source(
    request: Request,
    source_id: UUID,
    current_user: CurrentUser,
    uow: CurrentUow,
) -> None:
    """DELETE /sources/{source_id} — soft-delete; uploader, or project write access, only."""
    await SourceService().delete_source(
        source_id=source_id,
        requester_id=current_user.id,
        requester_roles=current_user.role_names,
        requester_tenant_id=current_user.tenant_id,
        uow=uow,
    )
