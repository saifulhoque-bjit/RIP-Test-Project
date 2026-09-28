"""Unit tests for the /incremental-updates route handlers."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.messages import (
    MSG_INCREMENTAL_UPDATES_LIST_FETCHED,
    MSG_INCREMENTAL_UPDATES_TREE_FETCHED,
    MSG_UPDATES_ACCEPTED,
    MSG_UPDATES_REJECTED,
)
from app.routes.v1.incremental_updates import (
    accept_update,
    get_latest_incremental_updates_tree,
    list_updates,
    reject_update,
)
from app.schemas.incremental_updates_schema import (
    IncrementalUpdatesListResponse,
    IncrementalUpdatesTreeResponse,
    UpdateAcceptRequest,
    UpdateChangeType,
    UpdateDecisionResponse,
    UpdateEntityType,
    UpdateRejectRequest,
)
from tests.conftest import make_project, make_user


class TestGetLatestIncrementalUpdatesTree:
    @pytest.mark.asyncio
    async def test_delegates_to_service_and_wraps_response(self):
        project_id = uuid.uuid4()
        user = make_user()
        project = make_project()
        uow = MagicMock()
        tree_response = IncrementalUpdatesTreeResponse(history_id=None, generated_at=None, items=[])

        with patch("app.routes.v1.incremental_updates.IncrementalUpdatesService") as MockService:
            MockService.return_value.get_latest_incremental_tree = AsyncMock(
                return_value=tree_response
            )

            result = await get_latest_incremental_updates_tree(
                project_id=project_id,
                uow=uow,
                _current_user=user,
                _project=project,
            )

            MockService.return_value.get_latest_incremental_tree.assert_awaited_once_with(
                project_id=project_id, uow=uow
            )

        assert result.success is True
        assert result.message == MSG_INCREMENTAL_UPDATES_TREE_FETCHED
        assert result.data is tree_response


class TestListUpdates:
    @pytest.mark.asyncio
    async def test_delegates_to_service_and_wraps_response(self):
        project_id = uuid.uuid4()
        user = make_user()
        project = make_project()
        uow = MagicMock()
        list_response = IncrementalUpdatesListResponse(items=[])

        with patch("app.routes.v1.incremental_updates.IncrementalUpdatesService") as MockService:
            MockService.return_value.get_incremental_updates_list = AsyncMock(
                return_value=list_response
            )

            result = await list_updates(
                project_id=project_id,
                uow=uow,
                _current_user=user,
                _project=project,
            )

            MockService.return_value.get_incremental_updates_list.assert_awaited_once_with(
                project_id=project_id, uow=uow
            )

        assert result.success is True
        assert result.message == MSG_INCREMENTAL_UPDATES_LIST_FETCHED
        assert result.data is list_response


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

        with patch("app.routes.v1.incremental_updates.IncrementalUpdatesService") as MockService:
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

        with patch("app.routes.v1.incremental_updates.IncrementalUpdatesService") as MockService:
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
    """These 4 endpoints are read/write project-content actions, not upload/auth/AI-generation
    triggers, so per `.claude/rules/routes.md` they are not required to be rate-limited. Confirmed
    via repo-wide search that no `@limiter.limit(...)` decorator or `RATE_LIMIT_*` entry references
    any of them. These tests just pin that fact down so a future change doesn't silently add
    inconsistent rate limiting without a matching decorator + `request: Request` param + test update.
    """

    @pytest.mark.parametrize(
        "handler",
        [get_latest_incremental_updates_tree, list_updates, accept_update, reject_update],
    )
    def test_handler_has_no_request_param(self, handler):
        assert "request" not in inspect.signature(handler).parameters
