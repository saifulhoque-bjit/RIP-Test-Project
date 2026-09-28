"""Unit tests for the /feedback-updates route handlers."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.messages import MSG_FEEDBACK_UPDATES_ACCEPTED, MSG_FEEDBACK_UPDATES_REJECTED
from app.routes.v1.feedback_updates import accept_feedback_update, reject_feedback_update
from app.schemas.feedback_updates_schema import (
    FeedbackChangeType,
    FeedbackEntityType,
    FeedbackUpdateAcceptRequest,
    FeedbackUpdateDecisionResponse,
    FeedbackUpdateRejectRequest,
)
from tests.conftest import make_project, make_user


class TestAcceptFeedbackUpdate:
    @pytest.mark.asyncio
    async def test_delegates_to_service_and_wraps_response(self):
        project_id = uuid.uuid4()
        user = make_user()
        project = make_project()
        uow = MagicMock()
        payload = FeedbackUpdateAcceptRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.UPDATED,
            comment="ok",
        )
        decision = FeedbackUpdateDecisionResponse(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.UPDATED,
            action="accepted",
            deleted=False,
            comment="ok",
        )

        with patch("app.routes.v1.feedback_updates.FeedbackUpdateService") as MockService:
            MockService.return_value.accept_feedback_update = AsyncMock(return_value=decision)

            result = await accept_feedback_update(
                project_id=project_id,
                payload=payload,
                uow=uow,
                current_user=user,
                _project=project,
            )

            MockService.return_value.accept_feedback_update.assert_awaited_once_with(
                project_id=project_id, payload=payload, actor_user_id=user.id, uow=uow
            )

        assert result.success is True
        assert result.message == MSG_FEEDBACK_UPDATES_ACCEPTED
        assert result.data is decision


class TestRejectFeedbackUpdate:
    @pytest.mark.asyncio
    async def test_delegates_to_service_and_wraps_response(self):
        project_id = uuid.uuid4()
        user = make_user()
        project = make_project()
        uow = MagicMock()
        payload = FeedbackUpdateRejectRequest(
            entity_type=FeedbackEntityType.FEATURE,
            entity_id="fea-1",
            change_type=FeedbackChangeType.ADDED,
            comment="not needed",
        )
        decision = FeedbackUpdateDecisionResponse(
            entity_type=FeedbackEntityType.FEATURE,
            entity_id="fea-1",
            change_type=FeedbackChangeType.ADDED,
            action="rejected",
            deleted=True,
            comment="not needed",
        )

        with patch("app.routes.v1.feedback_updates.FeedbackUpdateService") as MockService:
            MockService.return_value.reject_feedback_update = AsyncMock(return_value=decision)

            result = await reject_feedback_update(
                project_id=project_id,
                payload=payload,
                uow=uow,
                current_user=user,
                _project=project,
            )

            MockService.return_value.reject_feedback_update.assert_awaited_once_with(
                project_id=project_id, payload=payload, actor_user_id=user.id, uow=uow
            )

        assert result.success is True
        assert result.message == MSG_FEEDBACK_UPDATES_REJECTED
        assert result.data is decision


class TestNoRateLimiting:
    """Project-content accept/reject actions, not upload/auth/AI-generation triggers, so per
    `.claude/rules/routes.md` these are not required to be rate-limited (mirrors the equivalent
    pin-down test in test_incremental_updates_routes.py for /incremental-updates/accept,
    /incremental-updates/reject).
    """

    @pytest.mark.parametrize("handler", [accept_feedback_update, reject_feedback_update])
    def test_handler_has_no_request_param(self, handler):
        assert "request" not in inspect.signature(handler).parameters
