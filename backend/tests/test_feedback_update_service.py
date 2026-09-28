"""Unit tests for FeedbackUpdateService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.schemas.feedback_updates_schema import (
    FeedbackChangeType,
    FeedbackEntityType,
    FeedbackUpdateAcceptRequest,
    FeedbackUpdateRejectRequest,
)
from app.services.feedback_update_service import FeedbackUpdateService


def _make_service(
    mf_repo: MagicMock | None = None, us_repo: MagicMock | None = None
) -> FeedbackUpdateService:
    """Build the service, defaulting ``get_source_ingestion_id`` to ``None``.

    ``accept_feedback_update``/``reject_feedback_update`` always resolve the
    entity's ``source_ingestion_id`` before mutating it; tests that don't
    care about the resulting review-count bump don't need to configure this
    themselves.
    """
    mf_repo = mf_repo or MagicMock()
    us_repo = us_repo or MagicMock()
    if not isinstance(mf_repo.get_source_ingestion_id, AsyncMock):
        mf_repo.get_source_ingestion_id = AsyncMock(return_value=None)
    if not isinstance(us_repo.get_source_ingestion_id, AsyncMock):
        us_repo.get_source_ingestion_id = AsyncMock(return_value=None)
    return FeedbackUpdateService(
        module_feature_repo=mf_repo,
        user_story_repo=us_repo,
    )


def _pipeline_not_running(uow) -> None:
    uow.source_ingestions.list_running_by_project.return_value = []


class TestAcceptFeedbackUpdate:
    @pytest.mark.asyncio
    async def test_project_not_found_raises(self, uow):
        uow.projects.get_by_uuid.return_value = None
        service = _make_service()
        payload = FeedbackUpdateAcceptRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.UPDATED,
        )

        with pytest.raises(NotFoundError):
            await service.accept_feedback_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

    @pytest.mark.asyncio
    async def test_pipeline_running_raises_conflict(self, uow):
        from app.core.enums.source_type import SourceType

        uow.source_ingestions.list_running_by_project.return_value = [
            MagicMock(source_type=SourceType.RFP.value, stages=[], id=uuid.uuid4())
        ]
        service = _make_service()
        payload = FeedbackUpdateAcceptRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.UPDATED,
        )

        with pytest.raises(ConflictError):
            await service.accept_feedback_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

    @pytest.mark.asyncio
    async def test_delete_suggested_raises_validation_error(self, uow):
        _pipeline_not_running(uow)
        service = _make_service()
        payload = FeedbackUpdateAcceptRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.DELETE_SUGGESTED,
        )

        with pytest.raises(ValidationError):
            await service.accept_feedback_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

    @pytest.mark.asyncio
    async def test_accept_updated_module_approves(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.accept_module_feedback_change = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateAcceptRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.UPDATED,
            comment="looks good",
        )

        result = await service.accept_feedback_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "accepted"
        assert result.deleted is False
        assert result.comment == "looks good"
        mf_repo.accept_module_feedback_change.assert_awaited_once_with(project_id, "mod-1")

    @pytest.mark.asyncio
    async def test_accept_added_feature_approves(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.accept_feature_feedback_change = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateAcceptRequest(
            entity_type=FeedbackEntityType.FEATURE,
            entity_id="fea-1",
            change_type=FeedbackChangeType.ADDED,
        )

        result = await service.accept_feedback_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "accepted"
        mf_repo.accept_feature_feedback_change.assert_awaited_once_with(project_id, "fea-1")

    @pytest.mark.asyncio
    async def test_accept_entity_not_found_raises(self, uow):
        _pipeline_not_running(uow)
        mf_repo = MagicMock()
        mf_repo.accept_module_feedback_change = AsyncMock(return_value=False)
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateAcceptRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="missing",
            change_type=FeedbackChangeType.UPDATED,
        )

        with pytest.raises(NotFoundError):
            await service.accept_feedback_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

    @pytest.mark.asyncio
    async def test_accept_updated_user_story_approves(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        us_repo = MagicMock()
        us_repo.accept_user_story_feedback_change = AsyncMock(return_value=True)
        service = _make_service(us_repo=us_repo)
        payload = FeedbackUpdateAcceptRequest(
            entity_type=FeedbackEntityType.USER_STORY,
            entity_id="story-1",
            change_type=FeedbackChangeType.UPDATED,
        )

        result = await service.accept_feedback_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "accepted"
        us_repo.accept_user_story_feedback_change.assert_awaited_once_with("story-1")

    @pytest.mark.asyncio
    async def test_accept_bumps_review_count_on_tagging_ingestion(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        ingestion_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.accept_module_feedback_change = AsyncMock(return_value=True)
        mf_repo.get_source_ingestion_id = AsyncMock(return_value=str(ingestion_id))
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateAcceptRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.UPDATED,
            comment="looks good",
        )

        await service.accept_feedback_update(
            project_id=project_id, payload=payload, actor_user_id=uuid.uuid4(), uow=uow
        )

        mf_repo.get_source_ingestion_id.assert_awaited_once_with(project_id, "module", "mod-1")
        uow.source_ingestions.increment_review_count.assert_called_once_with(
            ingestion_id, entity_type="module", accepted=True
        )

    @pytest.mark.asyncio
    async def test_accept_skips_review_count_when_ingestion_id_unresolved(self, uow):
        _pipeline_not_running(uow)
        mf_repo = MagicMock()
        mf_repo.accept_module_feedback_change = AsyncMock(return_value=True)
        mf_repo.get_source_ingestion_id = AsyncMock(return_value=None)
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateAcceptRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.UPDATED,
        )

        await service.accept_feedback_update(
            project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
        )

        uow.source_ingestions.increment_review_count.assert_not_called()


class TestRejectFeedbackUpdate:
    @pytest.mark.asyncio
    async def test_project_not_found_raises(self, uow):
        uow.projects.get_by_uuid.return_value = None
        service = _make_service()
        payload = FeedbackUpdateRejectRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.UPDATED,
        )

        with pytest.raises(NotFoundError):
            await service.reject_feedback_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

    @pytest.mark.asyncio
    async def test_delete_suggested_raises_validation_error(self, uow):
        _pipeline_not_running(uow)
        service = _make_service()
        payload = FeedbackUpdateRejectRequest(
            entity_type=FeedbackEntityType.FEATURE,
            entity_id="fea-1",
            change_type=FeedbackChangeType.DELETE_SUGGESTED,
        )

        with pytest.raises(ValidationError):
            await service.reject_feedback_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

    @pytest.mark.asyncio
    async def test_reject_added_module_hard_deletes(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.delete_module_by_id = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateRejectRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.ADDED,
        )

        result = await service.reject_feedback_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "rejected"
        assert result.deleted is True
        mf_repo.delete_module_by_id.assert_awaited_once_with(project_id, "mod-1")

    @pytest.mark.asyncio
    async def test_reject_updated_feature_restores_from_snapshot(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.restore_feature_from_latest_feedback_version = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateRejectRequest(
            entity_type=FeedbackEntityType.FEATURE,
            entity_id="fea-1",
            change_type=FeedbackChangeType.UPDATED,
        )

        result = await service.reject_feedback_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "rejected"
        assert result.deleted is False
        mf_repo.restore_feature_from_latest_feedback_version.assert_awaited_once_with(
            project_id, "fea-1"
        )

    @pytest.mark.asyncio
    async def test_reject_updated_module_restores_from_snapshot(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.restore_module_from_latest_feedback_version = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateRejectRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.UPDATED,
        )

        result = await service.reject_feedback_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "rejected"
        mf_repo.restore_module_from_latest_feedback_version.assert_awaited_once_with(
            project_id, "mod-1"
        )

    @pytest.mark.asyncio
    async def test_reject_added_user_story_hard_deletes(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        us_repo = MagicMock()
        us_repo.delete_user_story_by_id = AsyncMock(return_value=True)
        service = _make_service(us_repo=us_repo)
        payload = FeedbackUpdateRejectRequest(
            entity_type=FeedbackEntityType.USER_STORY,
            entity_id="story-1",
            change_type=FeedbackChangeType.ADDED,
        )

        result = await service.reject_feedback_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "rejected"
        assert result.deleted is True
        us_repo.delete_user_story_by_id.assert_awaited_once_with(
            project_id=project_id, user_story_id="story-1"
        )

    @pytest.mark.asyncio
    async def test_reject_updated_user_story_restores_from_snapshot(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        us_repo = MagicMock()
        us_repo.restore_user_story_from_latest_feedback_version = AsyncMock(return_value=True)
        service = _make_service(us_repo=us_repo)
        payload = FeedbackUpdateRejectRequest(
            entity_type=FeedbackEntityType.USER_STORY,
            entity_id="story-1",
            change_type=FeedbackChangeType.UPDATED,
        )

        result = await service.reject_feedback_update(
            project_id=project_id, payload=payload, actor_user_id=None, uow=uow
        )

        assert result.action == "rejected"
        assert result.deleted is False
        us_repo.restore_user_story_from_latest_feedback_version.assert_awaited_once_with(
            "story-1"
        )

    @pytest.mark.asyncio
    async def test_reject_entity_not_found_raises(self, uow):
        _pipeline_not_running(uow)
        mf_repo = MagicMock()
        mf_repo.delete_feature_by_id = AsyncMock(return_value=False)
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateRejectRequest(
            entity_type=FeedbackEntityType.FEATURE,
            entity_id="missing",
            change_type=FeedbackChangeType.ADDED,
        )

        with pytest.raises(NotFoundError):
            await service.reject_feedback_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

    @pytest.mark.asyncio
    async def test_reject_bumps_review_count_on_tagging_ingestion(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        ingestion_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.delete_module_by_id = AsyncMock(return_value=True)
        mf_repo.get_source_ingestion_id = AsyncMock(return_value=str(ingestion_id))
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateRejectRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.ADDED,
            comment="not needed",
        )

        await service.reject_feedback_update(
            project_id=project_id, payload=payload, actor_user_id=uuid.uuid4(), uow=uow
        )

        mf_repo.get_source_ingestion_id.assert_awaited_once_with(project_id, "module", "mod-1")
        uow.source_ingestions.increment_review_count.assert_called_once_with(
            ingestion_id, entity_type="module", accepted=False
        )

    @pytest.mark.asyncio
    async def test_reject_skips_review_count_when_ingestion_id_unresolved(self, uow):
        _pipeline_not_running(uow)
        mf_repo = MagicMock()
        mf_repo.delete_module_by_id = AsyncMock(return_value=True)
        mf_repo.get_source_ingestion_id = AsyncMock(return_value=None)
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateRejectRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.ADDED,
        )

        await service.reject_feedback_update(
            project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
        )

        uow.source_ingestions.increment_review_count.assert_not_called()


class TestIngestionCompletionSideEffects:
    """Accept/reject must trigger SourceIngestionService's completion checks.

    Resolving a pending feedback change is exactly the signal that may
    complete its `requirement_update` ingestion and, in turn, the project's
    RFP/source-code generation ingestion — see source_ingestion_service.py.
    """

    @staticmethod
    def _patch_completion_checks():
        return (
            patch(
                "app.services.source_ingestion_service.SourceIngestionService."
                "try_complete_open_feedback_or_incremental_ingestions",
                new=AsyncMock(),
            ),
            patch(
                "app.services.source_ingestion_service.SourceIngestionService."
                "try_complete_generation_ingestion",
                new=AsyncMock(),
            ),
        )

    @pytest.mark.asyncio
    async def test_accept_triggers_completion_checks(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.accept_module_feedback_change = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateAcceptRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.UPDATED,
        )
        open_patch, generation_patch = self._patch_completion_checks()

        with open_patch as mock_open, generation_patch as mock_generation:
            await service.accept_feedback_update(
                project_id=project_id, payload=payload, actor_user_id=None, uow=uow
            )

        mock_open.assert_awaited_once_with(
            uow, project_id, service._mf_repo, service._us_repo, actor_user_id=None
        )
        mock_generation.assert_awaited_once_with(
            uow, project_id, service._us_repo, actor_user_id=None
        )

    @pytest.mark.asyncio
    async def test_reject_triggers_completion_checks(self, uow):
        _pipeline_not_running(uow)
        project_id = uuid.uuid4()
        mf_repo = MagicMock()
        mf_repo.delete_module_by_id = AsyncMock(return_value=True)
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateRejectRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="mod-1",
            change_type=FeedbackChangeType.ADDED,
        )
        open_patch, generation_patch = self._patch_completion_checks()

        with open_patch as mock_open, generation_patch as mock_generation:
            await service.reject_feedback_update(
                project_id=project_id, payload=payload, actor_user_id=None, uow=uow
            )

        mock_open.assert_awaited_once_with(
            uow, project_id, service._mf_repo, service._us_repo, actor_user_id=None
        )
        mock_generation.assert_awaited_once_with(
            uow, project_id, service._us_repo, actor_user_id=None
        )

    @pytest.mark.asyncio
    async def test_completion_checks_not_run_when_entity_not_found(self, uow):
        _pipeline_not_running(uow)
        mf_repo = MagicMock()
        mf_repo.accept_module_feedback_change = AsyncMock(return_value=False)
        service = _make_service(mf_repo=mf_repo)
        payload = FeedbackUpdateAcceptRequest(
            entity_type=FeedbackEntityType.MODULE,
            entity_id="missing",
            change_type=FeedbackChangeType.UPDATED,
        )
        open_patch, generation_patch = self._patch_completion_checks()

        with open_patch as mock_open, generation_patch as mock_generation, pytest.raises(NotFoundError):
            await service.accept_feedback_update(
                project_id=uuid.uuid4(), payload=payload, actor_user_id=None, uow=uow
            )

        mock_open.assert_not_called()
        mock_generation.assert_not_called()
