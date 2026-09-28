"""Business logic for module-feature graph workflows."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any
from uuid import UUID, uuid4

from app.core.constants import SOURCE_STATUS_QUEUED
from app.core.enums.activity_type import ActivityType
from app.core.enums.source_ingestion_stage import SourceIngestionStage
from app.core.enums.source_type import SourceType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.messages import (
    MSG_ACTIVITY_MODULE_FEATURE_APPROVED,
    MSG_FEATURE_DELETE_REASON_REQUIRED,
    MSG_FEATURE_NOT_FOUND,
    MSG_FEATURE_REGENERATION_SOURCE_PROCESSING,
    MSG_MODULE_DELETE_REASON_REQUIRED,
    MSG_MODULE_FEATURE_GENERATION_IN_PROGRESS,
    MSG_MODULE_FEATURE_PENDING_FEEDBACK_CHANGE_APPROVAL_BLOCKED,
    MSG_MODULE_NOT_FOUND,
    MSG_PROJECT_NOT_FOUND,
    MSG_PROJECT_SOURCES_NOT_FOUND,
    MSG_SOURCE_CODE_METADATA_NOT_FOUND,
    SUMMARY_ACTIVITY_MODULE_FEATURE_APPROVED,
)
from app.db.unit_of_work import UnitOfWork
from app.models.neo4j.module_feature_model import (
    ChangeType,
    FeatureModel,
    FunctionModel,
    ModuleFeatureStatus,
    ModuleModel,
)
from app.models.neo4j.version_model import FeatureVersionModel, ModuleVersionModel
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
from app.schemas.module_feature_schema import (
    FeatureFeedbackItem,
    FeatureRegenerationQueuedResponse,
    FeatureRegenerationTarget,
    FeatureResponse,
    FeatureSourceFileResponse,
    FeatureTreeNode,
    FeatureVersionResponse,
    FunctionResponse,
    FunctionTreeNode,
    ModuleDetailResponse,
    ModuleFeatureFeatureSingleResponse,
    ModuleFeatureListItemResponse,
    ModuleFeatureListResponse,
    ModuleFeatureRegenerationQueuedResponse,
    ModuleFeatureResponse,
    ModuleFeatureSingleResponse,
    ModuleFeatureStatusChangeRequest,
    ModuleFeatureStatusChangeResponse,
    ModuleFeatureStatusEnum,
    ModuleSingleResponse,
    ModuleTreeListResponse,
    ModuleTreeNode,
    ModuleVersionResponse,
    SyncFlagsUpdateRequest,
)
from app.utils.logger import get_logger

# Imported lazily inside method to avoid circular imports at module load time.
# from app.schemas.langgraph_schema import GoldenSkeleton

logger = get_logger(__name__)

_FEATURE_UPDATED_FIELDS_CANDIDATES = (
    "name",
    "description",
    "fea_code",
    "mfu_id",
    "functions",
    "sources",
    "l2_sources",
    "is_infrastructure",
    "condensation_note",
)

_MODULE_UPDATED_FIELDS_CANDIDATES = (
    "name",
    "description",
    "mod_code",
)


class ModuleFeatureService:
    """Validates and persists source-scoped modules and features."""

    def __init__(self, repository: ModuleFeatureRepository | None = None) -> None:
        self._repository = repository or ModuleFeatureRepository()

    async def get_module_by_project(
        self,
        *,
        project_id: UUID,
        module_id: str,
        uow: UnitOfWork,
    ) -> ModuleSingleResponse:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        module = await self._repository.get_module_for_project(project_id, module_id)
        if module is None:
            raise NotFoundError(MSG_MODULE_NOT_FOUND.format(module_id=module_id, source_id=""))
        latest_version = await self._repository.get_latest_module_version(project_id, module_id)
        return ModuleSingleResponse(
            project_id=project_id,
            module=self._to_detail_response(
                module,
                last_previous_items=self._to_module_version_response(latest_version),
            ),
        )

    async def get_feature_by_module(
        self,
        *,
        project_id: UUID,
        module_id: str,
        feature_id: str,
        uow: UnitOfWork,
    ) -> ModuleFeatureFeatureSingleResponse:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        feature = await self._repository.get_feature_for_module(project_id, module_id, feature_id)
        if feature is None:
            raise NotFoundError(
                MSG_FEATURE_NOT_FOUND.format(feature_id=feature_id, module_id=module_id)
            )
        # module is guaranteed to exist here (get_feature_for_module's own match
        # pattern already requires it) — module_id is the internal Neo4j id, and
        # mod_code is the pipeline-facing business code (e.g. "MOD-ANCES") callers
        # need to correlate this feature back to source-code regeneration requests.
        module = await self._repository.get_module_for_project(project_id, module_id)
        latest_version = await self._repository.get_latest_feature_version(project_id, feature_id)
        return ModuleFeatureFeatureSingleResponse(
            project_id=project_id,
            module_id=module_id,
            mod_code=module.mod_code if module is not None else None,
            feature=self._to_feature_response(
                feature,
                last_previous_items=self._to_feature_version_response(latest_version),
                source_files=self._resolve_feature_source_files(feature, uow=uow),
            ),
        )

    @staticmethod
    def _resolve_feature_source_files(
        feature: FeatureModel, *, uow: UnitOfWork
    ) -> list[FeatureSourceFileResponse]:
        """Resolve the Source rows cited by *feature*'s ``sources`` evidence list."""
        source_uuids: list[UUID] = []
        for entry in feature.sources or []:
            try:
                source_uuids.append(UUID(str(entry["source_id"])))
            except (TypeError, ValueError, KeyError):
                continue
        if not source_uuids:
            return []

        seen: set[UUID] = set()
        unique_uuids = [sid for sid in source_uuids if not (sid in seen or seen.add(sid))]
        sources = uow.sources.get_many_by_uuids(unique_uuids)
        return [
            FeatureSourceFileResponse(
                id=source.id,
                name=source.original_name,
                type=source.file_type.lower() if source.file_type else source.file_type,
                storage_key=source.storage_key,
                created_at=source.created_at,
                updated_at=source.updated_at,
            )
            for source in sources
        ]

    async def update_module_sync_flags(
        self,
        *,
        project_id: UUID,
        module_id: str,
        payload: SyncFlagsUpdateRequest,
        uow: UnitOfWork,
    ) -> ModuleFeatureSingleResponse:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        module = await self._repository.update_module_sync_flags(
            project_id,
            module_id,
            is_jira_synced=payload.is_jira_synced,
            is_tap_synced=payload.is_tap_synced,
        )
        if module is None:
            raise NotFoundError(MSG_MODULE_NOT_FOUND.format(module_id=module_id, source_id=""))
        return ModuleFeatureSingleResponse(
            project_id=project_id,
            module=self._to_response(module),
        )

    async def update_feature_sync_flags(
        self,
        *,
        project_id: UUID,
        module_id: str,
        feature_id: str,
        payload: SyncFlagsUpdateRequest,
        uow: UnitOfWork,
    ) -> ModuleFeatureFeatureSingleResponse:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        feature = await self._repository.update_feature_sync_flags(
            project_id,
            module_id,
            feature_id,
            is_jira_synced=payload.is_jira_synced,
            is_tap_synced=payload.is_tap_synced,
        )
        if feature is None:
            raise NotFoundError(
                MSG_FEATURE_NOT_FOUND.format(feature_id=feature_id, module_id=module_id)
            )
        return ModuleFeatureFeatureSingleResponse(
            project_id=project_id,
            module_id=module_id,
            feature=self._to_feature_response(feature),
        )

    async def delete_module(
        self,
        *,
        project_id: UUID,
        module_id: str,
        uow: UnitOfWork,
        reason: str | None = None,
    ) -> dict:
        """Delete a module, its features, and their user stories, scoped to a project.

        - Approved status    -> soft delete: sets ``is_deleted``/``deletion_reason``/
          ``deleted_at`` on the Module and all of its Features, and ``is_current=false``/
          ``del_reason``/``deleted_at`` on every UserStory beneath them. ``reason`` is required.
        - All other statuses -> hard delete: the Module, its Features, their UserStories,
          and every attached Module/Feature/UserStory version snapshot are permanently removed.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        module = await self._repository.get_module_for_project(project_id, module_id)
        if module is None:
            raise NotFoundError(MSG_MODULE_NOT_FOUND.format(module_id=module_id, source_id=""))

        if module.status == ModuleFeatureStatus.APPROVED:
            if reason is None:
                raise ValidationError(MSG_MODULE_DELETE_REASON_REQUIRED)
            await self._repository.soft_delete_module_cascade(
                project_id=project_id, module_id=module_id, reason=reason
            )
            return {"is_deleted": True, "deletion_reason": reason, "deleted_at": datetime.now(UTC)}

        await self._repository.hard_delete_module_cascade(project_id=project_id, module_id=module_id)
        return {"is_deleted": True, "deletion_reason": None, "deleted_at": None}

    async def delete_feature(
        self,
        *,
        project_id: UUID,
        module_id: str,
        feature_id: str,
        uow: UnitOfWork,
        reason: str | None = None,
    ) -> dict:
        """Delete a single feature and its user stories, scoped to a project and its parent module.

        - Approved status    -> soft delete: sets ``is_deleted``/``deletion_reason``/
          ``deleted_at`` on the Feature, and ``is_current=false``/``del_reason``/``deleted_at``
          on every UserStory beneath it. ``reason`` is required.
        - All other statuses -> hard delete: the Feature, its UserStories, and every
          attached Feature/UserStory version snapshot are permanently removed.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        feature = await self._repository.get_feature_for_module(project_id, module_id, feature_id)
        if feature is None:
            raise NotFoundError(
                MSG_FEATURE_NOT_FOUND.format(feature_id=feature_id, module_id=module_id)
            )

        if feature.status == ModuleFeatureStatus.APPROVED:
            if reason is None:
                raise ValidationError(MSG_FEATURE_DELETE_REASON_REQUIRED)
            await self._repository.soft_delete_feature_by_id(
                project_id=project_id, module_id=module_id, feature_id=feature_id, reason=reason
            )
            return {"is_deleted": True, "deletion_reason": reason, "deleted_at": datetime.now(UTC)}

        await self._repository.hard_delete_feature_cascade(
            project_id=project_id, module_id=module_id, feature_id=feature_id
        )
        return {"is_deleted": True, "deletion_reason": None, "deleted_at": None}

    async def upsert_modules_and_features_v2(
        self,
        *,
        project_id: UUID,
        module_feature_skeleton: Any | None = None,
        is_regeneration: bool = False,
        source_ingestion_id: str | None = None,
    ) -> list[ModuleModel]:
        """Persist the full Module-Feature hierarchy from a ``GoldenSkeleton``.

        Converts each ``SkeletonModule`` (and its ``SkeletonFeature`` children)
        into ``ModuleModel`` / ``FeatureModel`` objects and delegates persistence
        to ``ModuleFeatureRepository.upsert_module_for_source``.

        Parameters
        ----------
        project_id:
            UUID of the owning project.
        module_feature_skeleton:
            A skeleton payload returned by the module-feature generation stage.
        is_regeneration:
            When True (feedback-driven ``/modules/regenerate`` runs), each
            module/feature is resolved against its existing node by business
            code (``mod_code``/``fea_code``) and stamped with
            ``feedback_change_type`` — ``ADDED`` for a code with no existing
            node, ``UPDATED`` when content actually changed (after snapshotting
            the prior state to a ``ModuleVersion``/``FeatureVersion``), or left
            untouched otherwise. False (the default, first-time generation from
            bulk source upload) never touches ``feedback_change_type``.
        source_ingestion_id:
            The ``SourceIngestion`` row this run was generated/regenerated
            from. Stamped on every module/feature persisted by this call —
            the repository only overwrites an existing node's value when that
            node was just added or updated, otherwise leaves it untouched.

        Returns
        -------
        list[ModuleModel]
            The list of ``ModuleModel`` objects that were persisted, in
            skeleton order.  Each entry includes its nested ``FeatureModel``
            children so callers can inspect or log what was stored.
        """
        skeleton_payload = module_feature_skeleton
        if skeleton_payload is None:
            return []

        stored_modules: list[ModuleModel] = []
        for skel_module in self._extract_skeleton_modules(skeleton_payload):
            module_code = self._read_attr(skel_module, "mod_code", "")
            module_name = self._read_attr(skel_module, "module_name", "name", default="")
            module_description = self._read_attr(skel_module, "description")

            existing_module = None
            if is_regeneration and module_code:
                existing_module = await self._repository.get_module_by_mod_code(
                    project_id, module_code
                )

            module_id, module_feedback_change_type = await self._resolve_module_regeneration_state(
                project_id=project_id,
                existing_module=existing_module,
                module_name=module_name,
                module_description=module_description,
                is_regeneration=is_regeneration,
            )

            existing_features_by_code = (
                {f.fea_code: f for f in existing_module.features if f.fea_code}
                if existing_module is not None
                else {}
            )

            features = [
                await self._build_regenerated_feature(
                    feat=feat,
                    project_id=project_id,
                    module_id=module_id,
                    existing_feature=existing_features_by_code.get(
                        self._read_attr(feat, "fea_code", "")
                    ),
                    is_regeneration=is_regeneration,
                    source_ingestion_id=source_ingestion_id,
                )
                for feat in self._read_attr(skel_module, "features", default=[])
            ]

            module_model = ModuleModel(
                id=module_id,
                project_id=project_id,
                source_ingestion_id=source_ingestion_id,
                mod_code=module_code,
                name=module_name,
                description=module_description,
                features=features,
                feedback_change_type=module_feedback_change_type,
            )
            await self._repository.upsert_modules_and_features_v2(project_id, module_model)
            stored_modules.append(module_model)
        return stored_modules

    async def _resolve_module_regeneration_state(
        self,
        *,
        project_id: UUID,
        existing_module: ModuleModel | None,
        module_name: str,
        module_description: str | None,
        is_regeneration: bool,
    ) -> tuple[str, ChangeType | None]:
        """Resolve a regenerated module's persisted id and ``feedback_change_type``.

        Snapshots the prior state to a ``ModuleVersion`` before reporting
        ``UPDATED`` so a reject can restore it. Always returns ``(id, None)``
        outside regeneration (first-time generation never touches
        ``feedback_change_type``).
        """
        if existing_module is None:
            return str(uuid4()), (ChangeType.ADDED if is_regeneration else None)

        module_id = existing_module.id
        if not is_regeneration:
            return module_id, None
        if (
            existing_module.name != module_name
            or existing_module.description != module_description
        ):
            await self._repository.snapshot_module_version(project_id, module_id)
            return module_id, ChangeType.UPDATED
        return module_id, None

    async def _build_regenerated_feature(
        self,
        *,
        feat: Any,
        project_id: UUID,
        module_id: str,
        existing_feature: FeatureModel | None,
        is_regeneration: bool,
        source_ingestion_id: str | None = None,
    ) -> FeatureModel:
        """Build a ``FeatureModel`` for one skeleton feature, diffing against
        ``existing_feature`` (matched by ``fea_code``) when regenerating."""
        feature_name = self._read_attr(feat, "feature_name", "name", default="")
        feature_description = self._read_attr(feat, "description")
        functions = [
            FunctionModel(
                fun_code=self._read_attr(fn, "fun_code"),
                name=self._read_attr(fn, "name", default=""),
                description=self._read_attr(fn, "description"),
                func_src_ref=self._read_attr(fn, "func_src_ref"),
            )
            for fn in self._read_attr(feat, "functions", default=[])
        ]
        l2_sources = self._read_attr(feat, "l2_sources", default=[])

        feature_id = existing_feature.id if existing_feature is not None else str(uuid4())
        feature_feedback_change_type: ChangeType | None = None
        if is_regeneration:
            if existing_feature is None:
                feature_feedback_change_type = ChangeType.ADDED
            elif self._feature_content_changed(
                existing_feature, feature_name, feature_description, functions, l2_sources
            ):
                await self._repository.snapshot_feature_version(project_id, feature_id)
                feature_feedback_change_type = ChangeType.UPDATED

        return FeatureModel(
            id=feature_id,
            project_id=project_id,
            source_ingestion_id=source_ingestion_id,
            module_id=module_id,
            fea_code=self._read_attr(feat, "fea_code", ""),
            name=feature_name,
            description=feature_description,
            generation_metadata=self._read_attr(feat, "generation_metadata"),
            is_infrastructure=self._read_attr(feat, "is_infrastructure"),
            condensation_note=self._read_attr(feat, "condensation_note"),
            functions=functions,
            sources=self._normalize_feature_sources(self._read_attr(feat, "sources", default=[])),
            l2_sources=l2_sources,
            feedback_change_type=feature_feedback_change_type,
        )

    @staticmethod
    def _feature_content_changed(
        existing: FeatureModel,
        name: str,
        description: str | None,
        functions: list[FunctionModel],
        l2_sources: list[str],
    ) -> bool:
        """Compare a regenerated feature's content against its stored node.

        Mirrors the diffing rule the source-code MFU regeneration flow already
        uses (``_feature_content_changed`` in ``app.workers.source_code_task``):
        ``fea_code`` and ``sources`` are deliberately excluded, since the AI
        mints a fresh ``fea_code`` on every regeneration run and ``sources``
        evidence is untouched by regeneration.
        """
        existing_functions = [
            (fn.fun_code, fn.name, fn.description, fn.func_src_ref) for fn in existing.functions
        ]
        new_functions = [(fn.fun_code, fn.name, fn.description, fn.func_src_ref) for fn in functions]
        return (
            existing.name != name
            or existing.description != description
            or existing_functions != new_functions
            or (existing.l2_sources or []) != (l2_sources or [])
        )

    async def upsert_modules_and_features_for_source_code(
        self,
        *,
        project_id: UUID,
        module_feature_skeleton: Any | None = None,
        source_ingestion_id: str | None = None,
    ) -> list[ModuleModel]:
        """Persist the full Module-Feature hierarchy from a ``GoldenSkeleton``.

        Converts each ``SkeletonModule`` (and its ``SkeletonFeature`` children)
        into ``ModuleModel`` / ``FeatureModel`` objects and delegates persistence
        to ``ModuleFeatureRepository.upsert_modules_and_features_for_source_code``.

        Parameters
        ----------
        project_id:
            UUID of the owning project.
        module_feature_skeleton:
            A skeleton payload returned by the module-feature generation stage.
        source_ingestion_id:
            The ``SourceIngestion`` row this run was generated from, stamped
            on every module/feature persisted by this call.

        Returns
        -------
        list[ModuleModel]
            The list of ``ModuleModel`` objects that were persisted, in
            skeleton order.  Each entry includes its nested ``FeatureModel``
            children so callers can inspect or log what was stored.
        """
        skeleton_payload = module_feature_skeleton
        if skeleton_payload is None:
            return []

        stored_modules: list[ModuleModel] = []
        for skel_module in self._extract_skeleton_modules(skeleton_payload):
            module_id = str(uuid4())
            module_code = self._read_attr(skel_module, "mod_code", "")
            module_name = self._read_attr(skel_module, "module_name", "name", default="")
            module_model = ModuleModel(
                id=module_id,
                project_id=project_id,
                source_ingestion_id=source_ingestion_id,
                mod_code=module_code,
                name=module_name,
                description=self._read_attr(skel_module, "description"),
                features=[
                    FeatureModel(
                        id=str(uuid4()),
                        project_id=project_id,
                        source_ingestion_id=source_ingestion_id,
                        module_id=module_id,
                        fea_code=self._read_attr(feat, "fea_code", ""),
                        mfu_id=self._read_attr(feat, "mfu_id"),
                        name=self._read_attr(feat, "feature_name", "name", default=""),
                        description=self._read_attr(feat, "description"),
                        generation_metadata=self._read_attr(feat, "generation_metadata"),
                        is_infrastructure=self._read_attr(feat, "is_infrastructure"),
                        condensation_note=self._read_attr(feat, "condensation_note"),
                        functions=[
                            FunctionModel(
                                fun_code=self._read_attr(fn, "fun_code"),
                                name=self._read_attr(fn, "name", default=""),
                                description=self._read_attr(fn, "description"),
                                func_src_ref=self._read_attr(fn, "func_src_ref"),
                            )
                            for fn in self._read_attr(feat, "functions", default=[])
                        ],
                        sources=self._normalize_feature_sources(
                            self._read_attr(feat, "sources", default=[])
                        ),
                        l2_sources=self._read_attr(feat, "l2_sources", default=[]),
                    )
                    for feat in self._read_attr(skel_module, "features", default=[])
                ],
            )
            await self._repository.upsert_modules_and_features_for_source_code(
                project_id, module_model
            )
            stored_modules.append(module_model)
        return stored_modules

    async def enqueue_module_feature_regeneration(
        self,
        *,
        project_id: UUID,
        feedback: str,
        module_ids: list[str] | None = None,
        feature_ids: list[str] | None = None,
        uow: UnitOfWork,
        user_id: UUID | None = None,
    ) -> ModuleFeatureRegenerationQueuedResponse:
        """Enqueue module/feature regeneration with AI processing.

        This method publishes realtime WebSocket status updates with the lifecycle:
        queued → running → ready_for_review/failed

        These statuses are NOT persisted to any database - they exist only for
        realtime communication with frontend clients via WebSocket.

        Parameters
        ----------
        project_id : UUID
            Project ID to regenerate modules for
        feedback : str
            User feedback to guide the regeneration
        module_ids : list[str] | None
            Optional module ids the feedback targets — recorded onto the
            SourceIngestion's ``entity_json`` for traceability only; does not
            scope which modules get regenerated.
        feature_ids : list[str] | None
            Optional feature ids the feedback targets — recorded onto the
            SourceIngestion's ``entity_json`` for traceability only; does not
            scope which features get regenerated.
        uow : UnitOfWork
            Database unit of work

        Returns
        -------
        ModuleFeatureRegenerationQueuedResponse
            Response with task_id for WebSocket subscription

        Raises
        ------
        NotFoundError
            If project not found
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        # ── Guard against regenerating while the RFP upload's module/feature
        # generation is still running (source ingestion created by
        # POST /api/v1/sources/upload/bulk). Only that original ingestion is
        # checked — it flips to ready_for_review/completed/failed once the
        # initial generation pass finishes, at which point regeneration opens up.
        running_ingestions = uow.source_ingestions.list_running_by_project(
            project_id, source_types=[SourceType.RFP.value, SourceType.ADDITIONAL_RFP.value]
        )
        for running in running_ingestions:
            if SourceIngestionStage.GENERATING_MODULE_FEATURE.value in (running.stages or []):
                raise ConflictError(
                    MSG_MODULE_FEATURE_GENERATION_IN_PROGRESS.format(ingestion_id=running.id)
                )

        from app.services.fragment_service import FragmentService
        from app.workers.document_task import regenerate_modules_and_features_task

        # No sources is a valid state here — regeneration can be feedback-only;
        # a dedicated SourceIngestion row is created for this request below
        # regardless of whether any sources exist.
        source_ids = uow.sources.get_ids_by_project(project_id)

        modules = await self._repository.list_modules_by_project(project_id)
        fragments: list[Any] = []
        for source_id in source_ids:
            source = uow.sources.get_by_uuid(source_id)
            if source is None or source.is_deleted or source.project_id != project_id:
                continue
            fragments_response = await FragmentService().list_fragments(
                source_id=source_id, uow=uow
            )
            fragments.extend(fragments_response.fragments)

        from app.services.project_graph_service import ProjectGraphService

        project_metadata = ProjectGraphService().get_project_metadata(project_id=project_id)
        modules_and_features = self._serialize_modules_and_features(
            modules=modules,
            business_requirements=project_metadata["business_requirements"],
            exclusions=project_metadata["exclusions"],
        )

        source_id_strs = [str(sid) for sid in source_ids]

        from app.core.enums.source_ingestion_status import SourceIngestionStatus  # noqa: PLC0415
        from app.services.project_task_service import (  # noqa: PLC0415
            CreateTaskParams,
            ProjectTaskService,
        )

        regeneration_entity_json: dict[str, Any] = {"feedback": feedback}
        if module_ids:
            regeneration_entity_json["module_ids"] = module_ids
        if feature_ids:
            regeneration_entity_json["feature_ids"] = feature_ids

        # Every feedback-driven regeneration gets its own SourceIngestion row
        # (never tags/reuses whichever ingestion the project's sources already
        # happen to be linked to) so each request is independently trackable.
        # Starts `running`; the worker flips it to `ready_for_review`/`failed`
        # once regeneration finishes.
        now = datetime.now(UTC)
        ingestion = uow.source_ingestions.create_ingestion(
            project_id=project_id,
            source_type=SourceType.REQUIREMENT_UPDATE.value,
            status=SourceIngestionStatus.RUNNING.value,
            stages=[SourceIngestionStage.GENERATING_REQUIREMENTS.value],
            entity_json=regeneration_entity_json,
            started_at=now,
        )
        uow.commit()
        ingestion_id = str(ingestion.id)

        task_service = ProjectTaskService()
        task_id, task_db_id = task_service.create_task(
            project_id=project_id,
            user_id=user_id,
            params=CreateTaskParams(
                task_type="module_regeneration",
                status=SOURCE_STATUS_QUEUED,
                stage="modules_and_features.regeneration.queued",
                meta={"source_ids": source_id_strs},
            ),
        )

        async_result = regenerate_modules_and_features_task.apply_async(
            args=[
                str(project_id),
                [str(source_id) for source_id in source_ids],
                self._serialize_fragments(fragments),
                modules_and_features,
                feedback.strip(),
                task_db_id,
            ],
            kwargs={
                "ingestion_id": ingestion_id,
            },
        )

        task_service.set_celery_task_id(task_id, async_result.id)

        return ModuleFeatureRegenerationQueuedResponse(
            task_id=task_db_id,
            project_id=project_id,
            source_ids=source_ids,
            status=SOURCE_STATUS_QUEUED,
        )

    async def enqueue_feature_regeneration(
        self,
        *,
        project_id: UUID,
        feedback_items: list[FeatureFeedbackItem],
        skip_processing: bool = False,
        uow: UnitOfWork,
        user_id: UUID | None = None,
    ) -> FeatureRegenerationQueuedResponse:
        """Enqueue Stage 5 MFU regeneration for one or more source-code-derived features.

        Each feedback item carries its own ``mod_code``/``mfu_id`` — there is
        no single ``feature_id`` to resolve up front. Loads stored
        `SourceCodeMetadata` (specs + manifest generated during source-code
        ingestion) for every distinct ``mod_code`` referenced, then dispatches
        one Celery task that groups the feedback by ``(mod_code, mfu_id)`` and
        runs `PipelineOrchestrator.process_mfu_revise_with_reconstruction` once
        per group — the actual target Feature for each group is resolved by
        the worker from ``mod_code``/``mfu_id`` at persist time.

        This method publishes realtime WebSocket status updates with the
        lifecycle queued -> running -> completed/failed. These statuses are
        NOT persisted to any database — they exist only for realtime
        communication with frontend clients via WebSocket.

        Raises
        ------
        NotFoundError
            If the project or a referenced module's source-code metadata is not found.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        # ── Guard against regenerating while the source-code upload's
        # module/feature/user-story generation is still running (source
        # ingestion created by POST /api/v1/sources/upload/bulk). Source-code
        # projects generate everything in one automated pass, so a single
        # running check covers both stages.
        running_ingestions = uow.source_ingestions.list_running_by_project(
            project_id, source_types=[SourceType.SOURCE_CODE.value]
        )
        if running_ingestions:
            raise ConflictError(
                MSG_FEATURE_REGENERATION_SOURCE_PROCESSING.format(
                    ingestion_id=running_ingestions[0].id
                )
            )

        # Story-scoped feedback carries the pipeline ``user_story_code`` (e.g.
        # ``ANCES-001-F1-S2``) — the same id the reconstructed spec carries, so
        # the Stage 5 revise pipeline can match the target and apply the edit
        # (feature-wide feedback omits it). The worker maps this onto the
        # grouping service's ``user_story_id`` field.
        normalized_feedback_items = [
            {
                "mod_code": item.mod_code,
                "mfu_id": item.mfu_id,
                "user_story_code": item.user_story_code,
                "overall_feedback": item.overall_feedback,
                "specific_feedback": [
                    {"selected_text": sf.selected_text, "selected_feedback": sf.selected_feedback}
                    for sf in item.specific_feedback
                ],
            }
            for item in feedback_items
        ]

        # Distinct (mod_code, mfu_id) targets, in first-seen order — drives
        # both the response payload and per-module metadata loading below.
        seen_targets: set[tuple[str, str]] = set()
        targets: list[tuple[str, str]] = []
        for item in normalized_feedback_items:
            target = (item["mod_code"], item["mfu_id"])
            if target not in seen_targets:
                seen_targets.add(target)
                targets.append(target)

        from app.services.source_code_metadata_service import (
            SourceCodeMetadataService,  # noqa: PLC0415
        )

        # One SourceCodeMetadata fetch per distinct module — module_manifest and
        # markdown/config specs are stored per-module, not per-MFU, so a module
        # referenced by several MFUs in this request is only fetched once.
        module_metadata_by_code: dict[str, dict[str, Any]] = {}
        for mod_code in dict.fromkeys(module_id for module_id, _ in targets):
            metadata = await SourceCodeMetadataService().get_for_module(
                project_id=str(project_id), module_id=mod_code
            )
            if metadata is None:
                raise NotFoundError(
                    MSG_SOURCE_CODE_METADATA_NOT_FOUND.format(
                        module_id=mod_code, project_id=project_id
                    )
                )
            module_metadata_by_code[mod_code] = {
                "module_response": metadata.module_response,
                "module_manifest": metadata.module_manifest,
            }

        earliest_ingestion = uow.source_ingestions.get_earliest_by_project(project_id)
        source_paradigm = getattr(earliest_ingestion, "source_language", None) or "pb"

        from app.core.enums.source_ingestion_status import SourceIngestionStatus  # noqa: PLC0415

        target_dicts = [{"module_id": module_id, "mfu_id": mfu_id} for module_id, mfu_id in targets]

        # Every feedback-driven regeneration gets its own SourceIngestion row
        # (never reuses/tags an existing one) so each attempt is independently
        # trackable in the pipeline list. Starts `running`; the worker flips
        # it to `completed`/`failed` once the regeneration actually finishes.
        now = datetime.now(UTC)
        ingestion = uow.source_ingestions.create_ingestion(
            project_id=project_id,
            source_type=SourceType.REQUIREMENT_UPDATE.value,
            status=SourceIngestionStatus.RUNNING.value,
            stages=[SourceIngestionStage.GENERATING_REQUIREMENTS.value],
            entity_json={
                "feedback_items": normalized_feedback_items,
                "targets": target_dicts,
            },
            started_at=now,
        )
        uow.commit()
        ingestion_id = str(ingestion.id)

        from app.core.constants import SOURCE_INGESTION_STATUS_QUEUED  # noqa: PLC0415
        from app.services.project_task_service import (  # noqa: PLC0415
            CreateTaskParams,
            ProjectTaskService,
        )
        from app.workers.source_code_task import regenerate_feature_mfu_task  # noqa: PLC0415

        task_service = ProjectTaskService()
        task_id, task_db_id = task_service.create_task(
            project_id=project_id,
            user_id=user_id,
            params=CreateTaskParams(
                task_type="feature_regeneration",
                status=SOURCE_INGESTION_STATUS_QUEUED,
                stage="feature_regeneration.queued",
                meta={"targets": target_dicts},
            ),
        )

        async_result = regenerate_feature_mfu_task.apply_async(
            args=[
                str(project_id),
                normalized_feedback_items,
                module_metadata_by_code,
                source_paradigm,
                task_db_id,
                ingestion_id,
            ],
            kwargs={"skip_processing": skip_processing},
        )
        task_service.set_celery_task_id(task_id, async_result.id)

        return FeatureRegenerationQueuedResponse(
            task_id=task_db_id,
            project_id=project_id,
            targets=[
                FeatureRegenerationTarget(module_id=module_id, mfu_id=mfu_id)
                for module_id, mfu_id in targets
            ],
            status=SOURCE_INGESTION_STATUS_QUEUED,
        )

    @staticmethod
    def _build_regeneration_specs(
        module_response: Any,
        mfu_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
        """Extract this MFU's SRS spec files, config-naming-map, and features_stories from module_response.

        ``srs_files`` mirrors ``spec_generation.grouped_specs.markdown_specs``;
        ``config_naming_map`` is derived from the matching
        ``config_naming_map.json`` entry (if any) under
        ``spec_generation.grouped_specs.config_specs``; ``features_stories`` is
        the matching entry under ``feature_derivation.results`` — the shapes
        `PipelineOrchestrator.process_mfu_revise_with_reconstruction` expects.
        """
        module_response = module_response if isinstance(module_response, dict) else {}
        grouped_specs = (module_response.get("spec_generation") or {}).get("grouped_specs") or {}
        markdown_specs = grouped_specs.get("markdown_specs")
        config_specs = grouped_specs.get("config_specs")

        srs_files = [
            doc
            for doc in (markdown_specs if isinstance(markdown_specs, list) else [])
            if isinstance(doc, dict) and str(doc.get("feature_unit_id") or "") == mfu_id
        ]
        config_naming_map = ModuleFeatureService._extract_config_naming_map(config_specs, mfu_id)
        features_stories = ModuleFeatureService._extract_features_stories(module_response, mfu_id)

        return srs_files, config_naming_map, features_stories

    @staticmethod
    def _extract_features_stories(
        module_response: Any,
        mfu_id: str,
    ) -> dict[str, Any] | None:
        """Return the stored ``feature_derivation`` result for one MFU, if present.

        This is the same ``{"features": [...]}``-shaped dict that
        `_persist_feature_regeneration_result` reads back out of a completed
        regeneration — pre-seeding it lets revise start from the MFU's
        currently-stored feature/story content instead of an empty
        ``features_stories.json``.
        """
        results = ((module_response or {}).get("feature_derivation") or {}).get("results")
        if not isinstance(results, list):
            return None

        return next(
            (
                result
                for result in results
                if isinstance(result, dict) and str(result.get("mfu_id") or "") == mfu_id
            ),
            None,
        )

    @staticmethod
    def _extract_config_naming_map(
        config_specs: Any,
        mfu_id: str,
    ) -> dict[str, Any] | None:
        """Return the parsed ``config_naming_map.json`` content for one MFU, if present."""
        candidates = config_specs if isinstance(config_specs, list) else []
        doc = next(
            (
                doc
                for doc in candidates
                if isinstance(doc, dict)
                and str(doc.get("feature_unit_id") or "") == mfu_id
                and str(doc.get("filename") or "").strip().lower() == "config_naming_map.json"
            ),
            None,
        )
        if doc is None:
            return None

        content = doc.get("content")
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except ValueError:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    @staticmethod
    def _to_feature_response(
        feature: FeatureModel,
        *,
        last_previous_items: FeatureVersionResponse | None = None,
        source_files: list[FeatureSourceFileResponse] | None = None,
    ) -> FeatureResponse:
        response = FeatureResponse(
            id=feature.id,
            name=feature.name,
            description=feature.description,
            fea_code=ModuleFeatureService._to_optional_str(feature.fea_code),
            mfu_id=feature.mfu_id,
            version=feature.version,
            status=feature.status,
            total_user_stories=feature.total_user_stories,
            generation_metadata=feature.generation_metadata,
            incremental_change_type=feature.incremental_change_type,
            feedback_change_type=feature.feedback_change_type,
            is_infrastructure=feature.is_infrastructure,
            condensation_note=feature.condensation_note,
            is_jira_synced=feature.is_jira_synced,
            is_tap_synced=feature.is_tap_synced,
            functions=[
                FunctionResponse(
                    fun_code=fn.fun_code,
                    name=fn.name,
                    description=fn.description,
                    func_src_ref=fn.func_src_ref,
                )
                for fn in feature.functions
            ],
            sources=feature.sources,
            source_files=source_files or [],
            l2_sources=feature.l2_sources,
            text_diffs=feature.text_diffs,
            rfp_flagged_item=feature.rfp_flagged_item,
            created_at=feature.created_at,
            updated_at=feature.updated_at,
            deleted_at=feature.deleted_at,
            last_previous_items=last_previous_items,
        )
        response.updated_fields = ModuleFeatureService._compute_updated_fields(
            response, last_previous_items
        )
        return response

    @staticmethod
    def _compute_updated_fields(
        current: FeatureResponse, previous: FeatureVersionResponse | None
    ) -> list[str]:
        """Field names whose value differs between the current feature and its last snapshot."""
        if previous is None:
            return []
        return [
            field
            for field in _FEATURE_UPDATED_FIELDS_CANDIDATES
            if getattr(current, field) != getattr(previous, field)
        ]

    @staticmethod
    def _to_feature_version_response(
        version: FeatureVersionModel | None,
    ) -> FeatureVersionResponse | None:
        if version is None:
            return None
        return FeatureVersionResponse(
            id=version.id,
            feature_id=version.feature_id,
            module_id=version.module_id,
            project_id=version.project_id,
            fea_code=version.fea_code,
            mfu_id=version.mfu_id,
            name=version.name,
            description=version.description,
            version=version.version,
            status=version.status,
            functions=[
                FunctionResponse(
                    fun_code=fn.fun_code,
                    name=fn.name,
                    description=fn.description,
                    func_src_ref=fn.func_src_ref,
                )
                for fn in version.functions
            ],
            sources=version.sources,
            l2_sources=version.l2_sources,
            justification=version.justification,
            incremental_change_type=version.incremental_change_type,
            feedback_change_type=version.feedback_change_type,
            is_infrastructure=version.is_infrastructure,
            condensation_note=version.condensation_note,
            is_jira_synced=version.is_jira_synced,
            is_tap_synced=version.is_tap_synced,
            created_at=version.created_at,
            updated_at=version.updated_at,
            snapshotted_at=version.snapshotted_at,
        )

    @staticmethod
    def _to_response(module: ModuleModel) -> ModuleFeatureResponse:
        return ModuleFeatureResponse(
            id=module.id,
            project_id=module.project_id,
            mod_code=ModuleFeatureService._to_optional_str(module.mod_code),
            name=module.name,
            description=module.description,
            version=module.version,
            status=module.status,
            is_jira_synced=module.is_jira_synced,
            is_tap_synced=module.is_tap_synced,
            text_diffs=module.text_diffs,
            rfp_flagged_item=module.rfp_flagged_item,
            features=[
                ModuleFeatureService._to_feature_response(feature) for feature in module.features
            ],
            created_at=module.created_at,
            updated_at=module.updated_at,
            deleted_at=module.deleted_at,
        )

    @staticmethod
    def _to_detail_response(
        module: ModuleModel,
        *,
        last_previous_items: ModuleVersionResponse | None = None,
    ) -> ModuleDetailResponse:
        response = ModuleDetailResponse(
            id=module.id,
            project_id=module.project_id,
            mod_code=ModuleFeatureService._to_optional_str(module.mod_code),
            name=module.name,
            description=module.description,
            version=module.version,
            status=module.status,
            incremental_change_type=module.incremental_change_type,
            feedback_change_type=module.feedback_change_type,
            is_jira_synced=module.is_jira_synced,
            is_tap_synced=module.is_tap_synced,
            text_diffs=module.text_diffs,
            rfp_flagged_item=module.rfp_flagged_item,
            created_at=module.created_at,
            updated_at=module.updated_at,
            deleted_at=module.deleted_at,
            last_previous_items=last_previous_items,
        )
        response.updated_fields = ModuleFeatureService._compute_module_updated_fields(
            response, last_previous_items
        )
        return response

    @staticmethod
    def _compute_module_updated_fields(
        current: ModuleDetailResponse, previous: ModuleVersionResponse | None
    ) -> list[str]:
        """Field names whose value differs between the current module and its last snapshot."""
        if previous is None:
            return []
        return [
            field
            for field in _MODULE_UPDATED_FIELDS_CANDIDATES
            if getattr(current, field) != getattr(previous, field)
        ]

    @staticmethod
    def _to_module_version_response(
        version: ModuleVersionModel | None,
    ) -> ModuleVersionResponse | None:
        if version is None:
            return None
        return ModuleVersionResponse(
            id=version.id,
            module_id=version.module_id,
            project_id=version.project_id,
            mod_code=version.mod_code,
            name=version.name,
            description=version.description,
            version=version.version,
            status=version.status,
            justification=version.justification,
            incremental_change_type=version.incremental_change_type,
            feedback_change_type=version.feedback_change_type,
            is_jira_synced=version.is_jira_synced,
            is_tap_synced=version.is_tap_synced,
            created_at=version.created_at,
            updated_at=version.updated_at,
            snapshotted_at=version.snapshotted_at,
        )

    @staticmethod
    def _to_list_item_response(module: ModuleModel) -> ModuleFeatureListItemResponse:
        features = [
            ModuleFeatureService._to_feature_response(feature) for feature in module.features
        ]
        return ModuleFeatureListItemResponse(
            id=module.id,
            name=module.name,
            description=module.description,
            version=module.version,
            status=module.status,
            is_jira_synced=module.is_jira_synced,
            is_tap_synced=module.is_tap_synced,
            mod_code=ModuleFeatureService._to_optional_str(module.mod_code),
            total_features=len(module.features),
            total_user_stories=sum(f.total_user_stories for f in module.features),
            rfp_flagged_item=module.rfp_flagged_item,
            features=features,
            created_at=module.created_at,
            updated_at=module.updated_at,
            deleted_at=module.deleted_at,
        )

    async def list_modules_for_project(
        self,
        *,
        project_id: UUID,
        uow: UnitOfWork,
        skip: int = 0,
        limit: int = 20,
    ) -> ModuleFeatureListResponse:
        """Return paginated modules with full feature details for a project."""
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        modules = await self._repository.list_modules_by_project(project_id)
        total = len(modules)
        paged_modules = modules[skip : skip + limit]

        return ModuleFeatureListResponse(
            total=total,
            skip=skip,
            limit=limit,
            items=[self._to_list_item_response(module) for module in paged_modules],
        )

    async def list_modules_tree_for_project(
        self,
        *,
        project_id: UUID,
        uow: UnitOfWork,
        source_ingestion_id: str | None = None,
    ) -> ModuleTreeListResponse:
        """Return all modules with features and functions as a nested tree.

        ``source_ingestion_id``, when provided, restricts the result to
        modules whose own ``source_ingestion_id`` matches — features/functions
        under a matching module are always returned in full, unfiltered.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        modules = await self._repository.list_modules_by_project(
            project_id, source_ingestion_id=source_ingestion_id
        )
        items = [
            ModuleTreeNode(
                id=module.id,
                mod_code=self._to_optional_str(module.mod_code),
                name=module.name,
                description=module.description,
                children=[
                    FeatureTreeNode(
                        id=feature.id,
                        fea_code=self._to_optional_str(feature.fea_code),
                        mfu_id=feature.mfu_id,
                        name=feature.name,
                        description=feature.description,
                        children=[
                            FunctionTreeNode(
                                fun_code=fn.fun_code,
                                name=fn.name,
                                description=fn.description,
                                func_src_ref=fn.func_src_ref,
                            )
                            for fn in feature.functions
                        ],
                        created_at=feature.created_at,
                        updated_at=feature.updated_at,
                    )
                    for feature in module.features
                ],
            )
            for module in modules
        ]
        return ModuleTreeListResponse(total=len(items), items=items)

    async def change_module_feature_status_for_project(
        self,
        *,
        project_id: UUID,
        payload: ModuleFeatureStatusChangeRequest,
        uow: UnitOfWork,
        user_id: UUID | None = None,
    ) -> ModuleFeatureStatusChangeResponse:
        """Change the status of a module and all its child features.

        Updates the Module node's status and all child Feature nodes' status
        to the provided new_status value within a project context. Both module
        and feature nodes will be updated atomically in Neo4j.

        When status is set to "approved", this triggers user story regeneration
        which publishes realtime WebSocket status updates (queued → running →
        completed/failed). These realtime statuses are NOT persisted to any database;
        they exist only for WebSocket communication with frontend clients.

        Parameters
        ----------
        project_id : UUID
            UUID of the project containing the modules.
        payload : ModuleFeatureStatusChangeRequest
            Request payload containing new status.
        uow : UnitOfWork
            Unit of work for database operations.

        Returns
        -------
        ModuleFeatureStatusChangeResponse
            Operation result indicating whether status change succeeded.

        Raises
        ------
        NotFoundError
            If the project is not found in the database.
        NotFoundError
            If no sources are found for the project when approving (user story
            regeneration requires at least one uploaded source document).
        NotFoundError
            If no module exists in the specified project.

        Example
        -------
        >>> request = ModuleFeatureStatusChangeRequest(
        ...     status=ModuleFeatureStatusEnum.APPROVED
        ... )
        >>> response = await service.change_module_feature_status_for_project(
        ...     project_id=UUID('550e8400-e29b-41d4-a716-446655440000'),
        ...     payload=request,
        ...     uow=uow
        ... )
        >>> print(response.task_id)   # e.g. "celery-task-uuid"
        >>> print(response.status)    # "queued"
        """
        # Verify project exists
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        from app.services.source_ingestion_service import SourceIngestionService  # noqa: PLC0415

        SourceIngestionService.raise_if_pipeline_running(uow, project_id)

        # Pre-validate before any writes: approving triggers user story regeneration
        # which requires at least one source.  Checking here prevents a state where
        # modules are updated to "approved" in Neo4j but the subsequent enqueue step
        # fails, leaving the graph in an inconsistent state.
        if payload.status == ModuleFeatureStatusEnum.APPROVED:
            source_ids = uow.sources.get_ids_by_project(project_id)
            if not source_ids:
                raise NotFoundError(MSG_PROJECT_SOURCES_NOT_FOUND.format(project_id=project_id))

            # A module/feature still flagged ADDED/UPDATED/DELETE_SUGGESTED by a
            # feedback-driven regeneration is awaiting human accept/reject — approving
            # (and generating user stories) now would build on top of unreviewed content.
            pending_feedback_count = await self._repository.count_pending_feedback_changes_by_project(
                project_id
            )
            if pending_feedback_count > 0:
                raise ConflictError(MSG_MODULE_FEATURE_PENDING_FEEDBACK_CHANGE_APPROVAL_BLOCKED)

        # Update module and features status in repository
        updated_module = await self._repository.change_module_feature_status_for_project(
            project_id=project_id,
            payload=payload,
        )

        if updated_module is None:
            raise NotFoundError(f"No modules found for project {project_id}.")

        if payload.status == ModuleFeatureStatusEnum.APPROVED:
            total_modules, total_features = (
                await self._repository.count_modules_and_features_for_project(project_id)
            )
            self._notify_module_feature_approved(
                project=project,
                project_id=project_id,
                total_modules=total_modules,
                total_features=total_features,
                actor_user_id=user_id,
                uow=uow,
            )

        if payload.status == ModuleFeatureStatusEnum.APPROVED and not payload.is_incremental:
            from app.services.user_story_service import UserStoryService

            enqueue_kwargs: dict[str, Any] = {
                "project_id": project_id,
                "uow": uow,
                "user_id": user_id,
            }
            if payload.skip_processing:
                enqueue_kwargs["skip_processing"] = True

            queued = await UserStoryService().enqueue_user_story_generation(**enqueue_kwargs)
            return ModuleFeatureStatusChangeResponse(
                project_id=project_id,
                task_id=queued.task_id,
                status=queued.status,
            )

        return ModuleFeatureStatusChangeResponse(
            project_id=project_id,
            status=payload.status.value,
        )

    @staticmethod
    def _notify_module_feature_approved(
        *,
        project: Any,
        project_id: UUID,
        total_modules: int,
        total_features: int,
        actor_user_id: UUID | None,
        uow: UnitOfWork,
    ) -> None:
        """Record an activity-log entry and notify the project owner and every
        assigned member that a module/feature approval completed.

        Never raises — a notification/activity-log failure must not fail an
        already-successful status change.
        """
        from app.core.enums.notification_type import NotificationType  # noqa: PLC0415
        from app.services.activity_log_service import record_activity  # noqa: PLC0415
        from app.services.notification_service import publish_notification  # noqa: PLC0415

        record_activity(
            project_id=project_id,
            activity_type=ActivityType.MODULE_FEATURE_APPROVED,
            summary=SUMMARY_ACTIVITY_MODULE_FEATURE_APPROVED,
            message=MSG_ACTIVITY_MODULE_FEATURE_APPROVED.format(
                total_modules=total_modules, total_features=total_features
            ),
            actor_user_id=actor_user_id,
            data={"total_modules": total_modules, "total_features": total_features},
        )

        recipient_ids = {
            member.user_id for member in uow.project_members.list_by_project(project_id)
        }
        if project.owner_id is not None:
            recipient_ids.add(project.owner_id)

        message = (
            f"{total_modules} module(s) and {total_features} feature(s) approved "
            f'in "{project.name}".'
        )
        for recipient_id in recipient_ids:
            try:
                publish_notification(
                    user_id=recipient_id,
                    title=SUMMARY_ACTIVITY_MODULE_FEATURE_APPROVED,
                    message=message,
                    notification_type=NotificationType.SUCCESS,
                    data={
                        "project_id": str(project_id),
                        "total_modules": total_modules,
                        "total_features": total_features,
                    },
                )
            except Exception:
                logger.warning(
                    "_notify_module_feature_approved: failed to notify "
                    "project_id=%s user_id=%s",
                    project_id,
                    recipient_id,
                    exc_info=True,
                )

    @staticmethod
    def _read_attr(item: Any, *names: str, default: Any = None) -> Any:
        for name in names:
            if isinstance(item, dict) and name in item:
                return item[name]
            if hasattr(item, name):
                return getattr(item, name)
        return default

    @staticmethod
    def _to_optional_str(value: Any) -> str | None:
        if value is None:
            return None
        return str(value)

    @classmethod
    def _normalize_feature_sources(cls, raw_sources: Any) -> list[dict[str, Any]]:
        parsed_sources = cls._parse_raw_sources(raw_sources)
        normalized_sources: list[dict[str, Any]] = []
        for raw_source in parsed_sources:
            normalized_source = cls._normalize_single_source(raw_source)
            if normalized_source is not None:
                normalized_sources.append(normalized_source)
        return normalized_sources

    @classmethod
    def _parse_raw_sources(cls, raw_sources: Any) -> list[Any]:
        if raw_sources is None:
            return []
        if isinstance(raw_sources, str):
            try:
                raw_sources = json.loads(raw_sources)
            except (json.JSONDecodeError, TypeError):
                return []
        if isinstance(raw_sources, list):
            return raw_sources
        return []

    @classmethod
    def _normalize_single_source(cls, raw_source: Any) -> dict[str, Any] | None:
        source_id = cls._read_attr(raw_source, "source_id")
        if source_id in (None, ""):
            return None

        pages_payload = cls._read_attr(raw_source, "pages", default=[])
        pages = [
            normalized_page
            for raw_page in (pages_payload or [])
            if (normalized_page := cls._normalize_single_page(raw_page)) is not None
        ]
        return {
            "source_id": str(source_id),
            "pages": pages,
        }

    @classmethod
    def _normalize_single_page(cls, raw_page: Any) -> dict[str, Any] | None:
        page_value = cls._read_attr(raw_page, "page")
        if page_value is None:
            return None

        try:
            page_number = int(page_value)
        except (TypeError, ValueError):
            return None

        bboxes_payload = cls._read_attr(raw_page, "bboxes", default=[])
        bboxes = [
            normalized_bbox
            for raw_bbox in (bboxes_payload or [])
            if (normalized_bbox := cls._normalize_single_bbox(raw_bbox)) is not None
        ]
        return {"page": page_number, "bboxes": bboxes}

    @classmethod
    def _normalize_single_bbox(cls, raw_bbox: Any) -> dict[str, Any] | None:
        fragment_id = cls._read_attr(raw_bbox, "fragment_id")
        if fragment_id in (None, ""):
            return None

        bbox_payload = cls._read_attr(raw_bbox, "bbox", default={})
        x = cls._read_attr(bbox_payload, "x")
        y = cls._read_attr(bbox_payload, "y")
        w = cls._read_attr(bbox_payload, "w")
        h = cls._read_attr(bbox_payload, "h")
        if any(value is None for value in (x, y, w, h)):
            return None

        try:
            bbox = {
                "x": float(x),
                "y": float(y),
                "w": float(w),
                "h": float(h),
            }
        except (TypeError, ValueError):
            return None

        return {
            "fragment_id": str(fragment_id),
            "bbox": bbox,
        }

    @classmethod
    def _extract_skeleton_modules(cls, skeleton: Any) -> list[Any]:
        payload = skeleton
        if isinstance(payload, dict) and "output" in payload:
            payload = payload["output"]
        elif hasattr(payload, "output"):
            payload = payload.output

        if isinstance(payload, str):
            from app.schemas.rfp_pipeline_v2_graph_schema import ModuleFeatureOutput

            payload = ModuleFeatureOutput.model_validate_json(payload)

        if isinstance(payload, dict) and "feature_inventory" in payload:
            return payload["feature_inventory"]
        if hasattr(payload, "feature_inventory"):
            return list(payload.feature_inventory)
        if isinstance(payload, dict) and "modules" in payload:
            return payload["modules"]
        if hasattr(payload, "modules"):
            return list(payload.modules)
        return []

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
                                    "fun_code": function.fun_code,
                                    "name": function.name,
                                    "description": function.description or "",
                                    "func_src_ref": function.func_src_ref,
                                }
                                for function in feature.functions
                            ],
                            "sources": ModuleFeatureService._normalize_feature_sources(
                                feature.sources
                            ),
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
