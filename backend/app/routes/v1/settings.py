"""Route handlers for shared Setting resources — v1."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.messages import MSG_SETTINGS_ENUM_CATALOG_FETCHED, SUMMARY_SETTINGS_ENUM_CATALOG
from app.deps import get_current_db_user
from app.models.postgres.user_model import User
from app.schemas.setting_schema import EnumCatalogValue, SettingResponse
from app.services.enum_catalog_service import EnumCatalogService
from app.services.setting_service import SettingService
from app.utils.response import ApiResponse

router = APIRouter(prefix="/settings", tags=["Settings"])

CurrentUser = Annotated[User, Depends(get_current_db_user)]


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary="Get shared settings",
)
async def get_settings(
    _current_user: CurrentUser,
) -> ApiResponse[SettingResponse]:
    """GET /settings — return shared settings in the agreed JSON shape."""
    result = SettingService().get_or_seed_default_settings()
    return ApiResponse.ok(data=result, message="Settings fetched successfully")


@router.get(
    "/enums",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_SETTINGS_ENUM_CATALOG,
)
def get_enum_catalog(
    _current_user: CurrentUser,
) -> ApiResponse[dict[str, EnumCatalogValue]]:
    """GET /settings/enums — return every domain enum as a single JSON catalog."""
    result = EnumCatalogService().get_enum_catalog()
    return ApiResponse.ok(data=result, message=MSG_SETTINGS_ENUM_CATALOG_FETCHED)
