"""Unit tests for routes/v1/projects.py.

Tests verify that route handlers:
- Correctly delegate to ProjectService
- Wrap results in ApiResponse
- Pass the right arguments

ProjectService is mocked entirely — business logic is covered by
test_project_service.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest

from app.routes.v1.projects import (
    create_project,
    delete_project,
    get_project,
    list_all_projects,
    list_projects,
    list_projects_summary,
    update_project,
)
from app.schemas.project_schema import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectSummaryResponse,
    ProjectUpdate,
)
from tests.conftest import make_user

# ── Helpers ────────────────────────────────────────────────────────────────


def _response(**kwargs) -> ProjectResponse:
    defaults = {
        "id": uuid.uuid4(),
        "name": "Alpha",
        "code": "ALPHA-0001",
        "description": None,
        "llm_provider": None,
        "llm_model": None,
        "project_type": None,
        "status": "active",
        "files": 0,
        "user_stories": 0,
        "team_members": 0,
        "owner_id": uuid.uuid4(),
        "version": 1,
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
    }
    defaults.update(kwargs)
    return ProjectResponse(**defaults)


def _list_response() -> ProjectListResponse:
    return ProjectListResponse(items=[_response()], total=1, skip=0, limit=20)


def _summary_list() -> list[ProjectSummaryResponse]:
    return [ProjectSummaryResponse(id=uuid.uuid4(), name="Alpha", files=0)]


def _make_service(overrides: dict | None = None) -> MagicMock:
    svc = MagicMock()
    svc.create_project = MagicMock(return_value=_response())
    svc.list_projects = MagicMock(return_value=_list_response())
    svc.list_all_projects = MagicMock(return_value=_list_response())
    svc.list_projects_summary = MagicMock(return_value=_summary_list())
    svc.get_project = AsyncMock(return_value=_response())
    svc.update_project = MagicMock(return_value=_response())
    svc.delete_project = MagicMock(return_value=None)
    if overrides:
        for k, v in overrides.items():
            setattr(svc, k, v)
    return svc


# ── Tests ──────────────────────────────────────────────────────────────────


def test_create_project_delegates_to_service() -> None:
    user = make_user()
    uow = MagicMock()
    service = _make_service()
    payload = ProjectCreate(
        name="Alpha",
        description="d",
        llm_provider="anthropic",
        llm_model="claude_sonnet_4_6",
        project_type="rfp",
    )

    result = create_project(payload, current_user=user, uow=uow, service=service)

    service.create_project.assert_called_once_with(
        payload=payload,
        uow=uow,
        owner_id=user.id,
        requester_roles=user.role_names,
        owner_tenant_id=user.tenant_id,
    )
    assert result.success is True
    assert result.data is not None
    assert result.data.name == "Alpha"


def test_list_projects_delegates_to_service() -> None:
    user = make_user()
    pagination = MagicMock(skip=0, limit=20)
    uow = MagicMock()
    service = _make_service()

    result = list_projects(
        search=None,
        pagination=pagination,
        current_user=user,
        uow=uow,
        service=service,
    )

    service.list_projects.assert_called_once_with(
        owner_id=user.id, skip=0, limit=20, search=None, uow=uow
    )
    assert result.success is True
    assert result.data.total == 1


def test_list_all_projects_delegates_to_service() -> None:
    user = make_user()
    pagination = MagicMock(skip=0, limit=20)
    uow = MagicMock()
    service = _make_service()

    result = list_all_projects(
        search=None,
        pagination=pagination,
        current_user=user,
        uow=uow,
        service=service,
    )

    service.list_all_projects.assert_called_once_with(
        skip=0,
        limit=20,
        search=None,
        uow=uow,
        requester_roles=user.role_names,
        requester_tenant_id=user.tenant_id,
        tenant_id=None,
    )
    assert result.success is True


def test_list_projects_summary_delegates_to_service() -> None:
    user = make_user()
    uow = MagicMock()
    service = _make_service()

    result = list_projects_summary(
        search=None,
        stage=None,
        current_user=user,
        uow=uow,
        service=service,
    )

    service.list_projects_summary.assert_called_once_with(
        uow=uow,
        requester_id=user.id,
        requester_roles=user.role_names,
        requester_tenant_id=user.tenant_id,
        search=None,
        stage=None,
    )
    assert result.success is True
    assert len(result.data) == 1


@pytest.mark.asyncio
async def test_get_project_delegates_to_service() -> None:
    user = make_user()
    project_id = uuid.uuid4()
    uow = MagicMock()
    service = _make_service()

    result = await get_project(project_id=project_id, current_user=user, uow=uow, service=service)

    service.get_project.assert_called_once_with(
        project_id=project_id,
        requester_id=user.id,
        requester_roles=[r.name for r in user.roles],
        requester_tenant_id=user.tenant_id,
        uow=uow,
    )
    assert result.success is True


def test_update_project_delegates_to_service() -> None:
    user = make_user()
    role = MagicMock()
    role.name = "admin"
    user.roles = [role]
    project_id = uuid.uuid4()
    uow = MagicMock()
    service = _make_service()
    payload = ProjectUpdate(name="Beta")

    result = update_project(
        project_id=project_id,
        payload=payload,
        current_user=user,
        uow=uow,
        service=service,
    )

    service.update_project.assert_called_once_with(
        project_id=project_id,
        payload=payload,
        requester_id=user.id,
        requester_roles=["admin"],
        requester_tenant_id=user.tenant_id,
        uow=uow,
    )
    assert result.success is True


def test_delete_project_delegates_to_service() -> None:
    user = make_user()
    role = MagicMock()
    role.name = "pm"
    user.roles = [role]
    project_id = uuid.uuid4()
    uow = MagicMock()
    service = _make_service()

    delete_project(
        project_id=project_id,
        current_user=user,
        uow=uow,
        service=service,
    )

    service.delete_project.assert_called_once_with(
        project_id=project_id,
        requester_id=user.id,
        requester_roles=["pm"],
        requester_tenant_id=user.tenant_id,
        uow=uow,
    )
