"""Unit tests for routes/v1/sources.py.

Tests verify that each route handler:
- Delegates to the correct service method with the parsed request data
- Wraps the result in ``ApiResponse[T]`` (or returns the raw ``StreamingResponse``
  for the S3-download endpoint)
- Carries the ``@limiter.limit(...)`` rate-limit decorator where required

``SourceService`` / ``IncrementalBulkUploadService`` / ``SourceIngestionService``
are mocked entirely — their business logic is covered by
``tests/test_source_service.py`` and friends. Handlers are called directly
(matching ``tests/test_auth_routes.py`` / ``tests/test_project_routes.py``),
not through a full ``TestClient``, since sources.py's upload endpoints take
``UploadFile``/``Form`` params that are trivial to pass as plain Python values
in a direct call but awkward to multipart-encode through a client.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from fastapi.responses import StreamingResponse
import pytest
from starlette.requests import Request

from app.routes.v1.sources import (
    _parse_bulk_source_context,
    bulk_delete_sources,
    delete_source,
    download_single_file_from_s3,
    download_single_file_in_local,
    get_source,
    list_source_ingestions,
    list_sources,
    upload_bulk,
    upload_bulk_incremental,
    upload_from_link,
)
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
from app.services.source_service import SourceIngestionContextFields
from tests.conftest import make_source, make_user

# Handlers that MUST carry @limiter.limit(...) per security-guardrails.md
# (upload/download endpoints, plus the abuse-prone delete endpoints) —
# functools.wraps inside slowapi's decorator leaves a `__wrapped__` attribute
# we can assert on without a live request.
RATE_LIMITED_HANDLERS = [
    download_single_file_from_s3,
    download_single_file_in_local,
    upload_bulk,
    upload_bulk_incremental,
    upload_from_link,
    bulk_delete_sources,
    delete_source,
]


def _make_request(method: str = "GET") -> Request:
    """Minimal real starlette Request — required by the SlowAPI rate-limiter wrapper."""
    return Request(
        scope={"type": "http", "method": method, "path": "/", "query_string": b"", "headers": []}
    )


def _context() -> SourceIngestionContextFields:
    return SourceIngestionContextFields()


def _source_response(**kwargs) -> SourceResponse:
    return SourceResponse.model_validate(make_source(**kwargs))


# ── Rate-limit wiring (cross-cutting) ───────────────────────────────────────


class TestRateLimiting:
    def test_upload_download_and_delete_endpoints_are_rate_limited(self) -> None:
        for handler in RATE_LIMITED_HANDLERS:
            assert hasattr(handler, "__wrapped__"), (
                f"{handler.__name__} must be decorated with @limiter.limit(...)"
            )


# ── Bulk-upload source-context dependency factory ───────────────────────────


class TestParseBulkSourceContext:
    def test_maps_each_form_field_onto_the_dataclass_by_name(self) -> None:
        context = _parse_bulk_source_context(
            source_language="Python",
            frontend_stack="React",
            backend_stack="FastAPI",
            infrastructure_stack="AWS",
            architecture_stack="Microservices",
            database_stack="Postgres",
            coding_standard="PEP8",
            database_strategy="CQRS",
            architecture="Layered",
            security="OAuth2",
            source_layout_type="modular",
        )

        assert context == SourceIngestionContextFields(
            source_language="Python",
            frontend_stack="React",
            backend_stack="FastAPI",
            infrastructure_stack="AWS",
            architecture_stack="Microservices",
            database_stack="Postgres",
            coding_standard="PEP8",
            database_strategy="CQRS",
            architecture="Layered",
            security="OAuth2",
            source_layout_type="modular",
        )

    def test_defaults_to_all_none_when_no_fields_supplied(self) -> None:
        context = _parse_bulk_source_context()

        assert context == SourceIngestionContextFields()


# ── GET /sources/download/{source_id} ───────────────────────────────────────


class TestDownloadSingleFileFromS3:
    @pytest.mark.asyncio
    async def test_streams_file_from_s3(self) -> None:
        user = make_user()
        uow = MagicMock()
        source_id = uuid.uuid4()
        source = make_source(
            storage_key="sources/proj/file.pdf",
        )
        source.mime_type = "application/pdf"
        source.original_name = "file.pdf"
        source.file_size_bytes = 2048

        with patch(
            "app.routes.v1.sources.SourceService.get_source_for_download",
            return_value=source,
        ) as mock_get:
            response = await download_single_file_from_s3(
                request=_make_request(),
                source_id=source_id,
                current_user=user,
                uow=uow,
            )

        mock_get.assert_called_once_with(
            source_id=source_id,
            uow=uow,
            requester_id=user.id,
            requester_roles=user.role_names,
            requester_tenant_id=user.tenant_id,
        )
        assert isinstance(response, StreamingResponse)
        assert response.media_type == "application/pdf"
        assert response.headers["content-disposition"] == 'attachment; filename="file.pdf"'
        assert response.headers["content-length"] == "2048"


# ── GET /sources/download/local/{source_id} ─────────────────────────────────


class TestDownloadSingleFileInLocal:
    @pytest.mark.asyncio
    async def test_delegates_to_service_and_wraps_response(self) -> None:
        user = make_user()
        uow = MagicMock()
        source_id = uuid.uuid4()

        with patch(
            "app.routes.v1.sources.SourceService.download_single_file_in_local",
            new=AsyncMock(return_value=("/data/local.pdf", "application/pdf", "local.pdf")),
        ) as mock_download:
            result = await download_single_file_in_local(
                request=_make_request(),
                source_id=source_id,
                current_user=user,
                uow=uow,
            )

        mock_download.assert_awaited_once_with(
            source_id=source_id,
            uow=uow,
            requester_id=user.id,
            requester_roles=user.role_names,
            requester_tenant_id=user.tenant_id,
        )
        assert result.success is True
        assert isinstance(result.data, AiSourceDownloadResponse)
        assert result.data.local_path == "/data/local.pdf"
        assert result.data.content_type == "application/pdf"
        assert result.data.original_name == "local.pdf"


# ── POST /sources/upload/bulk ────────────────────────────────────────────────


class TestUploadBulk:
    @pytest.mark.asyncio
    async def test_non_incremental_delegates_to_source_service(self) -> None:
        user = make_user()
        uow = MagicMock()
        project_id = uuid.uuid4()
        files = [MagicMock(name="file1")]
        context = _context()
        response_payload = BulkUploadResponse(
            batch_id=uuid.uuid4(), total=1, succeeded=1, failed=0, results=[]
        )

        with (
            patch(
                "app.routes.v1.sources.SourceService.upload_bulk",
                new=AsyncMock(return_value=response_payload),
            ) as mock_upload,
            patch(
                "app.routes.v1.sources.IncrementalBulkUploadService.upload_and_enqueue",
                new=AsyncMock(),
            ) as mock_incremental,
        ):
            result = await upload_bulk(
                request=_make_request("POST"),
                current_user=user,
                uow=uow,
                files=files,
                project_id=project_id,
                source_type="rfp",
                source_context=context,
                description="desc",
                user_message="",
                is_incremental="False",
                skip_processing="False",
                context_mode="full",
            )

        mock_upload.assert_awaited_once_with(
            files=files,
            project_id=project_id,
            description="desc",
            source_type="rfp",
            context=context,
            user_message="",
            skip_processing=False,
            uploader_id=user.id,
            uow=uow,
            requester_roles=user.role_names,
            requester_tenant_id=user.tenant_id,
        )
        mock_incremental.assert_not_awaited()
        assert result.success is True
        assert result.data is response_payload

    @pytest.mark.asyncio
    async def test_incremental_flag_routes_to_incremental_service(self) -> None:
        user = make_user()
        uow = MagicMock()
        project_id = uuid.uuid4()
        files = [MagicMock(name="file1")]
        context = _context()
        response_payload = IncrementalBulkUploadResponse(
            batch_id=uuid.uuid4(), task_id="task-1", total=1, succeeded=1, failed=0, results=[]
        )

        with (
            patch(
                "app.routes.v1.sources.SourceService.upload_bulk", new=AsyncMock()
            ) as mock_upload,
            patch(
                "app.routes.v1.sources.IncrementalBulkUploadService.upload_and_enqueue",
                new=AsyncMock(return_value=response_payload),
            ) as mock_incremental,
        ):
            result = await upload_bulk(
                request=_make_request("POST"),
                current_user=user,
                uow=uow,
                files=files,
                project_id=project_id,
                source_type="rfp",
                source_context=context,
                description=None,
                user_message="context please",
                is_incremental="True",
                skip_processing="True",
                context_mode="subset",
            )

        mock_incremental.assert_awaited_once_with(
            files=files,
            project_id=project_id,
            description=None,
            source_type="rfp",
            user_message="context please",
            skip_processing=True,
            context_mode="subset",
            uploader_id=user.id,
            uow=uow,
            requester_roles=user.role_names,
            requester_tenant_id=user.tenant_id,
        )
        mock_upload.assert_not_awaited()
        assert result.success is True
        assert result.data is response_payload


# ── POST /sources/upload/bulk/incremental ───────────────────────────────────


class TestUploadBulkIncremental:
    @pytest.mark.asyncio
    async def test_delegates_to_incremental_service(self) -> None:
        user = make_user()
        uow = MagicMock()
        project_id = uuid.uuid4()
        files = [MagicMock(name="file1")]
        response_payload = IncrementalBulkUploadResponse(
            batch_id=uuid.uuid4(), task_id="task-2", total=1, succeeded=1, failed=0, results=[]
        )

        with patch(
            "app.routes.v1.sources.IncrementalBulkUploadService.upload_and_enqueue",
            new=AsyncMock(return_value=response_payload),
        ) as mock_incremental:
            result = await upload_bulk_incremental(
                request=_make_request("POST"),
                current_user=user,
                uow=uow,
                files=files,
                project_id=project_id,
                source_type="meeting_notes",
                description="notes",
                user_message="",
                skip_processing="False",
                context_mode="full",
            )

        mock_incremental.assert_awaited_once_with(
            files=files,
            project_id=project_id,
            description="notes",
            source_type="meeting_notes",
            user_message="",
            skip_processing=False,
            context_mode="full",
            uploader_id=user.id,
            uow=uow,
            requester_roles=user.role_names,
            requester_tenant_id=user.tenant_id,
        )
        assert result.success is True
        assert result.data is response_payload


# ── POST /sources/upload/link ────────────────────────────────────────────────


class TestUploadFromLink:
    @pytest.mark.asyncio
    async def test_delegates_to_source_service(self) -> None:
        user = make_user()
        uow = MagicMock()
        payload = LinkUploadRequest(
            project_id=uuid.uuid4(),
            link_url="https://github.com/owner/repo",
            source_type="source_code",
            description="repo import",
        )
        response_payload = _source_response(project_id=payload.project_id)

        with patch(
            "app.routes.v1.sources.SourceService.upload_from_link",
            new=AsyncMock(return_value=response_payload),
        ) as mock_upload:
            result = await upload_from_link(
                request=_make_request("POST"),
                payload=payload,
                current_user=user,
                uow=uow,
            )

        mock_upload.assert_awaited_once_with(
            link_url=str(payload.link_url),
            project_id=payload.project_id,
            description=payload.description,
            source_type=payload.source_type.value,
            uploader_id=user.id,
            uow=uow,
            requester_roles=user.role_names,
            requester_tenant_id=user.tenant_id,
        )
        assert result.success is True
        assert result.data is response_payload


# ── GET /sources ──────────────────────────────────────────────────────────


class TestListSources:
    def test_delegates_to_source_service(self) -> None:
        user = make_user()
        uow = MagicMock()
        project_id = uuid.uuid4()
        pagination = MagicMock(skip=0, limit=20)
        response_payload = SourceListResponse(items=[], total=0, skip=0, limit=20)

        with patch(
            "app.routes.v1.sources.SourceService.list_sources",
            return_value=response_payload,
        ) as mock_list:
            result = list_sources(
                project_id=project_id,
                uow=uow,
                current_user=user,
                pagination=pagination,
                status_filter="uploaded",
                file_type="PDF",
                upload_type="single",
            )

        mock_list.assert_called_once_with(
            project_id=project_id,
            skip=0,
            limit=20,
            status="uploaded",
            file_type="PDF",
            upload_type="single",
            uow=uow,
            requester_id=user.id,
            requester_roles=user.role_names,
            requester_tenant_id=user.tenant_id,
        )
        assert result.success is True
        assert result.data is response_payload


# ── GET /sources/ingestion ──────────────────────────────────────────────────


class TestListSourceIngestions:
    def test_delegates_to_source_ingestion_service(self) -> None:
        user = make_user()
        uow = MagicMock()
        project_id = uuid.uuid4()
        pagination = MagicMock(skip=0, limit=20)
        response_payload = SourceIngestionListResponse(items=[], total=0, skip=0, limit=20)

        with patch(
            "app.routes.v1.sources.SourceIngestionService.list_source_ingestions",
            return_value=response_payload,
        ) as mock_list:
            result = list_source_ingestions(
                project_id=project_id,
                uow=uow,
                current_user=user,
                pagination=pagination,
                status_filter=None,
                source_type_filter=None,
            )

        mock_list.assert_called_once_with(
            project_id=project_id,
            skip=0,
            limit=20,
            status=None,
            source_type=None,
            uow=uow,
            requester_id=user.id,
            requester_roles=user.role_names,
            requester_tenant_id=user.tenant_id,
        )
        assert result.success is True
        assert result.data is response_payload

    def test_enum_filters_are_unwrapped_to_their_value(self) -> None:
        from app.core.enums.source_ingestion_status import SourceIngestionStatus
        from app.core.enums.source_type import SourceType

        user = make_user()
        uow = MagicMock()
        project_id = uuid.uuid4()
        pagination = MagicMock(skip=0, limit=20)
        response_payload = SourceIngestionListResponse(items=[], total=0, skip=0, limit=20)

        with patch(
            "app.routes.v1.sources.SourceIngestionService.list_source_ingestions",
            return_value=response_payload,
        ) as mock_list:
            list_source_ingestions(
                project_id=project_id,
                uow=uow,
                current_user=user,
                pagination=pagination,
                status_filter=SourceIngestionStatus.COMPLETED,
                source_type_filter=SourceType.RFP,
            )

        _, kwargs = mock_list.call_args
        assert kwargs["status"] == SourceIngestionStatus.COMPLETED.value
        assert kwargs["source_type"] == SourceType.RFP.value


# ── DELETE /sources/delete/bulk ──────────────────────────────────────────────


class TestBulkDeleteSources:
    @pytest.mark.asyncio
    async def test_delegates_to_source_service(self) -> None:
        user = make_user()
        uow = MagicMock()
        source_ids = [uuid.uuid4(), uuid.uuid4()]
        payload = BulkDeleteRequest(source_ids=source_ids)
        response_payload = BulkDeleteResponse(total=2, succeeded=2, failed=0, results=[])

        with patch(
            "app.routes.v1.sources.SourceService.bulk_delete_sources",
            new=AsyncMock(return_value=response_payload),
        ) as mock_bulk_delete:
            result = await bulk_delete_sources(
                request=_make_request("DELETE"),
                payload=payload,
                current_user=user,
                uow=uow,
            )

        mock_bulk_delete.assert_awaited_once_with(
            source_ids=source_ids,
            requester_id=user.id,
            requester_roles=user.role_names,
            requester_tenant_id=user.tenant_id,
            uow=uow,
        )
        assert result.success is True
        assert result.data is response_payload


# ── GET /sources/{source_id} ─────────────────────────────────────────────────


class TestGetSource:
    def test_delegates_to_source_service(self) -> None:
        user = make_user()
        uow = MagicMock()
        source_id = uuid.uuid4()
        response_payload = _source_response()

        with patch(
            "app.routes.v1.sources.SourceService.get_source",
            return_value=response_payload,
        ) as mock_get:
            result = get_source(
                source_id=source_id,
                current_user=user,
                uow=uow,
            )

        mock_get.assert_called_once_with(
            source_id=source_id,
            uow=uow,
            requester_id=user.id,
            requester_roles=user.role_names,
            requester_tenant_id=user.tenant_id,
        )
        assert result.success is True
        assert result.data is response_payload


# ── DELETE /sources/{source_id} ──────────────────────────────────────────────


class TestDeleteSource:
    @pytest.mark.asyncio
    async def test_delegates_to_source_service(self) -> None:
        user = make_user()
        uow = MagicMock()
        source_id = uuid.uuid4()

        with patch(
            "app.routes.v1.sources.SourceService.delete_source",
            new=AsyncMock(return_value=None),
        ) as mock_delete:
            result = await delete_source(
                request=_make_request("DELETE"),
                source_id=source_id,
                current_user=user,
                uow=uow,
            )

        mock_delete.assert_awaited_once_with(
            source_id=source_id,
            requester_id=user.id,
            requester_roles=user.role_names,
            requester_tenant_id=user.tenant_id,
            uow=uow,
        )
        assert result is None
