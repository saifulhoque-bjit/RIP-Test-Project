"""Service that dispatches a single accept/reject decision to whichever review
flow actually has a pending change on the target node.

A Module/Feature/UserStory can carry two independent tracking flags —
``incremental_change_type`` (set by the incremental-source-update flow, see
``IncrementalUpdatesService``) and ``feedback_change_type`` (set by the
feedback-driven regeneration flow, see ``FeedbackUpdateService``). In
practice the two flows never both flag the same node at once. This service
lets a caller hit a single endpoint (``/projects/{project_id}/updates/accept``
/ ``/reject``) without knowing in advance which flag is set: it reads the
target node's two flags, then delegates the actual accept/reject business
logic to whichever of ``IncrementalUpdatesService``/``FeedbackUpdateService``
owns it. It contains no accept/reject logic of its own beyond that dispatch.
"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import NotFoundError, ValidationError
from app.core.messages import (
    MSG_PROJECT_NOT_FOUND,
    MSG_UPDATE_ENTITY_NOT_FOUND,
    MSG_UPDATE_NO_PENDING_CHANGE,
)
from app.db.unit_of_work import UnitOfWork
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.schemas.feedback_updates_schema import (
    FeedbackChangeType,
    FeedbackEntityType,
    FeedbackUpdateAcceptRequest,
    FeedbackUpdateRejectRequest,
)
from app.schemas.incremental_updates_schema import (
    UpdateAcceptRequest,
    UpdateChangeType,
    UpdateDecisionResponse,
    UpdateEntityType,
    UpdateRejectRequest,
)
from app.services.feedback_update_service import FeedbackUpdateService
from app.services.incremental_updates_service import IncrementalUpdatesService


class UpdatesService:
    """Routes an accept/reject decision to whichever flow has it pending."""

    def __init__(
        self,
        module_feature_repo: ModuleFeatureRepository | None = None,
        user_story_repo: UserStoryRepository | None = None,
        incremental_service: IncrementalUpdatesService | None = None,
        feedback_service: FeedbackUpdateService | None = None,
    ) -> None:
        self._mf_repo = module_feature_repo or ModuleFeatureRepository()
        self._us_repo = user_story_repo or UserStoryRepository()
        self._incremental_service = incremental_service or IncrementalUpdatesService(
            user_story_repo=self._us_repo, module_feature_repo=self._mf_repo
        )
        self._feedback_service = feedback_service or FeedbackUpdateService(
            module_feature_repo=self._mf_repo, user_story_repo=self._us_repo
        )

    async def accept_update(
        self,
        *,
        project_id: UUID,
        payload: UpdateAcceptRequest,
        actor_user_id: UUID | None,
        uow: UnitOfWork,
    ) -> UpdateDecisionResponse:
        """Accept a pending change, dispatched to whichever flow flagged it.

        Raises:
            NotFoundError: If the project or the target entity does not exist.
            ValidationError: If the entity has neither flag set (nothing
                pending to accept).
        """
        if uow.projects.get_by_uuid(project_id) is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        is_feedback = await self._is_feedback_pending(
            project_id, payload.entity_type, payload.entity_id
        )
        if not is_feedback:
            return await self._incremental_service.accept_update(
                project_id=project_id, payload=payload, actor_user_id=actor_user_id, uow=uow
            )

        feedback_result = await self._feedback_service.accept_feedback_update(
            project_id=project_id,
            payload=FeedbackUpdateAcceptRequest(
                entity_type=FeedbackEntityType(payload.entity_type.value),
                entity_id=payload.entity_id,
                change_type=FeedbackChangeType(payload.change_type.value),
                comment=payload.comment,
            ),
            actor_user_id=actor_user_id,
            uow=uow,
        )
        return UpdateDecisionResponse(
            entity_type=payload.entity_type,
            entity_id=feedback_result.entity_id,
            change_type=UpdateChangeType(feedback_result.change_type.value),
            action=feedback_result.action,
            deleted=feedback_result.deleted,
            comment=feedback_result.comment,
        )

    async def reject_update(
        self,
        *,
        project_id: UUID,
        payload: UpdateRejectRequest,
        actor_user_id: UUID | None,
        uow: UnitOfWork,
    ) -> UpdateDecisionResponse:
        """Reject a pending change, dispatched to whichever flow flagged it.

        Raises:
            NotFoundError: If the project or the target entity does not exist.
            ValidationError: If the entity has neither flag set (nothing
                pending to reject).
        """
        if uow.projects.get_by_uuid(project_id) is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        is_feedback = await self._is_feedback_pending(
            project_id, payload.entity_type, payload.entity_id
        )
        if not is_feedback:
            return await self._incremental_service.reject_update(
                project_id=project_id, payload=payload, actor_user_id=actor_user_id, uow=uow
            )

        feedback_result = await self._feedback_service.reject_feedback_update(
            project_id=project_id,
            payload=FeedbackUpdateRejectRequest(
                entity_type=FeedbackEntityType(payload.entity_type.value),
                entity_id=payload.entity_id,
                change_type=FeedbackChangeType(payload.change_type.value),
                comment=payload.reason,
            ),
            actor_user_id=actor_user_id,
            uow=uow,
        )
        return UpdateDecisionResponse(
            entity_type=payload.entity_type,
            entity_id=feedback_result.entity_id,
            change_type=UpdateChangeType(feedback_result.change_type.value),
            action=feedback_result.action,
            deleted=feedback_result.deleted,
            reason=payload.reason,
        )

    async def _is_feedback_pending(
        self, project_id: UUID, entity_type: UpdateEntityType, entity_id: str
    ) -> bool:
        """Classify which flow owns the entity's pending change.

        Returns True when ``feedback_change_type`` is the flag set, False
        when ``incremental_change_type`` is set (or when — contrary to the
        documented invariant that the two flows never both apply to one node
        — both happen to be set, in which case incremental review takes
        precedence).
        """
        flags = await self._get_pending_change_flags(project_id, entity_type, entity_id)
        if flags is None:
            raise NotFoundError(
                MSG_UPDATE_ENTITY_NOT_FOUND.format(
                    entity_type=entity_type.value, entity_id=entity_id, project_id=project_id
                )
            )
        incremental_flag, feedback_flag = flags
        if incremental_flag is None and feedback_flag is None:
            raise ValidationError(
                MSG_UPDATE_NO_PENDING_CHANGE.format(
                    entity_type=entity_type.value, entity_id=entity_id, project_id=project_id
                )
            )
        return incremental_flag is None and feedback_flag is not None

    async def _get_pending_change_flags(
        self, project_id: UUID, entity_type: UpdateEntityType, entity_id: str
    ) -> tuple[str | None, str | None] | None:
        if entity_type == UpdateEntityType.MODULE:
            node = await self._mf_repo.get_module_for_project(project_id, entity_id)
            return None if node is None else (node.incremental_change_type, node.feedback_change_type)
        if entity_type == UpdateEntityType.FEATURE:
            return await self._mf_repo.get_feature_pending_change_flags(project_id, entity_id)
        node = await self._us_repo.get_user_story_detail_for_project(project_id, entity_id)
        return None if node is None else (node.incremental_change_type, node.feedback_change_type)
