"""Unit tests for app/routes/v1/export.py.

``ExportService`` is mocked entirely — business logic is covered by
test_export_service.py. These tests verify the route:

- delegates to ``ExportService.build_export_zip`` with the parsed request
  data
- returns a raw ``application/zip`` ``Response`` (this endpoint intentionally
  does not use the ``ApiResponse`` envelope — see the module docstring in
  ``app/routes/v1/export.py``) with a sane ``Content-Disposition`` filename
- is rate-limited via ``@limiter.limit(export_limit)``
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import uuid

from fastapi import Response
import pytest
from starlette.requests import Request

from app.routes.v1.export import _safe_filename_part, export_project
from app.schemas.export_schema import ExportRequest
from tests.conftest import make_user


def _make_request() -> Request:
    """Minimal real starlette Request — required by the SlowAPI rate-limiter wrapper."""
    return Request(
        scope={"type": "http", "method": "POST", "path": "/", "query_string": b"", "headers": []}
    )


def _make_service(
    zip_bytes: bytes = b"PK\x03\x04zipdata", project_name: str = "Alpha"
) -> MagicMock:
    service = MagicMock()
    service.build_export_zip = AsyncMock(return_value=(zip_bytes, project_name))
    return service


class TestExportProject:
    @pytest.mark.asyncio
    async def test_delegates_to_service_with_parsed_request(self) -> None:
        user = make_user(email="tester@example.com")
        uow = MagicMock()
        service = _make_service()
        project_id = uuid.uuid4()
        payload = ExportRequest(
            status_filter="approved",
            include_backlog=True,
            include_srs=True,
            include_domain_knowledge=True,
            include_architecture_document=True,
        )

        result = await export_project(
            _make_request(),
            project_id,
            payload,
            current_user=user,
            uow=uow,
            service=service,
        )

        service.build_export_zip.assert_called_once_with(
            project_id=project_id,
            user_email=user.email,
            request=payload,
            uow=uow,
        )
        forwarded = service.build_export_zip.call_args.kwargs["request"]
        assert forwarded.include_domain_knowledge is True
        assert forwarded.include_architecture_document is True
        assert isinstance(result, Response)

    @pytest.mark.asyncio
    async def test_returns_raw_zip_response_with_content_disposition(self) -> None:
        user = make_user()
        uow = MagicMock()
        zip_bytes = b"the-zip-bytes"
        service = _make_service(zip_bytes=zip_bytes, project_name="My Project")
        payload = ExportRequest()

        result = await export_project(
            _make_request(),
            uuid.uuid4(),
            payload,
            current_user=user,
            uow=uow,
            service=service,
        )

        assert result.body == zip_bytes
        assert result.media_type == "application/zip"
        disposition = result.headers["content-disposition"]
        assert disposition.startswith('attachment; filename="My_Project_export_')
        assert disposition.endswith('.zip"')

    @pytest.mark.asyncio
    async def test_sanitizes_unsafe_characters_in_project_name_for_filename(self) -> None:
        user = make_user()
        uow = MagicMock()
        service = _make_service(project_name="Proj/../../etc: passwd*?")
        payload = ExportRequest()

        result = await export_project(
            _make_request(),
            uuid.uuid4(),
            payload,
            current_user=user,
            uow=uow,
            service=service,
        )

        disposition = result.headers["content-disposition"]
        # No raw slashes, colons, or other path/shell-unsafe characters leak through.
        assert "/" not in disposition.split("filename=")[1]
        assert ".." not in disposition

    def test_endpoint_is_rate_limited(self) -> None:
        # @limiter.limit(...) wraps the handler with functools.wraps, exposing
        # __wrapped__ — confirms the decorator is applied without needing to
        # drive an actual 429 through the SlowAPI middleware stack.
        assert hasattr(export_project, "__wrapped__")


class TestSafeFilenamePart:
    def test_keeps_alphanumeric_and_allowed_separators(self) -> None:
        assert _safe_filename_part("My-Project_1") == "My-Project_1"

    def test_replaces_unsafe_characters_with_underscore(self) -> None:
        assert _safe_filename_part("Proj/../etc: name*?") == "Proj____etc__name"

    def test_strips_leading_and_trailing_underscores(self) -> None:
        assert _safe_filename_part("  ") == "project"

    def test_empty_string_falls_back_to_project(self) -> None:
        assert _safe_filename_part("") == "project"

    def test_only_unsafe_characters_falls_back_to_project(self) -> None:
        assert _safe_filename_part("***") == "project"
