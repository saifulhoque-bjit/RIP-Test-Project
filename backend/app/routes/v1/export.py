"""Route for the project export feature.

Unlike every other route in this API, the response here is a raw ZIP file
(``Response`` with ``application/zip``), not the standard ``ApiResponse``
envelope — matching the precedent set by ``sources.py``'s
``download_single_file_from_s3``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from app.core.messages import SUMMARY_PROJECT_EXPORT
from app.core.rate_limiter import export_limit, limiter
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_export_service, get_uow
from app.models.postgres.user_model import User
from app.schemas.export_schema import ExportRequest
from app.services.export_service import ExportService

router = APIRouter(prefix="/projects/{project_id}/export", tags=["Export"])

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
ExportServiceDep = Annotated[ExportService, Depends(get_export_service)]


def _safe_filename_part(value: str) -> str:
    """Strip characters that would break a Content-Disposition filename."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in value).strip("_") or "project"


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_PROJECT_EXPORT,
)
@limiter.limit(export_limit)
async def export_project(
    request: Request,
    project_id: UUID,
    payload: ExportRequest,
    current_user: CurrentUser,
    uow: CurrentUow,
    service: ExportServiceDep,
) -> Response:
    """POST /projects/{project_id}/export — build and stream a ZIP export.

    Synchronous by design: the ZIP is assembled in-memory and returned in the
    same request/response cycle, never persisted server-side.
    """
    zip_bytes, project_name = await service.build_export_zip(
        project_id=project_id,
        user_email=current_user.email,
        request=payload,
        uow=uow,
    )
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    filename = f"{_safe_filename_part(project_name)}_export_{timestamp}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
