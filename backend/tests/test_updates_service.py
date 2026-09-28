"""Unit tests for UpdatesService — the /updates/accept|reject dispatcher."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest

from app.core.exceptions import NotFoundError, ValidationError
from app.schemas.feedback_updates_schema import (
    FeedbackChangeType as FbChangeType,
    FeedbackEntityType as FbEntityType,
    FeedbackUpdateDecisionResponse,
)
from app.schemas.incremental_updates_schema import (
    UpdateAcceptRequest,
    UpdateChangeType,
    UpdateDecisionResponse,
    UpdateEntityType,
    UpdateRejectRequest,
)
from app.services.updates_service import UpdatesService


def _make_service(
    mf_repo: MagicMock | None = None,
    us_repo: MagicMock | None = None,
    incremental_service: AsyncMock | None = None,
    feedback_service: AsyncMock | None = None,
) -> UpdatesService:
    return UpdatesService(
        module_feature_repo=mf_repo or MagicMock(),
        user_story_repo=us_repo or MagicMock(),
        incremental_service=incremental_service or AsyncMock(),
        feedback_service=feedback_service or AsyncMock(),
    )


class TestAcceptUpdateDispatch:
    @pytest.mark.asyncio
    async def test_project_not_found_raises(self, uow):
        uow.projects.get_by_uuid.return_value = None
        service = _make_service()
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.MODULE, entity_id="mod-1", change_type=UpdateChangeType.UPDATED
        )

        with pytest.raises(NotFoundError):
            await service.accept_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

    @pytest.mark.asyncio
    async def test_entity_not_found_raises(self, uow):
        mf_repo = MagicMock()
        mf_repo.get_module_for_project = AsyncMock(return_value=None)
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.MODULE, entity_id="mod-1", change_type=UpdateChangeType.UPDATED
        )

        with pytest.raises(NotFoundError):
            await service.accept_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

    @pytest.mark.asyncio
    async def test_no_pending_change_raises_validation_error(self, uow):
        mf_repo = MagicMock()
        mf_repo.get_module_for_project = AsyncMock(
            return_value=MagicMock(incremental_change_type=None, feedback_change_type=None)
        )
        service = _make_service(mf_repo=mf_repo)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.MODULE, entity_id="mod-1", change_type=UpdateChangeType.UPDATED
        )

        with pytest.raises(ValidationError):
            await service.accept_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

    @pytest.mark.asyncio
    async def test_incremental_change_type_set_dispatches_to_incremental_service(self, uow):
        mf_repo = MagicMock()
        mf_repo.get_module_for_project = AsyncMock(
            return_value=MagicMock(incremental_change_type="UPDATED", feedback_change_type=None)
        )
        incremental_service = AsyncMock()
        incremental_service.accept_update.return_value = UpdateDecisionResponse(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.UPDATED,
            action="accepted",
        )
        service = _make_service(mf_repo=mf_repo, incremental_service=incremental_service)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.MODULE, entity_id="mod-1", change_type=UpdateChangeType.UPDATED
        )
        project_id = uuid.uuid4()

        result = await service.accept_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        incremental_service.accept_update.assert_awaited_once_with(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )
        service._feedback_service.accept_feedback_update.assert_not_awaited()
        assert result.action == "accepted"

    @pytest.mark.asyncio
    async def test_feedback_change_type_set_dispatches_to_feedback_service(self, uow):
        mf_repo = MagicMock()
        mf_repo.get_module_for_project = AsyncMock(
            return_value=MagicMock(incremental_change_type=None, feedback_change_type="UPDATED")
        )
        feedback_service = AsyncMock()
        feedback_service.accept_feedback_update.return_value = FeedbackUpdateDecisionResponse(
            entity_type=FbEntityType.MODULE,
            entity_id="mod-1",
            change_type=FbChangeType.UPDATED,
            action="accepted",
            comment="looks good",
        )
        service = _make_service(mf_repo=mf_repo, feedback_service=feedback_service)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.MODULE,
            entity_id="mod-1",
            change_type=UpdateChangeType.UPDATED,
            comment="looks good",
        )
        project_id = uuid.uuid4()

        result = await service.accept_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        feedback_service.accept_feedback_update.assert_awaited_once()
        service._incremental_service.accept_update.assert_not_awaited()
        assert result.action == "accepted"
        assert result.comment == "looks good"
        assert result.entity_type == UpdateEntityType.MODULE

    @pytest.mark.asyncio
    async def test_feature_dispatch_uses_pending_change_flags_getter(self, uow):
        mf_repo = MagicMock()
        mf_repo.get_feature_pending_change_flags = AsyncMock(return_value=(None, "ADDED"))
        feedback_service = AsyncMock()
        feedback_service.accept_feedback_update.return_value = FeedbackUpdateDecisionResponse(
            entity_type=FbEntityType.FEATURE,
            entity_id="fea-1",
            change_type=FbChangeType.ADDED,
            action="accepted",
        )
        service = _make_service(mf_repo=mf_repo, feedback_service=feedback_service)
        payload = UpdateAcceptRequest(
            entity_type=UpdateEntityType.FEATURE, entity_id="fea-1", change_type=UpdateChangeType.ADDED
        )

        result = await service.accept_update(
            project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
        )

        mf_repo.get_feature_pending_change_flags.assert_awaited_once()
        assert result.action == "accepted"


class TestRejectUpdateDispatch:
    @pytest.mark.asyncio
    async def test_incremental_change_type_set_dispatches_to_incremental_service(self, uow):
        us_repo = MagicMock()
        us_repo.get_user_story_detail_for_project = AsyncMock(
            return_value=MagicMock(incremental_change_type="ADDED", feedback_change_type=None)
        )
        incremental_service = AsyncMock()
        incremental_service.reject_update.return_value = UpdateDecisionResponse(
            entity_type=UpdateEntityType.USER_STORY,
            entity_id="us-1",
            change_type=UpdateChangeType.ADDED,
            action="rejected",
            deleted=True,
        )
        service = _make_service(us_repo=us_repo, incremental_service=incremental_service)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.USER_STORY,
            entity_id="us-1",
            change_type=UpdateChangeType.ADDED,
        )
        project_id = uuid.uuid4()

        result = await service.reject_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        incremental_service.reject_update.assert_awaited_once_with(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )
        assert result.deleted is True

    @pytest.mark.asyncio
    async def test_feedback_change_type_set_dispatches_to_feedback_service_with_reason_as_comment(
        self, uow
    ):
        us_repo = MagicMock()
        us_repo.get_user_story_detail_for_project = AsyncMock(
            return_value=MagicMock(incremental_change_type=None, feedback_change_type="UPDATED")
        )
        feedback_service = AsyncMock()
        feedback_service.reject_feedback_update.return_value = FeedbackUpdateDecisionResponse(
            entity_type=FbEntityType.USER_STORY,
            entity_id="us-1",
            change_type=FbChangeType.UPDATED,
            action="rejected",
            deleted=False,
        )
        service = _make_service(us_repo=us_repo, feedback_service=feedback_service)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.USER_STORY,
            entity_id="us-1",
            change_type=UpdateChangeType.UPDATED,
            reason="needs more detail",
        )

        result = await service.reject_update(
            project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
        )

        called_payload = feedback_service.reject_feedback_update.await_args.kwargs["payload"]
        assert called_payload.comment == "needs more detail"
        assert result.reason == "needs more detail"

    @pytest.mark.asyncio
    async def test_no_pending_change_raises_validation_error(self, uow):
        us_repo = MagicMock()
        us_repo.get_user_story_detail_for_project = AsyncMock(
            return_value=MagicMock(incremental_change_type=None, feedback_change_type=None)
        )
        service = _make_service(us_repo=us_repo)
        payload = UpdateRejectRequest(
            entity_type=UpdateEntityType.USER_STORY,
            entity_id="us-1",
            change_type=UpdateChangeType.UPDATED,
        )

        with pytest.raises(ValidationError):
            await service.reject_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )
