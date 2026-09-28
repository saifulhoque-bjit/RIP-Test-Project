"""Unit tests for the project-tasks WebSocket endpoint (`app/websockets/source_ws.py`).

Regression coverage for the ownership-check bypass: the handshake used to
accept any authenticated user once a project existed, with no check that the
requester owned it or was an admin. These tests pin the fixed behavior —
``_authorize_project_access`` enforces the same rule as
``GET /projects/{project_id}`` (``ProjectService._assert_owner_or_admin``).

No real WebSocket/Postgres connection is used, following the framework-object
mocking rule in ``.github/instructions/tests.instructions.md``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from fastapi import WebSocketDisconnect
import pytest

from app.core.exceptions import ForbiddenError, NotFoundError
from app.websockets.source_ws import _authorize_project_access, project_tasks_ws


def _make_ws() -> MagicMock:
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_text = AsyncMock()
    # Simulates an immediate client disconnect so the receive loop returns
    # normally instead of raising CancelledError through the test.
    ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect())
    return ws


def _make_uow(user: MagicMock | None, project: MagicMock | None) -> MagicMock:
    uow = MagicMock()
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    uow.users.get_by_cognito_sub.return_value = user
    uow.projects.get_by_uuid.return_value = project
    # No ProjectMember rows by default — assert_project_access falls through
    # to this once owner/super_admin/tenant-admin all fail. Tests covering
    # an assigned Member configure this explicitly.
    uow.project_members.list_roles_for_user.return_value = []
    return uow


class TestAuthorizeProjectAccess:
    def test_raises_not_found_when_user_missing(self) -> None:
        uow = _make_uow(user=None, project=None)
        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=uow),
            pytest.raises(NotFoundError),
        ):
            _authorize_project_access("cognito-sub-1", uuid.uuid4())

    def test_raises_not_found_when_project_missing(self) -> None:
        user = MagicMock(id=uuid.uuid4(), role_names=[])
        uow = _make_uow(user=user, project=None)
        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=uow),
            pytest.raises(NotFoundError),
        ):
            _authorize_project_access("cognito-sub-1", uuid.uuid4())

    def test_raises_forbidden_when_not_owner_or_admin(self) -> None:
        user = MagicMock(id=uuid.uuid4(), role_names=[])
        project = MagicMock(owner_id=uuid.uuid4())  # different from user.id
        uow = _make_uow(user=user, project=project)
        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=uow),
            pytest.raises(ForbiddenError),
        ):
            _authorize_project_access("cognito-sub-1", uuid.uuid4())

    def test_allows_owner(self) -> None:
        owner_id = uuid.uuid4()
        user = MagicMock(id=owner_id, role_names=[])
        project = MagicMock(owner_id=owner_id)
        uow = _make_uow(user=user, project=project)
        with patch("app.db.unit_of_work.UnitOfWork", return_value=uow):
            _authorize_project_access("cognito-sub-1", uuid.uuid4())  # no raise

    def test_allows_admin_even_when_not_owner_in_same_tenant(self) -> None:
        tenant_id = uuid.uuid4()
        user = MagicMock(id=uuid.uuid4(), role_names=["admin"], tenant_id=tenant_id)
        project = MagicMock(owner_id=uuid.uuid4(), tenant_id=tenant_id)  # different from user.id
        uow = _make_uow(user=user, project=project)
        with patch("app.db.unit_of_work.UnitOfWork", return_value=uow):
            _authorize_project_access("cognito-sub-1", uuid.uuid4())  # no raise

    def test_admin_forbidden_for_project_in_other_tenant(self) -> None:
        user = MagicMock(id=uuid.uuid4(), role_names=["admin"], tenant_id=uuid.uuid4())
        project = MagicMock(owner_id=uuid.uuid4(), tenant_id=uuid.uuid4())
        uow = _make_uow(user=user, project=project)
        with (
            patch("app.db.unit_of_work.UnitOfWork", return_value=uow),
            pytest.raises(ForbiddenError),
        ):
            _authorize_project_access("cognito-sub-1", uuid.uuid4())


class TestProjectTasksWsAuthorization:
    @pytest.mark.asyncio
    async def test_closes_4003_when_authenticated_but_not_owner_or_admin(self) -> None:
        """The bug this guards against: any authenticated user could previously
        subscribe to any other user's project progress stream."""
        ws = _make_ws()
        with (
            patch(
                "app.websockets.source_ws._decode_token",
                return_value={"sub": "cognito-sub-1"},
            ),
            patch(
                "app.websockets.source_ws._authorize_project_access",
                side_effect=ForbiddenError("Forbidden"),
            ),
        ):
            await project_tasks_ws(ws, str(uuid.uuid4()), token="good-token", access_token=None)

        ws.close.assert_called_once_with(code=4003, reason="Forbidden")

    @pytest.mark.asyncio
    async def test_closes_4004_when_project_not_found(self) -> None:
        ws = _make_ws()
        with (
            patch(
                "app.websockets.source_ws._decode_token",
                return_value={"sub": "cognito-sub-1"},
            ),
            patch(
                "app.websockets.source_ws._authorize_project_access",
                side_effect=NotFoundError("Project not found"),
            ),
        ):
            await project_tasks_ws(ws, str(uuid.uuid4()), token="good-token", access_token=None)

        ws.close.assert_called_once_with(code=4004, reason="Project not found")

    @pytest.mark.asyncio
    async def test_closes_4001_when_no_token(self) -> None:
        ws = _make_ws()
        await project_tasks_ws(ws, str(uuid.uuid4()), token=None, access_token=None)
        ws.close.assert_called_once_with(code=4001, reason="Unauthorized")

    @pytest.mark.asyncio
    async def test_closes_4004_when_project_id_invalid(self) -> None:
        ws = _make_ws()
        with patch(
            "app.websockets.source_ws._decode_token",
            return_value={"sub": "cognito-sub-1"},
        ):
            await project_tasks_ws(ws, "not-a-uuid", token="good-token", access_token=None)

        ws.close.assert_called_once_with(code=4004, reason="Invalid project_id")

    @pytest.mark.asyncio
    async def test_proceeds_to_connect_when_authorized(self) -> None:
        ws = _make_ws()
        with (
            patch(
                "app.websockets.source_ws._decode_token",
                return_value={"sub": "cognito-sub-1"},
            ),
            patch(
                "app.websockets.source_ws._authorize_project_access",
                return_value=None,
            ),
            patch("app.websockets.source_ws.manager.connect", new=AsyncMock()) as mock_connect,
            patch("app.websockets.source_ws.manager.disconnect", new=AsyncMock()),
            patch("app.websockets.source_ws._send_current_tasks", new=AsyncMock()),
        ):
            await project_tasks_ws(ws, str(uuid.uuid4()), token="good-token", access_token=None)

        mock_connect.assert_called_once()
        ws.close.assert_not_called()
