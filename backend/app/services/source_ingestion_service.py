"""Business logic for SourceIngestion list operations."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.constants import (
    SOURCE_CODE_STALL_ADVISORY_THRESHOLD_SECONDS,
    SOURCE_STATUS_FAILED,
    STALE_INGESTION_GRACE_SECONDS,
    TASK_AI_TIME_LIMIT,
    TASK_PARSING_TIME_LIMIT,
)
from app.core.enums.activity_type import ActivityType
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.core.enums.source_type import SourceType
from app.core.exceptions import ConflictError, NotFoundError
from app.core.messages import (
    MSG_ACTIVITY_REQUIREMENT_UPDATE_REVIEW_COMPLETED,
    MSG_ACTIVITY_USER_STORIES_APPROVED,
    MSG_PIPELINE_RUNNING_APPROVAL_BLOCKED,
    MSG_PROJECT_NOT_FOUND,
    MSG_RFP_MODULE_FEATURE_GENERATION_RUNNING_APPROVAL_BLOCKED,
    MSG_RFP_USER_STORY_GENERATION_RUNNING_APPROVAL_BLOCKED,
    MSG_SOURCE_CODE_PIPELINE_RUNNING_APPROVAL_BLOCKED,
    MSG_SOURCE_INGESTION_STALE_AUTO_FAILED,
    SUMMARY_ACTIVITY_REQUIREMENT_UPDATE_REVIEW_COMPLETED,
    SUMMARY_ACTIVITY_USER_STORIES_APPROVED,
)
from app.schemas.source_ingestion_schema import (
    SourceIngestionListResponse,
    SourceIngestionResponse,
    SourceIngestionSourceResponse,
)
from app.services.project_service import ProjectService
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.db.unit_of_work import UnitOfWork
    from app.models.postgres.source_ingestion_model import SourceIngestion
    from app.models.postgres.source_model import Source
    from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
    from app.repositories.neo4j.user_story_repository import UserStoryRepository

logger = get_logger(__name__)


class SourceIngestionService:
    """Service for project-scoped SourceIngestion listing."""

    @staticmethod
    def _to_response(
        ingestion: SourceIngestion,
        sources: list[Source],
    ) -> SourceIngestionResponse:
        source_items = [
            SourceIngestionSourceResponse(
                id=source.id,
                original_name=source.original_name,
                status=source.status,
                file_type=source.file_type,
                upload_type=source.upload_type,
                file_size_bytes=source.file_size_bytes,
                storage_key=source.storage_key,
                created_at=source.created_at,
            )
            for source in sources
        ]

        return SourceIngestionResponse(
            id=ingestion.id,
            project_id=ingestion.project_id,
            run_code=ingestion.run_code,
            source_type=ingestion.source_type,
            stages=list(ingestion.stages),
            status=ingestion.status,
            description=ingestion.description,
            skip_processing=ingestion.skip_processing,
            entity_json=ingestion.entity_json,
            no_changes_explanation=ingestion.no_changes_explanation,
            errors=list(ingestion.errors),
            tot_modules_from_global_artifact=ingestion.tot_modules_from_global_artifact,
            tot_modules=ingestion.tot_modules,
            tot_features=ingestion.tot_features,
            tot_user_stories=ingestion.tot_user_stories,
            tot_modules_failed=ingestion.tot_modules_failed,
            tot_modules_updated=ingestion.tot_modules_updated,
            tot_features_updated=ingestion.tot_features_updated,
            tot_user_stories_updated=ingestion.tot_user_stories_updated,
            tot_modules_deleted=ingestion.tot_modules_deleted,
            tot_features_deleted=ingestion.tot_features_deleted,
            tot_user_stories_deleted=ingestion.tot_user_stories_deleted,
            tot_modules_accepted=ingestion.tot_modules_accepted,
            tot_modules_rejected=ingestion.tot_modules_rejected,
            tot_features_accepted=ingestion.tot_features_accepted,
            tot_features_rejected=ingestion.tot_features_rejected,
            tot_user_stories_accepted=ingestion.tot_user_stories_accepted,
            tot_user_stories_rejected=ingestion.tot_user_stories_rejected,
            mod_fea_gen_started_at=ingestion.mod_fea_gen_started_at,
            mod_fea_gen_completed_at=ingestion.mod_fea_gen_completed_at,
            user_story_gen_started_at=ingestion.user_story_gen_started_at,
            user_story_gen_completed_at=ingestion.user_story_gen_completed_at,
            started_at=ingestion.started_at,
            completed_at=ingestion.completed_at,
            created_at=ingestion.created_at,
            updated_at=ingestion.updated_at,
            sources=source_items,
        )

    @staticmethod
    def raise_if_pipeline_running(uow: UnitOfWork, project_id: UUID) -> None:
        """Block approve/feedback/incremental-update actions while a pipeline is running.

        Covers all three cases that leave the Module/Feature/UserStory tree
        mid-mutation: the source-code pipeline (which generates everything in
        one pass), the RFP pipeline generating modules/features, and the RFP
        pipeline generating user stories — any ``SourceIngestion`` row for
        this project still in ``running`` status blocks the action. The error
        message names whichever of the three is actually in flight.

        Raises:
            ConflictError: If any ingestion for this project is still running.
        """
        running_ingestions = uow.source_ingestions.list_running_by_project(project_id)
        if not running_ingestions:
            return

        rfp_source_types = {SourceType.RFP.value, SourceType.ADDITIONAL_RFP.value}
        for running in running_ingestions:
            if running.source_type == SourceType.SOURCE_CODE.value:
                raise ConflictError(
                    MSG_SOURCE_CODE_PIPELINE_RUNNING_APPROVAL_BLOCKED.format(
                        ingestion_id=running.id
                    )
                )

        for running in running_ingestions:
            if (
                running.source_type in rfp_source_types
                and SourceIngestionStage.GENERATING_MODULE_FEATURE.value in (running.stages or [])
            ):
                raise ConflictError(
                    MSG_RFP_MODULE_FEATURE_GENERATION_RUNNING_APPROVAL_BLOCKED.format(
                        ingestion_id=running.id
                    )
                )

        for running in running_ingestions:
            if (
                running.source_type in rfp_source_types
                and SourceIngestionStage.GENERATING_USER_STORY.value in (running.stages or [])
            ):
                raise ConflictError(
                    MSG_RFP_USER_STORY_GENERATION_RUNNING_APPROVAL_BLOCKED.format(
                        ingestion_id=running.id
                    )
                )

        raise ConflictError(
            MSG_PIPELINE_RUNNING_APPROVAL_BLOCKED.format(ingestion_id=running_ingestions[0].id)
        )

    @staticmethod
    async def try_complete_open_feedback_or_incremental_ingestions(
        uow: UnitOfWork,
        project_id: UUID,
        module_feature_repo: ModuleFeatureRepository,
        user_story_repo: UserStoryRepository,
        actor_user_id: UUID | None = None,
    ) -> None:
        """Complete any `requirement_update` ingestion whose flagged changes are all resolved.

        Call after every feedback/incremental accept-or-reject decision. A
        `ready_for_review` ingestion has nothing left to review once none of
        the Module/Feature/UserStory nodes it tagged still carry a non-null
        `incremental_change_type` — that's the same signal
        `IncrementalUpdatesService`/`FeedbackUpdateService` clear on accept/reject.
        The caller is responsible for committing the session.

        Reaching completion here means every item this ingestion flagged for
        review — whether from a feedback-driven regeneration or an
        incremental upload — has now been accepted or rejected, so this also
        records an activity-log entry and notifies the project owner and
        every assigned member with the accepted/rejected totals.
        ``actor_user_id`` attributes the log entry when the caller has one in
        scope (optional — several call sites don't carry one).
        """
        for ingestion in uow.source_ingestions.list_open_feedback_or_incremental_by_project(
            project_id
        ):
            pending = await module_feature_repo.count_pending_changes_by_ingestion(str(ingestion.id))
            pending += await user_story_repo.count_pending_changes_by_ingestion(str(ingestion.id))
            if pending == 0:
                uow.source_ingestions.update_fields(
                    ingestion.id,
                    status=SourceIngestionStatus.COMPLETED.value,
                    completed_at=datetime.now(UTC),
                )
                total_accepted = (
                    ingestion.tot_modules_accepted
                    + ingestion.tot_features_accepted
                    + ingestion.tot_user_stories_accepted
                )
                total_rejected = (
                    ingestion.tot_modules_rejected
                    + ingestion.tot_features_rejected
                    + ingestion.tot_user_stories_rejected
                )
                SourceIngestionService._notify_requirement_update_review_completed(
                    uow=uow,
                    project_id=project_id,
                    total_accepted=total_accepted,
                    total_rejected=total_rejected,
                    actor_user_id=actor_user_id,
                )

    @staticmethod
    async def try_complete_generation_ingestion(
        uow: UnitOfWork,
        project_id: UUID,
        user_story_repo: UserStoryRepository,
        actor_user_id: UUID | None = None,
    ) -> None:
        """Complete the project's RFP/source-code ingestion once its backlog is fully approved.

        Requires every user story in the project to be `approved` and no
        `requirement_update` (feedback/incremental) ingestion left
        unresolved — otherwise a still-open review round would be silently
        bypassed. Call after a user story is approved, and after
        `try_complete_open_feedback_or_incremental_ingestions` resolves the
        last open feedback/incremental ingestion. The caller is responsible
        for committing the session.

        Reaching completion here means every user story in the project — via
        bulk-status, single/all approve, or an incremental/feedback
        accept-reject decision that happened to approve the last one — is now
        approved, so this also records an activity-log entry and notifies the
        project owner and every assigned member. ``actor_user_id`` attributes
        the log entry when the caller has one in scope (optional — several
        call sites don't carry one).
        """
        ingestion = uow.source_ingestions.get_ready_for_review_generation_ingestion(project_id)
        if ingestion is None:
            return
        if uow.source_ingestions.has_unresolved_feedback_or_incremental(project_id):
            return
        if not await user_story_repo.are_all_user_stories_approved(project_id):
            return
        uow.source_ingestions.update_fields(
            ingestion.id,
            status=SourceIngestionStatus.COMPLETED.value,
            completed_at=datetime.now(UTC),
        )
        total_user_stories = await user_story_repo.count_approved_user_stories(project_id)
        SourceIngestionService._notify_user_stories_approved(
            uow=uow,
            project_id=project_id,
            total_user_stories=total_user_stories,
            actor_user_id=actor_user_id,
        )

    @staticmethod
    def _notify_user_stories_approved(
        *,
        uow: UnitOfWork,
        project_id: UUID,
        total_user_stories: int,
        actor_user_id: UUID | None,
    ) -> None:
        """Record an activity-log entry and notify the project owner and every
        assigned member that the project's user-story backlog is fully approved.

        Never raises — a notification/activity-log failure must not fail an
        already-successful status change.
        """
        from app.core.enums.notification_type import NotificationType  # noqa: PLC0415
        from app.services.activity_log_service import record_activity  # noqa: PLC0415
        from app.services.notification_service import publish_notification  # noqa: PLC0415

        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            return

        record_activity(
            project_id=project_id,
            activity_type=ActivityType.USER_STORIES_APPROVED,
            summary=SUMMARY_ACTIVITY_USER_STORIES_APPROVED,
            message=MSG_ACTIVITY_USER_STORIES_APPROVED.format(total_user_stories=total_user_stories),
            actor_user_id=actor_user_id,
            data={"total_user_stories": total_user_stories},
        )

        recipient_ids = {
            member.user_id for member in uow.project_members.list_by_project(project_id)
        }
        if project.owner_id is not None:
            recipient_ids.add(project.owner_id)

        message = f'{total_user_stories} user story(ies) approved in "{project.name}".'
        for recipient_id in recipient_ids:
            try:
                publish_notification(
                    user_id=recipient_id,
                    title=SUMMARY_ACTIVITY_USER_STORIES_APPROVED,
                    message=message,
                    notification_type=NotificationType.SUCCESS,
                    data={"project_id": str(project_id), "total_user_stories": total_user_stories},
                )
            except Exception:
                logger.warning(
                    "_notify_user_stories_approved: failed to notify project_id=%s user_id=%s",
                    project_id,
                    recipient_id,
                    exc_info=True,
                )

    @staticmethod
    def _notify_requirement_update_review_completed(
        *,
        uow: UnitOfWork,
        project_id: UUID,
        total_accepted: int,
        total_rejected: int,
        actor_user_id: UUID | None,
    ) -> None:
        """Record an activity-log entry and notify the project owner and every
        assigned member that a feedback/incremental review round is fully resolved.

        Never raises — a notification/activity-log failure must not fail an
        already-successful status change.
        """
        from app.core.enums.notification_type import NotificationType  # noqa: PLC0415
        from app.services.activity_log_service import record_activity  # noqa: PLC0415
        from app.services.notification_service import publish_notification  # noqa: PLC0415

        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            return

        record_activity(
            project_id=project_id,
            activity_type=ActivityType.REQUIREMENT_UPDATE_REVIEW_COMPLETED,
            summary=SUMMARY_ACTIVITY_REQUIREMENT_UPDATE_REVIEW_COMPLETED,
            message=MSG_ACTIVITY_REQUIREMENT_UPDATE_REVIEW_COMPLETED.format(
                total_accepted=total_accepted, total_rejected=total_rejected
            ),
            actor_user_id=actor_user_id,
            data={"total_accepted": total_accepted, "total_rejected": total_rejected},
        )

        recipient_ids = {
            member.user_id for member in uow.project_members.list_by_project(project_id)
        }
        if project.owner_id is not None:
            recipient_ids.add(project.owner_id)

        message = (
            f"{total_accepted} item(s) accepted, {total_rejected} item(s) rejected "
            f'in "{project.name}".'
        )
        for recipient_id in recipient_ids:
            try:
                publish_notification(
                    user_id=recipient_id,
                    title=SUMMARY_ACTIVITY_REQUIREMENT_UPDATE_REVIEW_COMPLETED,
                    message=message,
                    notification_type=NotificationType.SUCCESS,
                    data={
                        "project_id": str(project_id),
                        "total_accepted": total_accepted,
                        "total_rejected": total_rejected,
                    },
                )
            except Exception:
                logger.warning(
                    "_notify_requirement_update_review_completed: failed to notify "
                    "project_id=%s user_id=%s",
                    project_id,
                    recipient_id,
                    exc_info=True,
                )

    @staticmethod
    def add_stage_by_source_ids(
        uow: UnitOfWork, source_ids: list[UUID], stage: SourceIngestionStage
    ) -> set[UUID]:
        """Add *stage* to every SourceIngestion referenced by *source_ids*.

        Called from every module_feature/user_story generation and
        regeneration trigger point, as well as the source-code pipeline's
        own checkpoints, so an ingestion's ``stages`` array tracks which
        stage(s) have touched it — see ``SourceIngestionStage`` for both
        families of values. The caller is responsible for committing the
        session.

        Returns the set of ingestion ids that were actually tagged, so
        callers can detect when none of *source_ids* resolved to a linked
        ingestion (e.g. no sources, or sources with no ingestion link).
        """
        if not source_ids:
            return set()

        sources = uow.sources.get_many_by_uuids(source_ids)
        ingestion_ids = {s.source_ingestion_id for s in sources if s.source_ingestion_id}
        for ingestion_id in ingestion_ids:
            uow.source_ingestions.add_stage(ingestion_id, stage.value)
        return ingestion_ids

    @staticmethod
    def record_regeneration_feedback(
        uow: UnitOfWork,
        *,
        project_id: UUID,
        source_ids: list[UUID],
        stages: list[SourceIngestionStage],
        entity_json: dict[str, Any],
    ) -> None:
        """Record feedback-driven regeneration context onto SourceIngestion.

        Tags every entry in *stages* onto every ingestion reachable from
        *source_ids* and stores *entity_json* there (e.g. feedback text plus
        whichever module_ids/feature_ids/user_story_ids the caller's endpoint
        accepts). When none of *source_ids* resolves to a linked ingestion (no
        sources at all, or none of them carry a ``source_ingestion_id`` —
        e.g. a project regenerated purely from feedback with no uploaded
        file), creates a standalone SourceIngestion row instead — carrying
        all of *stages* — so the feedback is never silently dropped.

        The caller is responsible for committing the session.
        """
        ingestion_ids: set[UUID] = set()
        for stage in stages:
            ingestion_ids |= SourceIngestionService.add_stage_by_source_ids(uow, source_ids, stage)

        if ingestion_ids:
            for ingestion_id in ingestion_ids:
                uow.source_ingestions.update_fields(ingestion_id, entity_json=entity_json)
            return

        uow.source_ingestions.create_ingestion(
            project_id=project_id,
            source_type=SourceType.REQUIREMENT_UPDATE.value,
            status=SourceIngestionStatus.COMPLETED.value,
            stages=[stage.value for stage in stages],
            entity_json=entity_json,
        )

    def list_ingestions_for_user(
        self,
        uow: UnitOfWork,
        owner_id: UUID,
        skip: int,
        limit: int,
        status: str | None = None,
        source_type: str | None = None,
        search: str | None = None,
    ) -> dict:
        """Return paginated source-ingestion "pipelines" across projects owned by *owner_id*."""
        ingestions, total = uow.source_ingestions.list_by_owner_paginated(
            owner_id=owner_id,
            skip=skip,
            limit=limit,
            status=status,
            source_type=source_type,
            search=search,
        )

        items = [self._to_pipeline_dict(ingestion) for ingestion in ingestions]
        return {
            "items": items,
            "total": total,
            "skip": skip,
            "limit": limit,
            "has_next": (skip + limit) < total,
            "has_previous": skip > 0,
        }

    @staticmethod
    def _to_pipeline_dict(ingestion: SourceIngestion) -> dict:
        """Assemble a single SourceIngestion's owner-scoped "pipeline" response dict."""
        return {
            "id": str(ingestion.id),
            "run_code": ingestion.run_code,
            "project_id": str(ingestion.project_id),
            "project_name": ingestion.project.name if ingestion.project else None,
            "source_type": ingestion.source_type,
            "stages": list(ingestion.stages),
            "status": ingestion.status,
            "tot_modules": ingestion.tot_modules,
            "tot_features": ingestion.tot_features,
            "tot_user_stories": ingestion.tot_user_stories,
            "created_at": ingestion.created_at.isoformat() if ingestion.created_at else None,
            "updated_at": ingestion.updated_at.isoformat() if ingestion.updated_at else None,
        }

    @staticmethod
    def _stale_reference_time(ingestion: SourceIngestion) -> datetime | None:
        """Return the timestamp the currently in-flight stage of *ingestion* started at.

        RFP-family only (rfp/additional_rfp/meeting_notes/requirement_update)
        — see ``is_stale`` for why source_code is excluded. Picks whichever
        stage-specific "started" timestamp is open (started but not
        completed) — user_story generation, then module/feature generation —
        falling back to the ingestion's own ``started_at``/``created_at`` for
        a run still in its initial upload/parsing stage.
        """
        if ingestion.user_story_gen_started_at and not ingestion.user_story_gen_completed_at:
            return ingestion.user_story_gen_started_at
        if ingestion.mod_fea_gen_started_at and not ingestion.mod_fea_gen_completed_at:
            return ingestion.mod_fea_gen_started_at
        return ingestion.started_at or ingestion.created_at

    @staticmethod
    def _stale_threshold_seconds(ingestion: SourceIngestion) -> int:
        """Return the max plausible seconds *ingestion*'s in-flight stage can legitimately run.

        RFP-family only. Mirrors the Celery hard ``time_limit`` each stage's
        task is configured with (``app/core/constants.py``) plus
        ``STALE_INGESTION_GRACE_SECONDS`` — a task that's genuinely still
        running never exceeds its own hard limit, so a `running` row seen
        well past this point can only mean its worker process died without
        ever reaching the failure/retry handler.
        """
        if ingestion.user_story_gen_started_at and not ingestion.user_story_gen_completed_at:
            return TASK_AI_TIME_LIMIT + STALE_INGESTION_GRACE_SECONDS
        if ingestion.mod_fea_gen_started_at and not ingestion.mod_fea_gen_completed_at:
            return TASK_AI_TIME_LIMIT + STALE_INGESTION_GRACE_SECONDS
        return TASK_PARSING_TIME_LIMIT + STALE_INGESTION_GRACE_SECONDS

    @staticmethod
    def is_stale(ingestion: SourceIngestion, *, now: datetime) -> bool:
        """Return True if *ingestion* has been ``running`` past its stage's max plausible duration.

        Always False for ``source_type == "source_code"`` — that pipeline
        dispatches one sequential chain of per-module tasks and returns
        immediately (``source_code_task.py``'s ``_run_pipeline_orchestrator``),
        so no single task's hard ``time_limit`` bounds the full run, and
        module count is uncapped: a project with 20+ modules at a realistic
        per-module duration (~2.5h average, 10-11h outliers) can legitimately
        run 50+ hours. There is no fixed elapsed-time threshold that safely
        tells "still working through many modules" apart from "worker died"
        for that shape, so it's excluded from auto-fail entirely — see
        ``is_advisory_stale_source_code`` for its (non-mutating) advisory
        check instead.
        """
        if ingestion.source_type == SourceType.SOURCE_CODE.value:
            return False
        reference = SourceIngestionService._stale_reference_time(ingestion)
        if reference is None:
            return False
        elapsed_seconds = (now - reference).total_seconds()
        return elapsed_seconds > SourceIngestionService._stale_threshold_seconds(ingestion)

    @staticmethod
    def is_advisory_stale_source_code(ingestion: SourceIngestion, *, now: datetime) -> bool:
        """Return True if a source_code ingestion has run implausibly long.

        Advisory only — never used to change status (see ``is_stale``'s
        docstring for why source_code has no safe fixed auto-fail
        threshold). Reuses ``SOURCE_CODE_STALL_ADVISORY_THRESHOLD_SECONDS``
        (== ``COUNTER_TTL_SECONDS``, the codebase's own existing 54h
        worst-case-pipeline-duration assumption) purely so a human gets a log
        line to go check, rather than the run staying silently invisible.
        """
        if ingestion.source_type != SourceType.SOURCE_CODE.value:
            return False
        reference = ingestion.started_at or ingestion.created_at
        if reference is None:
            return False
        elapsed_seconds = (now - reference).total_seconds()
        return elapsed_seconds > SOURCE_CODE_STALL_ADVISORY_THRESHOLD_SECONDS

    def list_advisory_stale_source_code_ingestions(
        self, uow: UnitOfWork, *, now: datetime
    ) -> list[SourceIngestion]:
        """Return source_code ingestions running implausibly long, for advisory logging only.

        Unlike ``fail_stale_running_ingestions``, this never mutates state —
        see ``is_advisory_stale_source_code``.
        """
        return [
            ingestion
            for ingestion in uow.source_ingestions.list_running()
            if self.is_advisory_stale_source_code(ingestion, now=now)
        ]

    def fail_stale_running_ingestions(
        self, uow: UnitOfWork, *, now: datetime
    ) -> list[SourceIngestion]:
        """Find and fail SourceIngestion rows stuck ``running`` past their stage's max duration.

        Called from the periodic ``tasks.maintenance.detect_stale_ingestions``
        beat task — a source's worker can be killed mid-run (OOM, spot
        interruption, node replacement) without ever raising a Python
        exception, which leaves both the ingestion and its sources stuck
        ``running`` indefinitely (see ``.claude/rules/workers.md`` — no
        in-task exception means the normal failure path never runs). Marks
        every stale ingestion, and every Source row it covers, ``failed`` in
        one transaction. Never selects a ``source_code`` ingestion — see
        ``is_stale``. The caller is responsible for committing the session,
        and for notifying project owners / logging — this method only
        performs the DB-level transition.
        """
        stale = [
            ingestion
            for ingestion in uow.source_ingestions.list_running()
            if self.is_stale(ingestion, now=now)
        ]
        if not stale:
            return []

        ingestion_ids = [ingestion.id for ingestion in stale]
        for source in uow.sources.get_by_ingestion_ids(ingestion_ids):
            source.status = SOURCE_STATUS_FAILED
            source.processing_error = MSG_SOURCE_INGESTION_STALE_AUTO_FAILED

        for ingestion in stale:
            uow.source_ingestions.update_fields(
                ingestion.id, status=SourceIngestionStatus.FAILED.value
            )
            uow.source_ingestions.add_error(ingestion.id, MSG_SOURCE_INGESTION_STALE_AUTO_FAILED)

        return stale

    def list_source_ingestions(
        self,
        *,
        project_id: UUID,
        skip: int,
        limit: int,
        status: str | None,
        source_type: str | None,
        uow: UnitOfWork,
        requester_id: UUID | None = None,
        requester_roles: list[str] | None = None,
        requester_tenant_id: UUID | None = None,
    ) -> SourceIngestionListResponse:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        if requester_id is not None:
            ProjectService.assert_project_access(
                project=project,
                requester_id=requester_id,
                requester_roles=requester_roles or [],
                requester_tenant_id=requester_tenant_id,
                level="read",
                uow=uow,
            )

        ingestions, total = uow.source_ingestions.get_paginated(
            project_id=project_id,
            skip=skip,
            limit=limit,
            status=status,
            source_type=source_type,
        )

        ingestion_ids = [ingestion.id for ingestion in ingestions]
        related_sources = uow.sources.get_by_ingestion_ids(ingestion_ids)

        sources_by_ingestion: dict[UUID, list[Source]] = defaultdict(list)
        for source in related_sources:
            if source.source_ingestion_id is not None:
                sources_by_ingestion[source.source_ingestion_id].append(source)

        items = [
            self._to_response(ingestion, sources_by_ingestion.get(ingestion.id, []))
            for ingestion in ingestions
        ]

        return SourceIngestionListResponse(
            items=items,
            total=total,
            skip=skip,
            limit=limit,
        )
