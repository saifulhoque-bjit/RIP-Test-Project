"""Service for accepting/rejecting a feedback-driven regeneration change.

Reviews a single Module/Feature flagged by ``ModuleFeatureService.
upsert_modules_and_features_v2`` (the ``/modules/regenerate`` flow) or a
single UserStory flagged by ``UserStoryService.upsert_user_stories_from_patch``
(the ``/user-stories/regenerate-by-feedback`` flow) with a
``feedback_change_type`` of ``ADDED`` or ``UPDATED``. Independent of
``IncrementalUpdatesService``, which reviews the separate
``incremental_change_type`` flow — the two tracking flags never interact.

``DELETE_SUGGESTED`` is not yet produced by either regeneration flow and is
rejected here with a clear error rather than silently mishandled.
"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import NotFoundError, ValidationError
from app.core.messages import (
    MSG_FEEDBACK_DELETE_SUGGESTED_NOT_SUPPORTED,
    MSG_PROJECT_NOT_FOUND,
    MSG_UPDATE_ENTITY_NOT_FOUND,
)
from app.db.unit_of_work import UnitOfWork
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.schemas.feedback_updates_schema import (
    FeedbackChangeType,
    FeedbackEntityType,
    FeedbackUpdateAcceptRequest,
    FeedbackUpdateDecisionResponse,
    FeedbackUpdateRejectRequest,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class FeedbackUpdateService:
    """Accepts/rejects a single module/feature/user-story's pending ``feedback_change_type``."""

    def __init__(
        self,
        module_feature_repo: ModuleFeatureRepository | None = None,
        user_story_repo: UserStoryRepository | None = None,
    ) -> None:
        self._mf_repo = module_feature_repo or ModuleFeatureRepository()
        self._us_repo = user_story_repo or UserStoryRepository()

    async def accept_feedback_update(
        self,
        *,
        project_id: UUID,
        payload: FeedbackUpdateAcceptRequest,
        actor_user_id: UUID | None,
        uow: UnitOfWork,
    ) -> FeedbackUpdateDecisionResponse:
        """Accept a pending feedback-driven regeneration change for a single entity.

        ADDED/UPDATED: status becomes ``approved`` and ``feedback_change_type``
        is cleared — the regenerated content is kept as-is.

        Raises:
            NotFoundError: If the project or the target entity does not exist.
            ValidationError: If ``change_type`` is ``DELETE_SUGGESTED``.
        """
        if uow.projects.get_by_uuid(project_id) is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        self._raise_if_delete_suggested(payload.change_type)

        from app.services.source_ingestion_service import SourceIngestionService  # noqa: PLC0415

        SourceIngestionService.raise_if_pipeline_running(uow, project_id)

        source_ingestion_id = await self._resolve_source_ingestion_id(
            project_id, payload.entity_type, payload.entity_id
        )

        found = await self._accept_entity(project_id, payload.entity_type, payload.entity_id)
        if not found:
            raise NotFoundError(
                MSG_UPDATE_ENTITY_NOT_FOUND.format(
                    entity_type=payload.entity_type.value,
                    entity_id=payload.entity_id,
                    project_id=project_id,
                )
            )

        self._bump_review_count(uow, source_ingestion_id, payload.entity_type, accepted=True)

        await SourceIngestionService.try_complete_open_feedback_or_incremental_ingestions(
            uow, project_id, self._mf_repo, self._us_repo, actor_user_id=actor_user_id
        )
        await SourceIngestionService.try_complete_generation_ingestion(
            uow, project_id, self._us_repo, actor_user_id=actor_user_id
        )

        return FeedbackUpdateDecisionResponse(
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            change_type=payload.change_type,
            action="accepted",
            deleted=False,
            comment=payload.comment,
        )

    async def reject_feedback_update(
        self,
        *,
        project_id: UUID,
        payload: FeedbackUpdateRejectRequest,
        actor_user_id: UUID | None,
        uow: UnitOfWork,
    ) -> FeedbackUpdateDecisionResponse:
        """Reject a pending feedback-driven regeneration change for a single entity.

        ADDED: the node is hard-deleted, since a brand-new entity has no
        prior state to roll back to. UPDATED: content is restored from the
        most recent Module/Feature version snapshot and
        ``feedback_change_type`` is cleared.

        Raises:
            NotFoundError: If the project or the target entity does not exist.
            ValidationError: If ``change_type`` is ``DELETE_SUGGESTED``.
        """
        if uow.projects.get_by_uuid(project_id) is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        self._raise_if_delete_suggested(payload.change_type)

        from app.services.source_ingestion_service import SourceIngestionService  # noqa: PLC0415

        SourceIngestionService.raise_if_pipeline_running(uow, project_id)

        source_ingestion_id = await self._resolve_source_ingestion_id(
            project_id, payload.entity_type, payload.entity_id
        )

        if payload.change_type == FeedbackChangeType.ADDED:
            found = await self._delete_entity(project_id, payload.entity_type, payload.entity_id)
            deleted = found
        else:
            found = await self._restore_entity(project_id, payload.entity_type, payload.entity_id)
            deleted = False

        if not found:
            raise NotFoundError(
                MSG_UPDATE_ENTITY_NOT_FOUND.format(
                    entity_type=payload.entity_type.value,
                    entity_id=payload.entity_id,
                    project_id=project_id,
                )
            )

        self._bump_review_count(uow, source_ingestion_id, payload.entity_type, accepted=False)

        await SourceIngestionService.try_complete_open_feedback_or_incremental_ingestions(
            uow, project_id, self._mf_repo, self._us_repo, actor_user_id=actor_user_id
        )
        await SourceIngestionService.try_complete_generation_ingestion(
            uow, project_id, self._us_repo, actor_user_id=actor_user_id
        )

        return FeedbackUpdateDecisionResponse(
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            change_type=payload.change_type,
            action="rejected",
            deleted=deleted,
            comment=payload.comment,
        )

    @staticmethod
    def _raise_if_delete_suggested(change_type: FeedbackChangeType) -> None:
        if change_type == FeedbackChangeType.DELETE_SUGGESTED:
            raise ValidationError(MSG_FEEDBACK_DELETE_SUGGESTED_NOT_SUPPORTED)

    async def _accept_entity(
        self, project_id: UUID, entity_type: FeedbackEntityType, entity_id: str
    ) -> bool:
        if entity_type == FeedbackEntityType.MODULE:
            return await self._mf_repo.accept_module_feedback_change(project_id, entity_id)
        if entity_type == FeedbackEntityType.FEATURE:
            return await self._mf_repo.accept_feature_feedback_change(project_id, entity_id)
        return await self._us_repo.accept_user_story_feedback_change(entity_id)

    async def _delete_entity(
        self, project_id: UUID, entity_type: FeedbackEntityType, entity_id: str
    ) -> bool:
        if entity_type == FeedbackEntityType.MODULE:
            return await self._mf_repo.delete_module_by_id(project_id, entity_id)
        if entity_type == FeedbackEntityType.FEATURE:
            return await self._mf_repo.delete_feature_by_id(project_id, entity_id)
        return await self._us_repo.delete_user_story_by_id(
            project_id=project_id, user_story_id=entity_id
        )

    async def _restore_entity(
        self, project_id: UUID, entity_type: FeedbackEntityType, entity_id: str
    ) -> bool:
        if entity_type == FeedbackEntityType.MODULE:
            return await self._mf_repo.restore_module_from_latest_feedback_version(
                project_id, entity_id
            )
        if entity_type == FeedbackEntityType.FEATURE:
            return await self._mf_repo.restore_feature_from_latest_feedback_version(
                project_id, entity_id
            )
        return await self._us_repo.restore_user_story_from_latest_feedback_version(entity_id)

    async def _resolve_source_ingestion_id(
        self, project_id: UUID, entity_type: FeedbackEntityType, entity_id: str
    ) -> str | None:
        """Look up the entity's tagged ``source_ingestion_id`` before it's mutated.

        Must run before the accept/reject write, since an ADDED reject
        hard-deletes the node.
        """
        if entity_type == FeedbackEntityType.USER_STORY:
            return await self._us_repo.get_source_ingestion_id(entity_id)
        return await self._mf_repo.get_source_ingestion_id(project_id, entity_type.value, entity_id)

    @staticmethod
    def _bump_review_count(
        uow: UnitOfWork,
        source_ingestion_id: str | None,
        entity_type: FeedbackEntityType,
        *,
        accepted: bool,
    ) -> None:
        """Bump the accepted/rejected counter on the tagging ingestion, replacing
        the old per-decision ActivityLog entry with a running total instead.

        No-op (with a warning) if the entity carries no ``source_ingestion_id``
        (e.g. a legacy entity predating this field) — the accept/reject itself
        must still succeed either way.
        """
        if source_ingestion_id is None:
            logger.warning(
                "[FEEDBACK_UPDATE] no source_ingestion_id tagged on %s; skipping review count "
                "(accepted=%s)",
                entity_type.value,
                accepted,
            )
            return
        uow.source_ingestions.increment_review_count(
            UUID(source_ingestion_id), entity_type=entity_type.value, accepted=accepted
        )
