"""Business logic for user story graph workflows."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
import json
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.core.constants import INITIAL_ENTITY_VERSION, SOURCE_INGESTION_STATUS_QUEUED
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_type import SourceType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.messages import (
    MSG_PROJECT_NOT_FOUND,
    MSG_PROJECT_SOURCES_NOT_FOUND,
    MSG_USER_STORY_DELETE_REASON_REQUIRED,
    MSG_USER_STORY_FEEDBACK_ALREADY_IN_PROGRESS,
    MSG_USER_STORY_GENERATION_IN_PROGRESS,
    MSG_USER_STORY_NOT_FOUND_BY_ID,
)
from app.db.unit_of_work import UnitOfWork
from app.models.neo4j.module_feature_model import ChangeType, ModuleModel
from app.models.neo4j.user_story_model import UserStoryModel
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.schemas.rfp_pipeline_v2_graph_schema import AgileBacklogOutput
from app.schemas.srs_evidence_schema import SRSEvidenceInfo
from app.schemas.user_story_schema import (
    BulkStatusChangeRequest,
    BulkStatusChangeResponse,
    ChangeStatusRequest,
    ProjectUserStorySummaryResponse,
    SourceCodeStorySchema,
    SourceFileInfo,
    StoryFeedbackInput,
    SyncCandidateTreeResponse,
    UpdateBboxesResponse,
    UserStoryDetailResponse,
    UserStoryListItemResponse,
    UserStoryListResponse,
    UserStoryRegenerationQueuedResponse,
    UserStoryStatus,
    UserStoryStatusChangedResponse,
    UserStorySyncFlagsUpdatedResponse,
    UserStorySyncFlagsUpdateRequest,
    UserStoryTreeResponse,
    UserStoryVersionResponse,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_USER_STORY_UPDATED_FIELDS_CANDIDATES = (
    "user_story_code",
    "title",
    "description",
    "feature_id",
    "as_a",
    "i_want_to",
    "so_that",
    "acceptance_criteria",
    "nfrs",
    "technical_notes",
    "story_points",
    "justification",
    "l2_sources",
)


def _normalize_srs_evidence_entry(entry: Any) -> dict[str, Any]:
    """Normalize SRS evidence entry from dataclass or dict to plain dict."""
    if is_dataclass(entry):
        return asdict(entry)
    if isinstance(entry, dict):
        return entry
    return {}


class UserStoryService:
    """Validates and persists source-scoped user stories."""

    def __init__(
        self,
        repository: UserStoryRepository | None = None,
        module_feature_repository: ModuleFeatureRepository | None = None,
    ) -> None:
        self._repository = repository or UserStoryRepository()
        self._module_feature_repository = module_feature_repository or ModuleFeatureRepository()

    async def list_user_stories_by_project(
        self,
        *,
        project_id: UUID,
        skip: int,
        limit: int,
        status: str | None = None,
        version: int | None = None,
        module_id: str | None = None,
        feature_id: str | None = None,
        source_id: str | None = None,
        search_text: str | None = None,
        user_story_code: str | None = None,
        consensus_min: float | None = None,
        consensus_max: float | None = None,
        uow: UnitOfWork,
    ) -> UserStoryListResponse:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        (items, total), are_all_approved = await asyncio.gather(
            self._repository.list_user_stories_for_project(
                project_id=project_id,
                skip=skip,
                limit=limit,
                status=status,
                version=version,
                module_id=module_id,
                feature_id=feature_id,
                source_id=source_id,
                search_text=search_text,
                user_story_code=user_story_code,
                consensus_min=consensus_min,
                consensus_max=consensus_max,
            ),
            self._repository.are_all_user_stories_approved(project_id),
        )
        response = UserStoryListResponse(
            total=total,
            are_all_approved=are_all_approved,
            skip=skip,
            limit=limit,
            items=[self._to_list_item_response(req) for req in items],
        )
        return response

    async def get_project_user_story_summary(
        self,
        *,
        project_id: UUID,
        uow: UnitOfWork,
    ) -> ProjectUserStorySummaryResponse:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        summary = await self._repository.get_project_summary(project_id)
        return ProjectUserStorySummaryResponse(
            project_id=project_id,
            total_user_stories=summary["total_user_stories"],
            ready_count=summary["ready_count"],
            needs_edit_count=summary["needs_edit_count"],
            failed_count=summary["failed_count"],
            approved_count=summary["approved_count"],
            total_modules=summary["total_modules"],
            total_features=summary["total_features"],
        )

    async def get_user_stories_tree(
        self,
        *,
        project_id: UUID,
        uow: UnitOfWork,
        source_ingestion_id: str | None = None,
    ) -> UserStoryTreeResponse:
        """Return the full module → feature → user story tree for a project.

        Raises ``NotFoundError`` when the project does not exist.
        Returns an empty ``items`` list when no modules have been generated yet.
        ``source_ingestion_id``, when provided, keeps a Module/Feature/UserStory
        whose own ``source_ingestion_id`` matches (provenance can land at any
        of the three levels) or that has a surviving descendant; pruning
        everything else.
        """
        if uow.projects.get_by_uuid(project_id) is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        (
            items,
            are_all_approved_for_us,
            are_all_approved_for_mod,
            are_all_approved_for_fea,
        ) = await asyncio.gather(
            self._repository.list_user_stories_tree_for_project(
                project_id=project_id,
                source_ingestion_id=source_ingestion_id,
            ),
            self._repository.are_all_user_stories_approved(project_id),
            self._module_feature_repository.are_all_modules_approved(project_id),
            self._module_feature_repository.are_all_features_approved(project_id),
        )
        return UserStoryTreeResponse(
            are_all_approved_for_us=are_all_approved_for_us,
            are_all_approved_for_mod=are_all_approved_for_mod,
            are_all_approved_for_fea=are_all_approved_for_fea,
            items=items,
        )

    async def get_sync_candidate_tree(
        self,
        *,
        project_id: UUID,
        sync_target: str,
        uow: UnitOfWork,
    ) -> SyncCandidateTreeResponse:
        """Return the approved, not-yet-synced-to-``sync_target`` user story tree.

        ``sync_target`` is ``"jira"`` or ``"tap"``. Raises ``NotFoundError``
        when the project does not exist. Empty modules/features (no surviving
        story beneath them) are absent from ``items``.
        """
        if uow.projects.get_by_uuid(project_id) is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        items = await self._repository.list_sync_candidate_tree_for_project(
            project_id=project_id,
            sync_target=sync_target,
        )
        total_count = sum(
            len(feature.get("children") or [])
            for module in items
            for feature in (module.get("children") or [])
        )
        return SyncCandidateTreeResponse(
            sync_target=sync_target,
            total_count=total_count,
            items=items,
        )

    async def enqueue_user_story_generation(
        self,
        *,
        project_id: UUID,
        uow: UnitOfWork,
        user_id: UUID | None = None,
        skip_processing: bool = False,
    ) -> UserStoryRegenerationQueuedResponse:
        """Queue an initial (clean-slate) user story generation task.

        Called when modules/features are first approved.  No prior user stories
        or user feedback are passed to the AI \u2014 the worker starts from scratch.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
        from app.services.fragment_service import FragmentService
        from app.workers.document_task import generate_user_story_task

        source_ids = uow.sources.get_ids_by_project(project_id)
        if not source_ids:
            raise NotFoundError(MSG_PROJECT_SOURCES_NOT_FOUND.format(project_id=project_id))

        module_repository = ModuleFeatureRepository()
        modules = await module_repository.list_modules_by_project(project_id)

        from app.services.project_graph_service import ProjectGraphService

        project_metadata = ProjectGraphService().get_project_metadata(project_id=project_id)
        modules_payload = self._serialize_modules_and_features(
            modules,
            business_requirements=project_metadata["business_requirements"],
            exclusions=project_metadata["exclusions"],
        )

        fragments: list[Any] = []
        for source_id in source_ids:
            source = uow.sources.get_by_uuid(source_id)
            if source is None or source.is_deleted or source.project_id != project_id:
                continue
            fragments_response = await FragmentService().list_fragments(
                source_id=source_id, uow=uow
            )
            fragments.extend(fragments_response.fragments)

        source_id_strs = [str(sid) for sid in source_ids]

        from app.core.enums.source_ingestion_status import SourceIngestionStatus  # noqa: PLC0415
        from app.services.project_task_service import (  # noqa: PLC0415
            CreateTaskParams,
            ProjectTaskService,
        )
        from app.services.source_ingestion_service import SourceIngestionService  # noqa: PLC0415

        # Flip status to RUNNING synchronously, in the same request that queues
        # the Celery task — otherwise the ingestion (still tagged READY_FOR_REVIEW
        # from module_feature generation) reads as "ready for review" for however
        # long the task sits queued before a worker picks it up and writes RUNNING
        # itself, which is misleading to any client polling right after this call.
        ingestion_ids = SourceIngestionService.add_stage_by_source_ids(
            uow, source_ids, SourceIngestionStage.GENERATING_USER_STORY
        )
        for ingestion_id in ingestion_ids:
            uow.source_ingestions.update_fields(
                ingestion_id, status=SourceIngestionStatus.RUNNING.value
            )
        uow.commit()

        task_service = ProjectTaskService()
        task_id, task_db_id = task_service.create_task(
            project_id=project_id,
            user_id=user_id,
            params=CreateTaskParams(
                task_type="story_generation",
                status=SOURCE_INGESTION_STATUS_QUEUED,
                stage="user_story.generation.queued",
                meta={"source_ids": source_id_strs},
            ),
        )

        async_result = generate_user_story_task.apply_async(
            args=[
                str(project_id),
                self._serialize_fragments(fragments),
                modules_payload,
                task_db_id,
                skip_processing,
            ],
            kwargs={"source_ids": source_id_strs},
        )

        task_service.set_celery_task_id(task_id, async_result.id)

        return UserStoryRegenerationQueuedResponse(
            task_id=task_db_id,
            project_id=project_id,
            source_ids=source_ids,
            status=SOURCE_INGESTION_STATUS_QUEUED,
        )

    async def enqueue_user_story_regeneration(
        self,
        *,
        project_id: UUID,
        feedback: str | None = None,
        module_ids: list[str] | None = None,
        feature_ids: list[str] | None = None,
        user_story_ids: list[str] | None = None,
        uow: UnitOfWork,
        user_id: UUID | None = None,
    ) -> UserStoryRegenerationQueuedResponse:
        """Queue a feedback-driven user story regeneration task.

        Called from the API when a user explicitly requests regeneration with
        optional feedback.  The current user story snapshot and feedback are
        forwarded to the AI so it can produce a refined backlog.

        Parameters
        ----------
        module_ids, feature_ids, user_story_ids : list[str] | None
            Optional ids the feedback targets — recorded onto the
            SourceIngestion's ``entity_json`` for traceability only; does not
            scope which stories get regenerated.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        # ── Guard against regenerating while the RFP project's user story
        # generation is still running (kicked off via
        # PATCH /api/v1/projects/{project_id}/modules/status, status=approved).
        # Only that original ingestion is checked — it flips to
        # completed/failed once generation finishes, at which point
        # regeneration opens up again.
        running_ingestions = uow.source_ingestions.list_running_by_project(
            project_id, source_types=[SourceType.RFP.value, SourceType.ADDITIONAL_RFP.value]
        )
        for running in running_ingestions:
            if SourceIngestionStage.GENERATING_USER_STORY.value in (running.stages or []):
                raise ConflictError(
                    MSG_USER_STORY_GENERATION_IN_PROGRESS.format(ingestion_id=running.id)
                )

        from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
        from app.services.fragment_service import FragmentService
        from app.workers.document_task import regenerate_user_story_task

        # No sources is a valid state here — regeneration can be feedback-only;
        # a dedicated SourceIngestion row is created for this request below
        # regardless of whether any sources exist.
        source_ids = uow.sources.get_ids_by_project(project_id)

        module_repository = ModuleFeatureRepository()
        modules = await module_repository.list_modules_by_project(project_id)

        from app.services.project_graph_service import ProjectGraphService

        project_metadata = ProjectGraphService().get_project_metadata(project_id=project_id)
        modules_payload = self._serialize_modules_and_features(
            modules,
            business_requirements=project_metadata["business_requirements"],
            exclusions=project_metadata["exclusions"],
        )

        fragments: list[Any] = []
        for source_id in source_ids:
            source = uow.sources.get_by_uuid(source_id)
            if source is None or source.is_deleted or source.project_id != project_id:
                continue
            fragments_response = await FragmentService().list_fragments(
                source_id=source_id, uow=uow
            )
            fragments.extend(fragments_response.fragments)

        existing_user_stories, _ = await self._repository.list_user_stories_for_project(
            project_id=project_id,
            skip=0,
            limit=5000,
        )
        user_stories_arg: str | dict[str, Any] = (
            self._build_user_stories_arg(
                user_stories=existing_user_stories,
                modules=modules,
                persona_glossary=project_metadata["persona_glossary"],
            )
            if existing_user_stories
            else ""
        )

        feedback_arg = (feedback or "").strip()

        source_id_strs = [str(sid) for sid in source_ids]

        from app.core.enums.source_ingestion_status import SourceIngestionStatus  # noqa: PLC0415
        from app.services.project_task_service import (  # noqa: PLC0415
            CreateTaskParams,
            ProjectTaskService,
        )

        regeneration_entity_json: dict[str, Any] = {"feedback": feedback_arg}
        if module_ids:
            regeneration_entity_json["module_ids"] = module_ids
        if feature_ids:
            regeneration_entity_json["feature_ids"] = feature_ids
        if user_story_ids:
            regeneration_entity_json["user_story_ids"] = user_story_ids

        # Every feedback-driven regeneration gets its own SourceIngestion row
        # (never tags/reuses whichever ingestion the project's sources already
        # happen to be linked to) so each request is independently trackable.
        # Starts `running`; the worker flips it to `completed`/`failed` once
        # regeneration finishes.
        ingestion = uow.source_ingestions.create_ingestion(
            project_id=project_id,
            source_type=SourceType.REQUIREMENT_UPDATE.value,
            status=SourceIngestionStatus.RUNNING.value,
            stages=[
                SourceIngestionStage.GENERATING_MODULE_FEATURE.value,
                SourceIngestionStage.GENERATING_USER_STORY.value,
            ],
            entity_json=regeneration_entity_json,
            user_story_gen_started_at=datetime.now(UTC),
        )
        uow.commit()
        ingestion_id = str(ingestion.id)

        task_service = ProjectTaskService()
        task_id, task_db_id = task_service.create_task(
            project_id=project_id,
            user_id=user_id,
            params=CreateTaskParams(
                task_type="story_regeneration",
                status=SOURCE_INGESTION_STATUS_QUEUED,
                stage="user_story.regeneration.queued",
                meta={"source_ids": source_id_strs},
            ),
        )

        async_result = regenerate_user_story_task.apply_async(
            args=[
                str(project_id),
                self._serialize_fragments(fragments),
                modules_payload,
                user_stories_arg,
                feedback_arg,
                task_db_id,
            ],
            kwargs={"ingestion_id": ingestion_id},
        )

        task_service.set_celery_task_id(task_id, async_result.id)

        return UserStoryRegenerationQueuedResponse(
            task_id=task_db_id,
            project_id=project_id,
            source_ids=source_ids,
            status=SOURCE_INGESTION_STATUS_QUEUED,
        )

    @staticmethod
    def _guard_against_conflicting_story_feedback(
        project_id: UUID,
        requested_ids: set[str],
        uow: UnitOfWork,
    ) -> None:
        """Raise ConflictError if `requested_ids` can't safely receive feedback right now.

        Two concurrent regenerate-by-feedback requests are allowed as long as
        they target disjoint user stories. Each request gets its own
        SourceIngestion row (status "running" while the patch is in flight);
        if any requested story is already referenced by a still-running
        ingestion's `entity_json.user_story_ids`, reject the new request
        instead of racing both Celery workers on the same node. Once that
        ingestion leaves "running" (completed/failed), the same story is free
        to receive new feedback again.

        Separately (not scoped to specific stories): if the RFP project's
        whole-tree user story generation is still running — kicked off via
        PATCH /api/v1/projects/{project_id}/modules/status, status=approved —
        reject any feedback outright, since the story tree it targets may not
        even exist yet / is about to be rewritten wholesale.
        """
        running_ingestions = uow.source_ingestions.list_running_by_project(project_id)
        for running in running_ingestions:
            if running.source_type in (
                SourceType.RFP.value,
                SourceType.ADDITIONAL_RFP.value,
            ) and SourceIngestionStage.GENERATING_USER_STORY.value in (running.stages or []):
                raise ConflictError(
                    MSG_USER_STORY_GENERATION_IN_PROGRESS.format(ingestion_id=running.id)
                )
            in_flight_ids = set((running.entity_json or {}).get("user_story_ids") or [])
            overlap = requested_ids & in_flight_ids
            if overlap:
                raise ConflictError(
                    MSG_USER_STORY_FEEDBACK_ALREADY_IN_PROGRESS.format(
                        user_story_ids=sorted(overlap),
                        ingestion_id=running.id,
                    )
                )

    async def enqueue_user_story_regeneration_by_feedback(
        self,
        *,
        project_id: UUID,
        feedback_items: list[StoryFeedbackInput],
        uow: UnitOfWork,
        user_id: UUID | None = None,
        skip_processing: bool = False,
    ) -> UserStoryRegenerationQueuedResponse:
        """Queue a targeted patch regeneration driven by per-story feedback.

        Fetches the full story data from Neo4j for each feedback item, builds
        feature contexts and valid sources, then hands everything to the Celery
        worker which calls run_agile_backlog_patch and upserts the revised stories.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        requested_ids = {item.user_story_id for item in feedback_items}
        self._guard_against_conflicting_story_feedback(project_id, requested_ids, uow)

        from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
        from app.workers.document_task import regenerate_user_stories_by_feedback_task

        # ── 1. Fetch full story data for each feedback item ───────────────
        tasks = [
            self._repository.get_user_story_detail_for_project(project_id, item.user_story_id)
            for item in feedback_items
        ]
        story_models = await asyncio.gather(*tasks)

        story_feedbacks: list[dict[str, Any]] = []
        feature_ids_ordered: list[str] = []
        seen_feature_ids: set[str] = set()
        story_code_to_feature_id: dict[str, str] = {}

        for item, model in zip(feedback_items, story_models, strict=False):
            if model is None:
                raise NotFoundError(
                    MSG_USER_STORY_NOT_FOUND_BY_ID.format(user_story_id=item.user_story_id)
                )
            normalized_sources = self._normalize_story_sources(model.sources)
            ac_list = [
                {
                    "type": ac.get("type", ""),
                    "given": ac.get("given", ""),
                    "when": ac.get("when", ""),
                    "then": ac.get("then", ""),
                }
                for ac in (model.acceptance_criteria or [])
            ]
            story_dict = {
                "user_story_id": model.id,
                "user_story_code": model.user_story_code,
                "title": model.title,
                "as_a": model.as_a or "",
                "i_want_to": model.i_want_to or "",
                "so_that": model.so_that or "",
                "acceptance_criteria": ac_list,
                "technical_notes": model.technical_notes or "",
                "story_points": model.story_points or 3,
                "sources": normalized_sources,
            }
            story_feedbacks.append(
                {
                    "story": story_dict,
                    "feature_id": model.feature_id or "",
                    "overall_feedback": item.overall_feedback,
                    "specific_feedback": [
                        {
                            "selected_text": sf.selected_text,
                            "selected_feedback": sf.selected_feedback,
                        }
                        for sf in (item.specific_feedback or [])
                    ]
                    or None,
                }
            )
            if model.feature_id and model.feature_id not in seen_feature_ids:
                seen_feature_ids.add(model.feature_id)
                feature_ids_ordered.append(model.feature_id)
            if model.feature_id:
                story_code_to_feature_id[model.user_story_code] = model.feature_id

        # ── 2. Build feature_contexts with sibling stories ────────────────
        patched_story_ids = {item.user_story_id for item in feedback_items}
        module_repository = ModuleFeatureRepository()
        modules = await module_repository.list_modules_by_project(project_id)

        # Build feature_id → FeatureModel lookup from all modules
        feature_lookup: dict[str, Any] = {}
        for mod in modules:
            for feat in mod.features:
                feature_lookup[feat.id] = feat

        # Fetch sibling stories per unique feature
        sibling_tasks = [
            self._repository.list_user_stories_for_project(
                project_id=project_id,
                feature_id=fid,
                skip=0,
                limit=500,
            )
            for fid in feature_ids_ordered
        ]
        sibling_results = await asyncio.gather(*sibling_tasks)

        feature_contexts: list[dict[str, Any]] = []
        for fid, (sibling_stories, _) in zip(feature_ids_ordered, sibling_results, strict=False):
            feat = feature_lookup.get(fid)
            if feat is None:
                continue
            siblings = [
                {
                    "user_story_code": s.user_story_code,
                    "title": s.title,
                    "i_want_to": s.i_want_to or "",
                    "so_that": s.so_that or "",
                }
                for s in sibling_stories
                if s.id not in patched_story_ids
            ]
            feature_contexts.append(
                {
                    "fea_code": feat.fea_code or "",
                    "name": feat.name,
                    "description": feat.description or "",
                    "sibling_stories": siblings,
                }
            )

        # ── 3. Build valid_sources from all story sources (deduplicated) ──
        valid_sources = self._build_valid_sources_from_story_feedbacks(story_feedbacks)

        # ── 4. Get persona_glossary from ProjectMetadata node ────────────────
        from app.services.project_graph_service import ProjectGraphService

        persona_glossary_list = await asyncio.to_thread(
            ProjectGraphService().get_persona_glossary, project_id=project_id
        )
        persona_glossary = json.dumps(persona_glossary_list, ensure_ascii=False)

        # ── 5. Get LLM options from postgres project ──────────────────────
        options: dict[str, Any] = {}
        if project.llm_provider:
            options["llm_provider"] = project.llm_provider
        if project.llm_model:
            options["llm_model"] = project.llm_model
        if project.tenant_id and project.llm_provider:
            from app.services.tenant_llm_provider_service import (
                TenantLLMProviderService,  # noqa: PLC0415
            )

            api_key = TenantLLMProviderService(uow).get_active_api_key(
                project.tenant_id, project.llm_provider
            )
            if api_key:
                options["llm_api_key"] = api_key

        # ── 6. Enqueue Celery task ─────────────────────────────────────────
        source_ids = uow.sources.get_ids_by_project(project_id)
        source_id_strs = [str(sid) for sid in source_ids]

        from app.core.enums.source_ingestion_status import SourceIngestionStatus  # noqa: PLC0415
        from app.services.project_task_service import CreateTaskParams, ProjectTaskService

        feedback_entity_json: dict[str, Any] = {
            "user_story_ids": sorted(requested_ids),
            "feedback_items": [
                {
                    "user_story_id": item.user_story_id,
                    "overall_feedback": item.overall_feedback,
                    "specific_feedback": [
                        {
                            "selected_text": sf.selected_text,
                            "selected_feedback": sf.selected_feedback,
                        }
                        for sf in (item.specific_feedback or [])
                    ]
                    or None,
                }
                for item in feedback_items
            ],
        }
        # Every feedback-driven patch gets its own SourceIngestion row (never
        # tags/reuses whichever ingestion the project's sources already happen
        # to be linked to) so each request is independently trackable. Starts
        # `running`; the worker flips it to `completed`/`failed` once the
        # patch finishes.
        ingestion = uow.source_ingestions.create_ingestion(
            project_id=project_id,
            source_type=SourceType.REQUIREMENT_UPDATE.value,
            status=SourceIngestionStatus.RUNNING.value,
            stages=[SourceIngestionStage.GENERATING_REQUIREMENTS.value],
            entity_json=feedback_entity_json,
            started_at=datetime.now(UTC),
        )
        uow.commit()
        ingestion_id = str(ingestion.id)

        task_service = ProjectTaskService()
        task_id, task_db_id = task_service.create_task(
            project_id=project_id,
            user_id=user_id,
            params=CreateTaskParams(
                task_type="story_feedback_patch",
                status=SOURCE_INGESTION_STATUS_QUEUED,
                stage="user_story.feedback_patch.queued",
                meta={
                    "source_ids": source_id_strs,
                    "user_story_ids": sorted(requested_ids),
                    "user_story_count": len(requested_ids),
                },
            ),
        )

        async_result = regenerate_user_stories_by_feedback_task.apply_async(
            args=[
                str(project_id),
                story_feedbacks,
                feature_contexts,
                persona_glossary,
                json.dumps(valid_sources, ensure_ascii=False),
                story_code_to_feature_id,
                options,
                task_db_id,
            ],
            kwargs={"ingestion_id": ingestion_id, "skip_processing": skip_processing},
        )
        task_service.set_celery_task_id(task_id, async_result.id)

        return UserStoryRegenerationQueuedResponse(
            task_id=task_db_id,
            project_id=project_id,
            source_ids=source_ids,
            user_story_ids=sorted(requested_ids),
            status=SOURCE_INGESTION_STATUS_QUEUED,
        )

    async def upsert_user_stories_from_patch(
        self,
        *,
        project_id: UUID,
        patch_result: dict[str, Any],
        story_code_to_feature_id: dict[str, str],
        source_ingestion_id: str | None = None,
    ) -> dict[str, int]:
        """Persist revised stories returned by run_agile_backlog_patch.

        Each revised story is diffed against its existing UserStory node
        (matched by ``user_story_id``): a story with no existing node is
        stamped ``feedback_change_type=ADDED``; a story whose content changed
        has its prior state snapshotted to a ``UserStoryVersion`` node first,
        then is stamped ``UPDATED``; unchanged content leaves
        ``feedback_change_type`` untouched. Mirrors
        ``ModuleFeatureService._resolve_module_regeneration_state`` for the
        ``/user-stories/regenerate-by-feedback`` flow.

        Returns ``{"added": n, "updated": n}`` — tallied from each story's
        resolved ``feedback_change_type``, not from the upsert's row count
        (which would also include unchanged stories carried through as a
        no-op write).
        """
        from app.schemas.rfp_pipeline_v2_graph_schema import StoryPatch

        output = patch_result.get("output", "")
        if not output:
            return {"added": 0, "updated": 0}

        if isinstance(output, str):
            parsed = StoryPatch.model_validate_json(output)
        else:
            parsed = StoryPatch.model_validate(output)

        models: list[UserStoryModel] = []
        for story in parsed.revised_stories:
            feature_id = story_code_to_feature_id.get(story.user_story_code)
            story_sources = self._normalize_story_sources(
                [src.model_dump(mode="json") for src in story.sources]
            )
            source_ids = sorted({str(src.source_id) for src in story.sources if src.source_id})
            ac_list = [ac.model_dump(mode="json") for ac in story.acceptance_criteria]
            nfr_list = UserStoryService._normalize_story_nfrs(
                [nfr.model_dump(mode="json") for nfr in (story.nfrs or [])]
            )

            existing = (
                await self._repository.get_user_story_detail_for_project(
                    project_id, story.user_story_id
                )
                if story.user_story_id
                else None
            )
            user_story_id, feedback_change_type, version = (
                await self._resolve_story_feedback_state(
                    project_id=project_id,
                    story_user_story_id=story.user_story_id,
                    existing=existing,
                    user_story_code=story.user_story_code,
                    title=story.title,
                    as_a=story.as_a,
                    i_want_to=story.i_want_to,
                    so_that=story.so_that,
                    acceptance_criteria=ac_list,
                    technical_notes=story.technical_notes,
                    story_points=story.story_points,
                    nfrs=nfr_list,
                )
            )

            models.append(
                UserStoryModel(
                    id=user_story_id,
                    user_story_code=story.user_story_code,
                    title=story.title,
                    description=None,
                    consensus=0.0,
                    status=UserStoryStatus.READY.value,
                    version=version,
                    feature_id=feature_id or None,
                    project_id=project_id,
                    source_ingestion_id=source_ingestion_id,
                    as_a=story.as_a,
                    i_want_to=story.i_want_to,
                    so_that=story.so_that,
                    acceptance_criteria=ac_list,
                    nfrs=nfr_list,
                    technical_notes=story.technical_notes,
                    story_points=story.story_points,
                    sources=story_sources,
                    source_file_count=len(source_ids),
                    feedback_change_type=feedback_change_type,
                )
            )

        await self._repository.bulk_upsert_user_stories_for_project(
            project_id=project_id,
            user_stories=models,
        )
        return {
            "added": sum(1 for m in models if m.feedback_change_type == ChangeType.ADDED),
            "updated": sum(1 for m in models if m.feedback_change_type == ChangeType.UPDATED),
        }

    async def _resolve_story_feedback_state(
        self,
        *,
        project_id: UUID,
        story_user_story_id: str | None,
        existing: UserStoryModel | None,
        user_story_code: str,
        title: str,
        as_a: str,
        i_want_to: str,
        so_that: str,
        acceptance_criteria: list[dict],
        technical_notes: str,
        story_points: int,
        nfrs: list[dict],
    ) -> tuple[str, ChangeType | None, int]:
        """Resolve a patched story's persisted id, ``feedback_change_type``, and version.

        Snapshots the prior state to a ``UserStoryVersion`` before reporting
        ``UPDATED`` so a reject can restore it. A story with no matching
        existing node is ``ADDED`` (its ``user_story_id``, when the AI
        supplied one, is honored as-is rather than re-minted).
        """
        if existing is not None:
            if self._story_content_changed(
                existing,
                title=title,
                as_a=as_a,
                i_want_to=i_want_to,
                so_that=so_that,
                acceptance_criteria=acceptance_criteria,
                technical_notes=technical_notes,
                story_points=story_points,
                nfrs=nfrs,
            ):
                await self._repository.snapshot_user_story_version(existing.id)
                return existing.id, ChangeType.UPDATED, existing.version + 1
            return existing.id, None, existing.version

        user_story_id = story_user_story_id or self._generate_user_story_id(
            project_id=str(project_id), user_story_code=user_story_code
        )
        return user_story_id, ChangeType.ADDED, INITIAL_ENTITY_VERSION

    @staticmethod
    def _story_content_changed(
        existing: UserStoryModel,
        *,
        title: str,
        as_a: str,
        i_want_to: str,
        so_that: str,
        acceptance_criteria: list[dict],
        technical_notes: str,
        story_points: int,
        nfrs: list[dict],
    ) -> bool:
        """Compare a regenerated story's content against its stored node.

        Mirrors ``ModuleFeatureService._feature_content_changed``: ``sources``
        is deliberately excluded, since regeneration doesn't touch evidence,
        and volatile per-criterion/per-nfr identifiers (``ac_code``/``id``)
        are excluded from the comparison so a re-coded but otherwise
        unchanged criterion doesn't register as a content change.
        """
        existing_ac = [
            (ac.get("given", ""), ac.get("when", ""), ac.get("then", ""))
            for ac in (existing.acceptance_criteria or [])
        ]
        new_ac = [
            (ac.get("given", ""), ac.get("when", ""), ac.get("then", ""))
            for ac in acceptance_criteria
        ]
        existing_nfrs = [
            (n.get("category", ""), n.get("requirement", "")) for n in (existing.nfrs or [])
        ]
        new_nfrs = [(n.get("category", ""), n.get("requirement", "")) for n in nfrs]
        return (
            existing.title != title
            or (existing.as_a or "") != as_a
            or (existing.i_want_to or "") != i_want_to
            or (existing.so_that or "") != so_that
            or existing_ac != new_ac
            or (existing.technical_notes or "") != technical_notes
            or existing.story_points != story_points
            or existing_nfrs != new_nfrs
        )

    async def delete_user_stories_for_project(
        self,
        *,
        project_id: UUID,
        uow: UnitOfWork,
    ) -> int:
        """Delete all user stories for a project.

        Does not validate project existence — callers that need a 404 response
        (e.g. the route handler) must perform that check themselves before
        calling this method.  Worker tasks should not fail on a missing project
        row; they simply delete nothing and return 0.
        """
        source_ids = uow.sources.get_ids_by_project(project_id)
        result = await self._repository.delete_user_stories_for_project(
            project_id=project_id,
            source_ids=source_ids,
        )
        return result

    async def delete_user_story(
        self,
        *,
        project_id: UUID,
        user_story_id: str,
        uow: UnitOfWork,
        reason: str | None = None,
    ) -> dict:
        """Delete a single user story scoped to a project.

        - Approved status  → soft delete: sets ``is_current=False``, ``del_reason``,
          ``deleted_at``.  ``reason`` is required.
        - All other statuses → hard delete: node is permanently removed.

        Raises NotFoundError when the project or user story does not exist.
        Raises ValidationError when the user story status is 'approved' and
        no reason is provided.

        Returns a dict with keys: ``is_current``, ``del_reason``, ``deleted_at``.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        req_model = await self._repository.get_user_story_detail_for_project(
            project_id, user_story_id
        )
        if req_model is None:
            raise NotFoundError(MSG_USER_STORY_NOT_FOUND_BY_ID.format(user_story_id=user_story_id))
        if req_model.status == UserStoryStatus.APPROVED:
            if reason is None:
                raise ValidationError(MSG_USER_STORY_DELETE_REASON_REQUIRED)
            # Soft delete — preserve the node with is_current=False
            await self._repository.soft_delete_user_story_by_id(
                project_id=project_id,
                user_story_id=user_story_id,
                del_reason=reason,
            )
            from datetime import datetime as _dt

            deleted_at = _dt.now(UTC)
            result = {"is_current": False, "del_reason": reason, "deleted_at": deleted_at}
        else:
            # Hard delete — remove the node entirely
            await self._repository.delete_user_story_by_id(
                project_id=project_id,
                user_story_id=user_story_id,
            )
            result = {"is_current": True, "del_reason": None, "deleted_at": None}

        await self._cleanup_orphaned_feature_and_module(
            project_id=project_id,
            feature_id=req_model.feature_id,
        )
        return result

    async def _cleanup_orphaned_feature_and_module(
        self,
        *,
        project_id: UUID,
        feature_id: str | None,
    ) -> None:
        """Cascade-delete a feature (and its module) left with no user stories.

        After a user story is deleted, checks whether its parent feature still
        has any active (``is_current=true``) user stories left. If not, the
        feature is hard-deleted. When that feature was the module's last
        remaining feature, the module is hard-deleted too. No-ops silently
        when ``feature_id`` is absent or the counts indicate siblings remain.
        """
        if not feature_id:
            return
        remaining_stories = await self._repository.count_active_user_stories_for_feature(
            project_id=project_id,
            feature_id=feature_id,
        )
        if remaining_stories > 0:
            return

        module_id = await self._module_feature_repository.get_module_id_for_feature(
            project_id, feature_id
        )
        deleted = await self._module_feature_repository.delete_feature_by_id(project_id, feature_id)
        if deleted:
            logger.info(
                "Deleted orphaned feature %s (no remaining user stories) in project %s",
                feature_id,
                project_id,
            )
        if not module_id:
            return

        remaining_features = await self._module_feature_repository.count_features_for_module(
            project_id, module_id
        )
        if remaining_features == 0:
            module_deleted = await self._module_feature_repository.delete_module_by_id(
                project_id, module_id
            )
            if module_deleted:
                logger.info(
                    "Deleted orphaned module %s (no remaining features) in project %s",
                    module_id,
                    project_id,
                )

    async def upsert_user_stories_from_backlog(
        self,
        *,
        project_id: UUID,
        backlog_result: dict[str, Any],
        source_ingestion_id: str | None = None,
    ) -> int:
        """Store backlog stories (under epics) as UserStory nodes for the project."""
        payload = backlog_result.get("output", backlog_result)
        if isinstance(payload, str):
            parsed = AgileBacklogOutput.model_validate_json(payload)
        else:
            payload = self._normalize_backlog_user_story_codes(payload)
            parsed = AgileBacklogOutput.model_validate(payload)

        logger.info(
            "upsert_user_stories_from_backlog parsed_output=%s",
            len(parsed.epics) if parsed.epics else "None",
        )

        rfp_flag_map = UserStoryService._extract_rfp_flag_map(backlog_result)

        models: list[UserStoryModel] = []
        for epic in parsed.epics:
            for story in epic.stories:
                source_ids = sorted({str(src.source_id) for src in story.sources if src.source_id})
                story_sources = UserStoryService._normalize_story_sources(
                    [src.model_dump(mode="json") for src in story.sources]
                )

                # logger.info(
                #     "upsert_user_stories_from_backlog user_story_code=%s story.sources=%s",
                #     story.user_story_code,
                #     json.dumps([src.model_dump(mode="json") for src in story.sources], default=str),
                # )

                user_story_id = self._generate_user_story_id(
                    project_id=str(project_id),
                    user_story_code=story.user_story_code,
                )

                rfp_flagged_item = rfp_flag_map.get(story.user_story_code)
                status = (
                    UserStoryStatus.FAILED.value
                    if rfp_flagged_item
                    else UserStoryStatus.READY.value
                )

                models.append(
                    UserStoryModel(
                        id=user_story_id,
                        user_story_code=story.user_story_code,
                        title=story.title,
                        description=getattr(story, "description", None) or None,
                        consensus=0.0,
                        status=status,
                        version=INITIAL_ENTITY_VERSION,
                        feature_id=epic.feature_id or None,
                        project_id=project_id,
                        source_ingestion_id=source_ingestion_id,
                        as_a=story.as_a,
                        i_want_to=story.i_want_to,
                        so_that=story.so_that,
                        acceptance_criteria=[
                            ac.model_dump(mode="json") for ac in story.acceptance_criteria
                        ],
                        nfrs=UserStoryService._normalize_story_nfrs(
                            [nfr.model_dump(mode="json") for nfr in (story.nfrs or [])]
                        ),
                        technical_notes=story.technical_notes,
                        story_points=story.story_points,
                        sources=story_sources,
                        source_file_count=len(source_ids),
                        rfp_flagged_item=rfp_flagged_item,
                    )
                )

        logger.info(
            "upsert_user_stories_from_backlog models=%s",
            len(models),
        )

        return await self._repository.bulk_upsert_user_stories_for_project(
            project_id=project_id,
            user_stories=models,
        )

    async def upsert_user_stories_for_source_code(
        self,
        *,
        project_id: UUID,
        backlog_result: dict[str, Any],
        source_ingestion_id: str | None = None,
    ) -> int:
        """Store backlog stories (under epics) as UserStory nodes for the project."""
        payload = backlog_result.get("output", backlog_result)
        if isinstance(payload, str):
            payload = json.loads(payload)

        if not isinstance(payload, dict):
            raise ValidationError("Invalid source-code backlog payload format")

        epics = payload.get("epics")
        if not isinstance(epics, list):
            epics = []

        logger.info(
            "upsert_user_stories_for_source_code parsed_output=%s",
            len(epics) if epics else "None",
        )

        models: list[UserStoryModel] = []
        for epic in epics:
            if not isinstance(epic, dict):
                continue

            stories = epic.get("stories") or []
            if not isinstance(stories, list):
                continue

            epic_generation_metadata = epic.get("generation_metadata")

            for raw_story in stories:
                story = SourceCodeStorySchema.model_validate(raw_story)
                story_status = UserStoryService._resolve_source_code_story_status(
                    epic_generation_metadata, story.user_story_code
                )
                story_sources_raw = story.sources or []
                source_ids = sorted(
                    {str(src.source_id) for src in story_sources_raw if src.source_id}
                )
                story_sources = UserStoryService._normalize_story_sources(
                    [
                        {
                            "fragment_id": src.fragment_id,
                            "source_id": src.source_id,
                            "page": src.page,
                            "bbox": src.bbox.model_dump(mode="json"),
                        }
                        for src in story_sources_raw
                    ]
                )

                models.append(
                    UserStoryModel(
                        id=story.user_story_id,
                        user_story_code=story.user_story_code,
                        title=story.title,
                        description=getattr(story, "description", None) or None,
                        consensus=0.0,
                        status=story_status,
                        version=INITIAL_ENTITY_VERSION,
                        feature_id=str(epic.get("feature_id") or "") or None,
                        project_id=project_id,
                        source_ingestion_id=source_ingestion_id,
                        as_a=story.as_a,
                        i_want_to=story.i_want_to,
                        so_that=story.so_that,
                        acceptance_criteria=[
                            ac.model_dump(mode="json") for ac in story.acceptance_criteria
                        ],
                        nfrs=UserStoryService._normalize_story_nfrs(
                            [nfr.model_dump(mode="json") for nfr in (story.nfrs or [])]
                        ),
                        technical_notes=story.technical_notes,
                        story_points=story.story_points,
                        sources=story_sources,
                        l2_sources=list(story.l2_sources or []),
                        screens=[s.model_dump(mode="json") for s in (story.screens or [])] or None,
                        source_file_count=len(source_ids),
                    )
                )

        logger.info(
            "upsert_user_stories_for_source_code models=%s",
            len(models),
        )

        return await self._repository.bulk_upsert_user_stories_for_source_code(
            project_id=project_id,
            user_stories=models,
        )

    async def change_status_by_project(
        self,
        *,
        project_id: UUID,
        request: ChangeStatusRequest,
        uow: UnitOfWork,
        user_id: UUID | None = None,
    ) -> tuple[UUID, str, int]:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        from app.services.source_ingestion_service import SourceIngestionService  # noqa: PLC0415

        SourceIngestionService.raise_if_pipeline_running(uow, project_id)

        updated_count = await self._repository.change_all_user_story_status_by_project(
            str(project_id), request.status.value
        )
        if request.status == UserStoryStatus.APPROVED:
            await SourceIngestionService.try_complete_generation_ingestion(
                uow, project_id, self._repository, actor_user_id=user_id
            )
        return project_id, request.status.value, updated_count

    async def bulk_change_status_by_project(
        self,
        *,
        project_id: UUID,
        request: BulkStatusChangeRequest,
        uow: UnitOfWork,
        user_id: UUID | None = None,
    ) -> BulkStatusChangeResponse:
        """Change the status of specific user stories within a project.

        Only user stories reachable via the project's module/feature graph are
        updated — cross-project mutation is impossible at the repository level.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        from app.services.source_ingestion_service import SourceIngestionService  # noqa: PLC0415

        SourceIngestionService.raise_if_pipeline_running(uow, project_id)

        updated_count = await self._repository.bulk_change_user_story_status_by_ids(
            str(project_id),
            request.user_story_ids,
            request.status.value,
        )
        if request.status == UserStoryStatus.APPROVED:
            await SourceIngestionService.try_complete_generation_ingestion(
                uow, project_id, self._repository, actor_user_id=user_id
            )
        return BulkStatusChangeResponse(
            project_id=project_id,
            status=request.status.value,
            updated_count=updated_count,
            user_story_ids=request.user_story_ids,
        )

    async def change_status(
        self,
        *,
        user_story_id: str,
        request: ChangeStatusRequest,
        project_id: UUID,
        uow: UnitOfWork,
        user_id: UUID | None = None,
    ) -> UserStoryStatusChangedResponse:
        from app.services.source_ingestion_service import SourceIngestionService  # noqa: PLC0415

        SourceIngestionService.raise_if_pipeline_running(uow, project_id)

        updated = await self._repository.change_user_story_status(
            user_story_id, request.status.value
        )
        if updated is None:
            raise NotFoundError(MSG_USER_STORY_NOT_FOUND_BY_ID.format(user_story_id=user_story_id))

        if request.status == UserStoryStatus.APPROVED:
            await SourceIngestionService.try_complete_generation_ingestion(
                uow, project_id, self._repository, actor_user_id=user_id
            )

        return UserStoryStatusChangedResponse(
            id=updated.id,
            status=updated.status,
            project_id=updated.project_id,
        )

    async def update_sync_flags(
        self,
        *,
        user_story_id: str,
        request: UserStorySyncFlagsUpdateRequest,
    ) -> UserStorySyncFlagsUpdatedResponse:
        updated = await self._repository.update_user_story_sync_flags(
            user_story_id,
            is_jira_synced=request.is_jira_synced,
            is_tap_synced=request.is_tap_synced,
        )
        if updated is None:
            raise NotFoundError(MSG_USER_STORY_NOT_FOUND_BY_ID.format(user_story_id=user_story_id))

        return UserStorySyncFlagsUpdatedResponse(
            id=updated.id,
            is_jira_synced=updated.is_jira_synced,
            is_tap_synced=updated.is_tap_synced,
            project_id=updated.project_id,
        )

    async def update_user_story_bboxes(
        self,
        *,
        project_id: UUID,
        user_story_id: str,
        sources: list[dict] | None,
        uow: UnitOfWork,
    ) -> UpdateBboxesResponse:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        normalized_sources = self._normalize_story_sources(sources)
        updated = await self._repository.update_user_story_sources(
            user_story_id=user_story_id,
            sources=normalized_sources,
        )
        if updated is None:
            raise NotFoundError(MSG_USER_STORY_NOT_FOUND_BY_ID.format(user_story_id=user_story_id))
        response = UpdateBboxesResponse(
            id=updated.id,
            project_id=updated.project_id,
            sources=self._normalize_story_sources(updated.sources),
            created_at=updated.created_at,
            updated_at=updated.updated_at,
        )
        return response

    async def get_user_story_detail_by_project(
        self,
        *,
        project_id: UUID,
        user_story_id: str,
        uow: UnitOfWork,
        include_deleted: bool = False,
    ) -> UserStoryDetailResponse:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        req_model = await self._repository.get_user_story_detail_for_project(
            project_id, user_story_id, include_deleted
        )
        if req_model is None:
            raise NotFoundError(MSG_USER_STORY_NOT_FOUND_BY_ID.format(user_story_id=user_story_id))

        # Derive source IDs from the user story's own `sources` field rather
        # than from the project-level HAS_SOURCE edges, which may not exist in
        # all graph states and would leave source_files empty.
        seen_sids: set[str] = set()
        unique_source_uuids: list[UUID] = []
        for entry in req_model.sources or []:
            sid = (
                entry.get("source_id")
                if isinstance(entry, dict)
                else getattr(entry, "source_id", None)
            )
            if sid and sid not in seen_sids:
                seen_sids.add(sid)
                try:
                    unique_source_uuids.append(UUID(str(sid)))
                except (ValueError, AttributeError):
                    pass
        source_objects = (
            uow.sources.get_many_by_uuids(unique_source_uuids) if unique_source_uuids else []
        )

        latest_version = await self._repository.get_latest_user_story_version(user_story_id)
        last_previous_items = (
            UserStoryVersionResponse(
                id=latest_version.id,
                user_story_id=latest_version.user_story_id,
                feature_id=latest_version.feature_id,
                project_id=latest_version.project_id,
                user_story_code=latest_version.user_story_code,
                title=latest_version.title,
                description=latest_version.description,
                consensus=latest_version.consensus,
                status=latest_version.status,
                version=latest_version.version,
                as_a=latest_version.as_a,
                i_want_to=latest_version.i_want_to,
                so_that=latest_version.so_that,
                acceptance_criteria=latest_version.acceptance_criteria,
                nfrs=latest_version.nfrs,
                technical_notes=latest_version.technical_notes,
                story_points=latest_version.story_points,
                justification=latest_version.justification,
                incremental_change_type=latest_version.incremental_change_type,
                feedback_change_type=latest_version.feedback_change_type,
                sources=latest_version.sources,
                l2_sources=latest_version.l2_sources,
                is_jira_synced=latest_version.is_jira_synced,
                is_tap_synced=latest_version.is_tap_synced,
                created_at=latest_version.created_at,
                updated_at=latest_version.updated_at,
                snapshotted_at=latest_version.snapshotted_at,
            )
            if latest_version is not None
            else None
        )

        source_files = [
            SourceFileInfo(
                id=str(src.id),
                name=src.original_name,
                type=src.file_type.lower(),
                storage_key=src.storage_key,
                created_at=src.created_at,
                updated_at=src.updated_at,
            )
            for src in source_objects
        ]

        response = UserStoryDetailResponse(
            id=req_model.id,
            user_story_code=req_model.user_story_code,
            title=req_model.title,
            description=req_model.description,
            consensus=req_model.consensus,
            status=req_model.status,
            version=req_model.version,
            feature_id=req_model.feature_id,
            mfu_id=req_model.mfu_id,
            mod_code=req_model.mod_code,
            project_id=req_model.project_id,
            as_a=req_model.as_a,
            i_want_to=req_model.i_want_to,
            so_that=req_model.so_that,
            acceptance_criteria=req_model.acceptance_criteria,
            nfrs=req_model.nfrs,
            technical_notes=req_model.technical_notes,
            story_points=req_model.story_points,
            justification=req_model.justification,
            incremental_change_type=req_model.incremental_change_type,
            feedback_change_type=req_model.feedback_change_type,
            sources=self._normalize_story_sources(req_model.sources),
            l2_sources=req_model.l2_sources,
            screens=req_model.screens or None,
            rfp_flagged_item=req_model.rfp_flagged_item,
            text_diffs=req_model.text_diffs,
            is_current=req_model.is_current,
            is_jira_synced=req_model.is_jira_synced,
            is_tap_synced=req_model.is_tap_synced,
            del_reason=req_model.del_reason,
            deleted_at=req_model.deleted_at,
            source_file_count=len(source_files),
            source_files=source_files,
            srs_evidence=[
                SRSEvidenceInfo.model_validate(_normalize_srs_evidence_entry(evidence))
                for evidence in req_model.srs_evidence
                if _normalize_srs_evidence_entry(evidence)
            ],
            created_at=req_model.created_at,
            updated_at=req_model.updated_at,
            last_previous_items=last_previous_items,
        )
        response.updated_fields = UserStoryService._compute_updated_fields(
            response, last_previous_items
        )
        return response

    @staticmethod
    def _compute_updated_fields(
        current: UserStoryDetailResponse, previous: UserStoryVersionResponse | None
    ) -> list[str]:
        """Field names whose value differs between the current story and its last snapshot."""
        if previous is None:
            return []
        return [
            field
            for field in _USER_STORY_UPDATED_FIELDS_CANDIDATES
            if getattr(current, field) != getattr(previous, field)
        ]

    # ── Internal helpers ──────────────────────────────────────────────────

    @staticmethod
    def _generate_user_story_id(
        *,
        project_id: str | None = None,
        user_story_code: str | None = None,
    ) -> str:
        if project_id and user_story_code:
            return str(uuid5(NAMESPACE_URL, f"{project_id}:{user_story_code.strip()}"))
        return str(uuid4())

    @staticmethod
    def _normalize_backlog_user_story_codes(payload: Any) -> Any:
        """Normalize backlog story codes to U.S x.y.z expected by schema validators."""
        if not isinstance(payload, dict):
            return payload

        epics = payload.get("epics")
        if not isinstance(epics, list):
            return payload

        valid_us_code = re.compile(r"U\.S [1-9]\d*\.[1-9]\d*\.[1-9]\d*")
        legacy_code = re.compile(r".*-(\d+)-F(\d+)-S(\d+)", re.IGNORECASE)

        for epic_idx, epic in enumerate(epics, start=1):
            if not isinstance(epic, dict):
                continue

            if "parent_br" not in epic or not isinstance(epic.get("parent_br"), list):
                epic["parent_br"] = []

            stories = epic.get("stories")
            if not isinstance(stories, list):
                continue

            for story_idx, story in enumerate(stories, start=1):
                if not isinstance(story, dict):
                    continue

                raw_code = str(story.get("user_story_code") or "").strip()
                if valid_us_code.fullmatch(raw_code):
                    continue

                match = legacy_code.fullmatch(raw_code)
                if match:
                    module_n, feature_n, story_n = match.groups()
                    story["user_story_code"] = (
                        f"U.S {int(module_n)}.{int(feature_n)}.{int(story_n)}"
                    )
                else:
                    story["user_story_code"] = f"U.S {epic_idx}.1.{story_idx}"

        return payload

    @staticmethod
    def _extract_rfp_flag_map(backlog_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Map ``user_story_code`` -> flagged-item dict for stories the backlog quality gate rejected.

        Only populated when the backlog run itself failed — top-level ``status``
        is ``"FAIL"`` or ``generation_metadata.final_status`` contains ``"FAIL"``
        (e.g. ``"FAIL_CORRECTION_EXHAUSTED"``). A clean run returns an empty map,
        leaving every story at the default READY status.
        """
        top_status = str(backlog_result.get("status") or "").upper()
        generation_metadata = backlog_result.get("generation_metadata") or {}
        final_status = str(generation_metadata.get("final_status") or "").upper()
        if top_status != "FAIL" and "FAIL" not in final_status:
            return {}

        flag_map: dict[str, dict[str, Any]] = {}
        for item in generation_metadata.get("flagged_items") or []:
            if not isinstance(item, dict) or item.get("entity_type") != "story":
                continue
            entity_id = item.get("entity_id")
            if not entity_id:
                continue
            flag_map[str(entity_id)] = {
                "entity_id": str(entity_id),
                "entity_type": str(item.get("entity_type") or ""),
                "issue": str(item.get("issue") or ""),
                "suggested_fix": str(item.get("suggested_fix") or ""),
            }
        return flag_map

    @staticmethod
    def _resolve_source_code_story_status(
        generation_metadata: dict[str, Any] | None,
        user_story_code: str,
    ) -> str:
        """Map a source-code feature's ``generation_metadata.review_guidance`` to a story status.

        ``review_guidance`` is emitted once per feature (one derivation result covers every
        story in that feature). ``recommended_action`` + ``approve_as_is_allowed`` are the
        deterministic decision fields (see ``_REVIEW_GUIDELINES`` in feature_story_agent.py) —
        Every decision is gated by ``flagged_story_ids`` first: a story keeps READY unless its
        own ``user_story_code`` is explicitly named there (so an empty list, or a list that
        names other stories, leaves this story at READY). Only for a story that IS named does
        ``recommended_action``/``approve_as_is_allowed`` get evaluated (see
        ``_REVIEW_GUIDELINES`` in feature_story_agent.py) — ``NONE``/``SPOT_CHECK``/
        ``APPROVE_AS_IS`` with approval allowed still resolves to READY, ``EDIT`` to
        NEEDS_EDIT, and anything else (``REGENERATE``/``RERUN``) to FAILED. A missing or
        malformed ``review_guidance`` block is treated as FAILED.
        """
        review_guidance = (generation_metadata or {}).get("review_guidance")
        if not isinstance(review_guidance, dict):
            return UserStoryStatus.FAILED.value

        flagged_story_ids = {str(s) for s in (review_guidance.get("flagged_story_ids") or [])}
        if user_story_code not in flagged_story_ids:
            return UserStoryStatus.READY.value

        recommended_action = str(review_guidance.get("recommended_action") or "").upper()
        approve_as_is_allowed = bool(review_guidance.get("approve_as_is_allowed"))

        if approve_as_is_allowed and recommended_action in ("NONE", "SPOT_CHECK", "APPROVE_AS_IS"):
            return UserStoryStatus.READY.value

        return (
            UserStoryStatus.NEEDS_EDIT.value
            if recommended_action == "EDIT"
            else UserStoryStatus.FAILED.value
        )

    @staticmethod
    def _to_list_item_response(model: UserStoryModel) -> UserStoryListItemResponse:
        normalized_story_sources = UserStoryService._normalize_story_sources(model.sources)
        return UserStoryListItemResponse(
            id=model.id,
            user_story_code=model.user_story_code,
            title=model.title,
            description=model.description,
            consensus=model.consensus,
            status=model.status,
            version=model.version,
            feature_id=model.feature_id,
            project_id=model.project_id,
            as_a=model.as_a,
            i_want_to=model.i_want_to,
            so_that=model.so_that,
            acceptance_criteria=model.acceptance_criteria,
            nfrs=model.nfrs,
            technical_notes=model.technical_notes,
            story_points=model.story_points,
            sources=normalized_story_sources,
            l2_sources=model.l2_sources,
            rfp_flagged_item=model.rfp_flagged_item,
            is_current=model.is_current,
            is_jira_synced=model.is_jira_synced,
            is_tap_synced=model.is_tap_synced,
            del_reason=model.del_reason,
            deleted_at=model.deleted_at,
            source_file_count=len(normalized_story_sources),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _serialize_fragments(fragments: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": fragment.id,
                "source_id": str(fragment.source_id),
                "source_type": fragment.source_type,
                "frag_type": fragment.frag_type,
                "content": fragment.content,
                "bbox": [
                    {
                        "page": entry.page,
                        "bbox": {
                            "x": entry.bbox.x,
                            "y": entry.bbox.y,
                            "w": entry.bbox.w,
                            "h": entry.bbox.h,
                        },
                        "confidence": entry.confidence,
                    }
                    for entry in fragment.bbox
                ],
            }
            for fragment in fragments
        ]

    @staticmethod
    def _serialize_modules_and_features(
        modules: list[ModuleModel],
        *,
        business_requirements: list | None = None,
        exclusions: list | None = None,
    ) -> dict[str, Any]:
        return {
            "feature_inventory": [
                {
                    "id": module.id,
                    "mod_code": module.mod_code,
                    "name": module.name,
                    "description": module.description or "",
                    "features": [
                        {
                            "id": feature.id,
                            "fea_code": feature.fea_code,
                            "name": feature.name,
                            "description": feature.description or "",
                            "functions": [
                                {
                                    "fun_code": fn.fun_code,
                                    "name": fn.name,
                                    "description": fn.description or "",
                                }
                                for fn in feature.functions
                            ],
                            "sources": list(feature.sources),
                            "l2_sources": list(feature.l2_sources or []),
                        }
                        for feature in module.features
                    ],
                }
                for module in modules
            ],
            "business_requirements": business_requirements
            if business_requirements is not None
            else [],
            "exclusions": exclusions if exclusions is not None else [],
        }

    @staticmethod
    def _to_backlog_user_story(user_story: UserStoryModel) -> dict[str, Any]:
        story_sources = UserStoryService._normalize_story_sources(user_story.sources)
        return {
            "id": user_story.id,
            "user_story_code": user_story.user_story_code,
            "title": user_story.title,
            "description": user_story.description,
            "consensus": user_story.consensus,
            "status": user_story.status,
            "version": user_story.version,
            "feature_id": user_story.feature_id,
            "project_id": str(user_story.project_id) if user_story.project_id else None,
            "as_a": user_story.as_a,
            "i_want_to": user_story.i_want_to,
            "so_that": user_story.so_that,
            "acceptance_criteria": user_story.acceptance_criteria,
            "technical_notes": user_story.technical_notes,
            "story_points": user_story.story_points,
            "sources": story_sources,
            "l2_sources": list(user_story.l2_sources or []),
            "source_file_count": user_story.source_file_count,
        }

    @staticmethod
    def _build_user_stories_arg(
        user_stories: list[UserStoryModel],
        modules: list[ModuleModel],
        persona_glossary: list[dict],
    ) -> dict[str, Any]:
        """Build the structured backlog payload grouped by feature (epic).

        Groups existing user stories under their parent feature, enriching each
        epic with feature-level metadata (fea_code, name, description) sourced
        from the already-fetched modules list.  ``parent_br`` is left empty
        because that mapping is not stored on the Feature node.
        """
        feature_meta: dict[str, dict[str, Any]] = {
            feature.id: {
                "epic_code": feature.fea_code or "",
                "title": feature.name or "",
                "description": feature.description or "",
            }
            for module in modules
            for feature in module.features
        }

        epics_map: dict[str, dict[str, Any]] = {}
        for story in user_stories:
            fid = story.feature_id or ""
            if fid not in epics_map:
                meta = feature_meta.get(fid, {})
                epics_map[fid] = {
                    "epic_code": meta.get("epic_code", ""),
                    "title": meta.get("title", ""),
                    "feature_id": fid,
                    "parent_br": [],
                    "description": meta.get("description", ""),
                    "stories": [],
                }
            epics_map[fid]["stories"].append(
                {
                    "user_story_id": story.id,
                    "user_story_code": story.user_story_code,
                    "title": story.title,
                    "as_a": story.as_a or "",
                    "i_want_to": story.i_want_to or "",
                    "so_that": story.so_that or "",
                    "acceptance_criteria": story.acceptance_criteria,
                    "technical_notes": story.technical_notes or "",
                    "story_points": story.story_points,
                    "sources": UserStoryService._normalize_story_sources(story.sources),
                    "l2_sources": list(story.l2_sources or []),
                }
            )

        return {
            "persona_glossary": persona_glossary,
            "epics": list(epics_map.values()),
        }

    @staticmethod
    def _normalize_story_nfrs(raw_nfrs: list[Any] | None) -> list[dict[str, str]]:
        """Normalize user-story NFRs to the supported four-field structure.

        Accepts both shapes:
          - Source-code / old pipeline: { id, category, requirement }
          - RFP pipeline v2 (StoryNFRSchema): { id, category, description }
        Stores as { id, category, description, requirement } (UserStoryNFRDict),
        keeping whichever of description/requirement the source actually
        supplied rather than collapsing one into the other.
        """
        if not raw_nfrs:
            return []

        normalized: list[dict[str, str]] = []
        for item in raw_nfrs:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "id": str(item.get("id") or ""),
                    "category": str(item.get("category") or ""),
                    "description": str(item.get("description") or ""),
                    "requirement": str(item.get("requirement") or ""),
                }
            )
        return normalized

    @staticmethod
    def _coerce_story_source_page(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_story_bbox_entry(
        bbox_entry: Any,
    ) -> tuple[str | None, dict | None]:
        if not isinstance(bbox_entry, dict):
            return None, None

        if "bbox" in bbox_entry:
            bbox = bbox_entry.get("bbox")
            if not isinstance(bbox, dict):
                return None, None
            fragment_id = bbox_entry.get("fragment_id")
            return (str(fragment_id) if fragment_id else None), bbox

        return None, bbox_entry

    @staticmethod
    def _extract_nested_story_source_entries(
        *,
        source_id: str,
        pages: Any,
    ) -> list[tuple[str, int, str | None, dict | None]]:
        entries: list[tuple[str, int, str | None, dict | None]] = []
        if not isinstance(pages, list):
            return entries

        for page_entry in pages:
            entries.extend(
                UserStoryService._extract_story_source_entries_for_page(
                    source_id=source_id,
                    page_entry=page_entry,
                )
            )

        return entries

    @staticmethod
    def _extract_story_source_entries_for_page(
        *,
        source_id: str,
        page_entry: Any,
    ) -> list[tuple[str, int, str | None, dict | None]]:
        if not isinstance(page_entry, dict):
            return []

        page = UserStoryService._coerce_story_source_page(page_entry.get("page"))
        if page is None:
            return []

        page_entries: list[tuple[str, int, str | None, dict | None]] = []
        bboxes = page_entry.get("bboxes")
        if isinstance(bboxes, list):
            for bbox_entry in bboxes:
                fragment_id, bbox = UserStoryService._parse_story_bbox_entry(bbox_entry)
                if isinstance(bbox, dict):
                    page_entries.append((source_id, page, fragment_id, bbox))

        if page_entries:
            return page_entries
        return [(source_id, page, None, None)]

    @staticmethod
    def _extract_flat_story_source_entries(
        *,
        source_id: str,
        item: dict[str, Any],
    ) -> list[tuple[str, int, str | None, dict | None]]:
        entries: list[tuple[str, int, str | None, dict | None]] = []
        page = UserStoryService._coerce_story_source_page(item.get("page"))
        if page is None:
            return entries

        fragment_id = str(item.get("fragment_id")) if item.get("fragment_id") else None

        if isinstance(item.get("bbox"), dict):
            entries.append((source_id, page, fragment_id, item["bbox"]))

        bboxes = item.get("bboxes")
        if isinstance(bboxes, list):
            for bbox_entry in bboxes:
                nested_fragment_id, bbox = UserStoryService._parse_story_bbox_entry(bbox_entry)
                if isinstance(bbox, dict):
                    entries.append(
                        (
                            source_id,
                            page,
                            nested_fragment_id or fragment_id,
                            bbox,
                        )
                    )

        if not entries:
            entries.append((source_id, page, fragment_id, None))

        return entries

    @staticmethod
    def _extract_story_source_entries(
        story_sources: list[dict],
    ) -> list[tuple[str, int, str | None, dict | None]]:
        entries: list[tuple[str, int, str | None, dict | None]] = []

        for item in story_sources:
            if not isinstance(item, dict):
                continue

            source_id_raw = item.get("source_id")
            if not source_id_raw:
                continue
            source_id = str(source_id_raw)

            if isinstance(item.get("pages"), list):
                entries.extend(
                    UserStoryService._extract_nested_story_source_entries(
                        source_id=source_id,
                        pages=item.get("pages"),
                    )
                )
                continue

            entries.extend(
                UserStoryService._extract_flat_story_source_entries(
                    source_id=source_id,
                    item=item,
                )
            )

        return entries

    @staticmethod
    def _normalize_story_sources(story_sources: list[dict] | None) -> list[dict]:
        """Normalize story sources into nested source/page/bbox hierarchy.

        Rules
        -----
        * Multiple entries sharing the same ``(source_id, page)`` are merged.
        * Input entries may use either ``bbox`` (single dict) or ``bboxes`` (list).
        * Input may also use nested ``pages`` format from ``SourceRefSchema``:
          ``{"source_id": ..., "pages": [{"page": ..., "bboxes": [{"fragment_id": ..., "bbox": {...}}]}]}``.
        * Output shape is always:
          ``{"source_id": ..., "pages": [{"page": ..., "bboxes": [{"fragment_id": ..., "bbox": {...}}]}]}``.
        """
        source_order: list[str] = []
        page_order: dict[str, list[int]] = {}
        normalized: dict[str, dict[int, dict[str, Any]]] = {}

        for source_id, page, fragment_id, bbox in UserStoryService._extract_story_source_entries(
            story_sources or []
        ):
            if source_id not in normalized:
                normalized[source_id] = {}
                source_order.append(source_id)
                page_order[source_id] = []

            if page not in normalized[source_id]:
                normalized[source_id][page] = {
                    "page": page,
                    "bboxes": [],
                }
                page_order[source_id].append(page)

            if isinstance(bbox, dict):
                bbox_entry: dict[str, Any] = {"bbox": bbox}
                if fragment_id:
                    bbox_entry["fragment_id"] = fragment_id
                normalized[source_id][page]["bboxes"].append(bbox_entry)

        output: list[dict[str, Any]] = []
        for source_id in source_order:
            output.append(
                {
                    "source_id": source_id,
                    "pages": [normalized[source_id][page] for page in page_order[source_id]],
                }
            )

        return output

    @staticmethod
    def _build_valid_sources_from_story_feedbacks(
        story_feedbacks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge sources from all story feedback items into a deduplicated valid-sources pool.

        Produces the nested source_id → pages → bboxes structure expected by
        run_agile_backlog_patch.  Duplicate fragment_ids within the same
        (source_id, page) are silently collapsed.
        """
        # source_id → page → fragment_id → bbox entry
        pool: dict[str, dict[int, dict[str, dict[str, Any]]]] = {}
        source_order: list[str] = []
        page_order: dict[str, list[int]] = {}

        for item in story_feedbacks:
            story = item.get("story") or {}
            for src in story.get("sources") or []:
                sid = src.get("source_id")
                if not sid:
                    continue
                if sid not in pool:
                    pool[sid] = {}
                    source_order.append(sid)
                    page_order[sid] = []
                for pg in src.get("pages") or []:
                    page_num = pg.get("page")
                    if page_num is None:
                        continue
                    if page_num not in pool[sid]:
                        pool[sid][page_num] = {}
                        page_order[sid].append(page_num)
                    for bbox_entry in pg.get("bboxes") or []:
                        fid = bbox_entry.get("fragment_id")
                        if fid and fid not in pool[sid][page_num]:
                            pool[sid][page_num][fid] = bbox_entry

        result: list[dict[str, Any]] = []
        for sid in source_order:
            pages = []
            for pg_num in page_order[sid]:
                pages.append(
                    {
                        "page": pg_num,
                        "bboxes": list(pool[sid][pg_num].values()),
                    }
                )
            result.append({"source_id": sid, "pages": pages})
        return result

    @staticmethod
    def _flatten_story_sources_for_response(story_sources: list[dict]) -> list[dict]:
        """Flatten nested source/page refs to legacy API response shape."""
        normalized: dict[tuple[str, int], dict[str, Any]] = {}

        for source_id, page, fragment_id, bbox in UserStoryService._extract_story_source_entries(
            story_sources
        ):
            key = (source_id, page)
            if key not in normalized:
                normalized[key] = {
                    "source_id": source_id,
                    "fragment_id": fragment_id,
                    "page": page,
                    "bboxes": [],
                }
            elif fragment_id:
                current_fragment = normalized[key].get("fragment_id")
                if current_fragment is None:
                    normalized[key]["fragment_id"] = fragment_id
                elif current_fragment != fragment_id:
                    normalized[key]["fragment_id"] = None

            if isinstance(bbox, dict):
                normalized[key]["bboxes"].append(bbox)

        return list(normalized.values())
