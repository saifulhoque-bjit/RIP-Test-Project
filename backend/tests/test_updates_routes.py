"""Unit tests for the unified /updates route handlers."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.messages import MSG_UPDATES_ACCEPTED, MSG_UPDATES_REJECTED
from app.routes.v1.updates import accept_update, reject_update
from app.schemas.incremental_updates_schema import (
    UpdateAcceptRequest,
    UpdateChangeType,
    UpdateDecisionResponse,
    UpdateEntityType,
    UpdateRejectRequest,
)
from tests.conftest import make_project, make_user


class TestAcceptUpdate:
    @pytest.mark.asyncio
    async def test_delegates_to_service_and_wraps_response(self):
        project_id = uuid.uuid4()
        user = make_user()
        project = make_project()
        uow = MagicMock()
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.UPDATED,
            comment="ok",
        )
        decision = UpdateDecisionResponse(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.UPDATED,
            action="accepted",
            deleted=False,
            comment="ok",
        )

        with patch("app.routes.v1.updates.UpdatesService") as MockService:
            MockService.return_value.accept_update = AsyncMock(return_value=decision)

            result = await accept_update(
                project_id=project_id,
                payload=payload,
                uow=uow,
                current_user=user,
                _project=project,
            )

            MockService.return_value.accept_update.assert_awaited_once_with(
                project_id=project_id, payload=payload, actor_user_id=user.id, uow=uow
            )

        assert result.success is True
        assert result.message == MSG_UPDATES_ACCEPTED
        assert result.data is decision


class TestRejectUpdate:
    @pytest.mark.asyncio
    async def test_delegates_to_service_and_wraps_response(self):
        project_id = uuid.uuid4()
        user = make_user()
        project = make_project()
        uow = MagicMock()
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.USER_STORY,
            entity_id="us-1",
            change_type=UpdateChangeType.ADDED,
            reason="not needed",
        )
        decision = UpdateDecisionResponse(
            entity_type=UpdateEntityType.USER_STORY,
            entity_id="us-1",
            change_type=UpdateChangeType.ADDED,
            action="rejected",
            deleted=True,
            reason="not needed",
        )

        with patch("app.routes.v1.updates.UpdatesService") as MockService:
            MockService.return_value.reject_update = AsyncMock(return_value=decision)

            result = await reject_update(
                project_id=project_id,
                payload=payload,
                uow=uow,
                current_user=user,
                _project=project,
            )

            MockService.return_value.reject_update.assert_awaited_once_with(
                project_id=project_id, payload=payload, actor_user_id=user.id, uow=uow
            )

        assert result.success is True
        assert result.message == MSG_UPDATES_REJECTED
        assert result.data is decision


class TestNoRateLimiting:
    """Project-content accept/reject actions, not upload/auth/AI-generation triggers, so per
    `.claude/rules/routes.md` these are not required to be rate-limited (mirrors the equivalent
    pin-down test in test_incremental_updates_routes.py / test_feedback_updates_routes.py).
    """

    @pytest.mark.parametrize("handler", [accept_update, reject_update])
    def test_handler_has_no_request_param(self, handler):
        assert "request" not in inspect.signature(handler).parameters
