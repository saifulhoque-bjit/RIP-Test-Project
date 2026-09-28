"""Service that merges the live backlog tree with the latest pending incremental proposal.

Builds a single Module -> Feature -> UserStory tree from the current Neo4j
graph state, then overlays the most recent ``incremental_histories`` row's
adds/updates/deletes on top of it — annotating each affected node with
``changed`` / ``changed_action`` / ``proposed_items`` so a human reviewer can
see current vs. proposed state in one response, without diffing two separate
payloads themselves.

Hierarchy traversal rule (mirrors the LLM prompt contract used by
IncrementalUpdateProcessorService):
  - A node with ``changed: false`` in the proposal is a wrapper that exists
    only to carry parent linkage down to the actual changed descendant.
  - A node with ``changed: true`` in ``adds``/``updates`` is the real target
    of the operation.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.core.messages import (
    MSG_PROJECT_NOT_FOUND,
    MSG_UPDATE_ENTITY_NOT_FOUND,
)
from app.db.unit_of_work import UnitOfWork
from app.models.neo4j.module_feature_model import ChangeType
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.schemas.incremental_updates_schema import (
    ChangedAction,
    FeatureProposedItems,
    IncrementalFeatureNode,
    IncrementalModuleNode,
    IncrementalUpdatesListResponse,
    IncrementalUpdatesTreeResponse,
    IncrementalUserStoryNode,
    ModuleProposedItems,
    UpdateAcceptRequest,
    UpdateChangeType,
    UpdateDecisionResponse,
    UpdateEntityType,
    UpdateRejectRequest,
    UserStoryProposedItems,
)
from app.schemas.user_story_schema import UserStoryStatus
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _numeric_code_key(code: str | None) -> tuple[int, str]:
    """Sort key that orders codes numerically by trailing integer; codeless (new) items sort last."""
    if not code:
        return (2**31, "")
    nums = re.findall(r"\d+", code)
    return (int(nums[-1]), code) if nums else (2**31, code)


class IncrementalUpdatesService:
    """Builds the merged incremental-review tree for a project."""

    def __init__(
        self,
        user_story_repo: UserStoryRepository | None = None,
        module_feature_repo: ModuleFeatureRepository | None = None,
    ) -> None:
        self._us_repo = user_story_repo or UserStoryRepository()
        self._mf_repo = module_feature_repo or ModuleFeatureRepository()

    async def get_latest_incremental_tree(
        self,
        *,
        project_id: UUID,
        uow: UnitOfWork,
    ) -> IncrementalUpdatesTreeResponse:
        """Return the live backlog tree, pruned down to what the latest pending
        incremental proposal actually touched (``changed: true`` in
        ``adds_json``, ``updates_json``, or ``delete_json``).

        Pruning is bottom-up: a user story survives only if it changed; a
        feature survives only if it changed itself or still has a surviving
        story; a module survives only if it changed itself or still has a
        surviving feature. Returns an empty tree when no incremental run has
        been recorded for this project yet, since nothing is pending review.

        Raises:
            NotFoundError: If the project does not exist.
        """
        if uow.projects.get_by_uuid(project_id) is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        base_tree = await self._us_repo.list_full_backlog_tree_for_project(project_id=project_id)
        modules = [self._build_base_module(m) for m in base_tree]

        history_rows = uow.incremental_histories.list_by_project(project_id, limit=1)
        history = history_rows[0] if history_rows else None

        if history is not None:
            self._apply_updates(modules, history.updates_json or [])
            self._apply_adds(modules, history.adds_json or [])
            self._apply_deletes(modules, history.delete_json or [])

        self._filter_unchanged_stories(modules)
        self._prune_empty_unchanged_features(modules)
        modules = self._prune_empty_unchanged_modules(modules)
        self._sort_tree(modules)

        return IncrementalUpdatesTreeResponse(
            history_id=history.id if history is not None else None,
            generated_at=history.created_at if history is not None else None,
            items=modules,
        )

    async def get_incremental_updates_list(
        self,
        *,
        project_id: UUID,
        uow: UnitOfWork,
    ) -> IncrementalUpdatesListResponse:
        """Return the module → feature → user story tree pruned to nodes flagged
        by an incremental update (``incremental_change_type is not None``).

        Pruning is bottom-up, mirroring ``get_latest_incremental_tree``: a user
        story survives only if its own ``incremental_change_type`` is set; a
        feature survives if its own ``incremental_change_type`` is set OR it has
        a surviving story; a module survives if its own ``incremental_change_type``
        is set OR it has a surviving feature — so parent linkage down to a
        changed descendant is never lost, even when the parent itself is
        unchanged.

        Raises:
            NotFoundError: If the project does not exist.
        """
        if uow.projects.get_by_uuid(project_id) is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        tree = await self._us_repo.list_user_stories_tree_for_project(project_id=project_id)
        counts = self._empty_change_type_counts()
        filtered_modules = [
            filtered
            for module in tree
            if (filtered := self._filter_module(module, counts)) is not None
        ]

        return IncrementalUpdatesListResponse(items=filtered_modules, **counts)

    @staticmethod
    def _empty_change_type_counts() -> dict[str, int]:
        return {
            "module_added": 0,
            "module_updated": 0,
            "module_deleted_suggested": 0,
            "feature_added": 0,
            "feature_updated": 0,
            "feature_deleted_suggested": 0,
            "user_story_added": 0,
            "user_story_updated": 0,
            "user_story_deleted_suggested": 0,
        }

    @staticmethod
    def _tally_change_type(counts: dict[str, int], prefix: str, change_type: str | None) -> None:
        """Bump ``{prefix}_added``/``_updated``/``_deleted_suggested`` for a non-null change type."""
        if change_type == ChangeType.ADDED.value:
            counts[f"{prefix}_added"] += 1
        elif change_type == ChangeType.UPDATED.value:
            counts[f"{prefix}_updated"] += 1
        elif change_type == ChangeType.DELETE_SUGGESTED.value:
            counts[f"{prefix}_deleted_suggested"] += 1

    @classmethod
    def _filter_story(cls, story: dict[str, Any], counts: dict[str, int]) -> dict[str, Any] | None:
        """Return the story if its own ``incremental_change_type`` is set, tallying it; else None."""
        story_type = story.get("incremental_change_type")
        if story_type is None:
            return None
        cls._tally_change_type(counts, "user_story", story_type)
        return story

    @classmethod
    def _filter_feature(
        cls, feature: dict[str, Any], counts: dict[str, int]
    ) -> dict[str, Any] | None:
        """Return a pruned feature (with only changed stories) if it or any story
        survives, tallying the feature itself if changed; else None."""
        filtered_stories = [
            filtered
            for story in feature.get("children", [])
            if (filtered := cls._filter_story(story, counts)) is not None
        ]
        feature_type = feature.get("incremental_change_type")
        feature_changed = feature_type is not None
        if feature_changed:
            cls._tally_change_type(counts, "feature", feature_type)
        if not (feature_changed or filtered_stories):
            return None
        return {**feature, "children": filtered_stories}

    @classmethod
    def _filter_module(
        cls, module: dict[str, Any], counts: dict[str, int]
    ) -> dict[str, Any] | None:
        """Return a pruned module (with only surviving features) if it or any
        feature survives, tallying the module itself if changed; else None."""
        filtered_features = [
            filtered
            for feature in module.get("children", [])
            if (filtered := cls._filter_feature(feature, counts)) is not None
        ]
        module_type = module.get("incremental_change_type")
        module_changed = module_type is not None
        if module_changed:
            cls._tally_change_type(counts, "module", module_type)
        if not (module_changed or filtered_features):
            return None
        return {**module, "children": filtered_features}

    # ── Accept / reject a pending incremental change ────────────────────────

    async def accept_update(
        self,
        *,
        project_id: UUID,
        payload: UpdateAcceptRequest,
        actor_user_id: UUID | None,
        uow: UnitOfWork,
    ) -> UpdateDecisionResponse:
        """Accept a pending incremental change for a single entity.

        ADDED/UPDATED: status becomes ``approved`` and ``text_diffs``/
        ``incremental_change_type`` are cleared. DELETE_SUGGESTED user stories
        are marked deleted and remain eligible for synchronization.

        Raises:
            NotFoundError: If the project or the target entity does not exist.
        """
        if uow.projects.get_by_uuid(project_id) is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        from app.services.source_ingestion_service import SourceIngestionService  # noqa: PLC0415

        SourceIngestionService.raise_if_pipeline_running(uow, project_id)

        source_ingestion_id = await self._resolve_source_ingestion_id(
            project_id, payload.entity_type, payload.entity_id
        )

        if payload.change_type == UpdateChangeType.DELETE_SUGGESTED:
            if payload.entity_type == UpdateEntityType.USER_STORY:
                found = await self._us_repo.soft_delete_user_story_by_id(
                    project_id=project_id,
                    user_story_id=payload.entity_id,
                    del_reason="",
                )
            else:
                found = await self._delete_entity(project_id, payload.entity_type, payload.entity_id)
            deleted = found
        else:
            found = await self._accept_entity(project_id, payload.entity_type, payload.entity_id)
            deleted = False

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

        return UpdateDecisionResponse(
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            change_type=payload.change_type,
            action="accepted",
            deleted=deleted,
            comment=payload.comment,
        )

    async def reject_update(
        self,
        *,
        project_id: UUID,
        payload: UpdateRejectRequest,
        actor_user_id: UUID | None,
        uow: UnitOfWork,
    ) -> UpdateDecisionResponse:
        """Reject a pending incremental change for a single entity.

        UPDATED and DELETE_SUGGESTED: both are rolled back the same way —
        content (and the ``version`` counter, which both flows bump) is
        restored from the most recent Module/Feature/UserStory version
        snapshot taken just before the change was applied, and
        ``text_diffs``/``incremental_change_type`` are cleared. The consumed
        snapshot node is deleted as part of the restore. ADDED: the node is
        hard-deleted, since a brand-new entity has no prior state to roll
        back to.

        Raises:
            NotFoundError: If the project or the target entity does not exist.
        """
        if uow.projects.get_by_uuid(project_id) is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        from app.services.source_ingestion_service import SourceIngestionService  # noqa: PLC0415

        SourceIngestionService.raise_if_pipeline_running(uow, project_id)

        source_ingestion_id = await self._resolve_source_ingestion_id(
            project_id, payload.entity_type, payload.entity_id
        )

        if payload.change_type == UpdateChangeType.ADDED:
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

        return UpdateDecisionResponse(
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            change_type=payload.change_type,
            action="rejected",
            deleted=deleted,
            reason=payload.reason,
        )

    async def _resolve_source_ingestion_id(
        self, project_id: UUID, entity_type: UpdateEntityType, entity_id: str
    ) -> str | None:
        """Look up the entity's tagged ``source_ingestion_id`` before it's mutated.

        Must run before the accept/reject write, since a DELETE_SUGGESTED
        accept or an ADDED reject hard-deletes the node.
        """
        if entity_type == UpdateEntityType.USER_STORY:
            return await self._us_repo.get_source_ingestion_id(entity_id)
        return await self._mf_repo.get_source_ingestion_id(project_id, entity_type.value, entity_id)

    @staticmethod
    def _bump_review_count(
        uow: UnitOfWork,
        source_ingestion_id: str | None,
        entity_type: UpdateEntityType,
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
                "[INCREMENTAL_UPDATE] no source_ingestion_id tagged on %s; skipping review count "
                "(accepted=%s)",
                entity_type.value,
                accepted,
            )
            return
        uow.source_ingestions.increment_review_count(
            UUID(source_ingestion_id), entity_type=entity_type.value, accepted=accepted
        )

    async def _delete_entity(
        self, project_id: UUID, entity_type: UpdateEntityType, entity_id: str
    ) -> bool:
        if entity_type == UpdateEntityType.MODULE:
            return await self._mf_repo.delete_module_by_id(project_id, entity_id)
        if entity_type == UpdateEntityType.FEATURE:
            return await self._mf_repo.delete_feature_by_id(project_id, entity_id)
        return await self._us_repo.delete_user_story_by_id(
            project_id=project_id, user_story_id=entity_id
        )

    async def _accept_entity(
        self, project_id: UUID, entity_type: UpdateEntityType, entity_id: str
    ) -> bool:
        if entity_type == UpdateEntityType.MODULE:
            return await self._mf_repo.accept_module(project_id, entity_id)
        if entity_type == UpdateEntityType.FEATURE:
            return await self._mf_repo.accept_feature(project_id, entity_id)
        return await self._us_repo.accept_user_story(entity_id, UserStoryStatus.APPROVED.value)

    async def _restore_entity(
        self, project_id: UUID, entity_type: UpdateEntityType, entity_id: str
    ) -> bool:
        if entity_type == UpdateEntityType.MODULE:
            return await self._mf_repo.restore_module_from_latest_version(project_id, entity_id)
        if entity_type == UpdateEntityType.FEATURE:
            return await self._mf_repo.restore_feature_from_latest_version(project_id, entity_id)
        return await self._us_repo.restore_user_story_from_latest_version(entity_id)

    # ── Base tree construction ──────────────────────────────────────────────

    @staticmethod
    def _build_base_module(module: dict[str, Any]) -> IncrementalModuleNode:
        return IncrementalModuleNode(
            id=module["id"],
            mod_code=module.get("mod_code"),
            name=module.get("name"),
            description=module.get("description"),
            children=[
                IncrementalUpdatesService._build_base_feature(f) for f in module.get("children", [])
            ],
        )

    @staticmethod
    def _build_base_feature(feature: dict[str, Any]) -> IncrementalFeatureNode:
        return IncrementalFeatureNode(
            id=feature["id"],
            fea_code=feature.get("fea_code"),
            name=feature.get("name"),
            description=feature.get("description"),
            functions=feature.get("functions") or [],
            sources=feature.get("sources") or [],
            children=[
                IncrementalUpdatesService._build_base_story(s) for s in feature.get("children", [])
            ],
        )

    @staticmethod
    def _build_base_story(story: dict[str, Any]) -> IncrementalUserStoryNode:
        return IncrementalUserStoryNode(
            id=story["id"],
            user_story_code=story.get("user_story_code"),
            title=story.get("title"),
            status=story.get("status"),
            as_a=story.get("as_a"),
            i_want_to=story.get("i_want_to"),
            so_that=story.get("so_that"),
            acceptance_criteria=story.get("acceptance_criteria") or [],
            nfrs=story.get("nfrs") or [],
            story_points=story.get("story_points"),
            technical_notes=story.get("technical_notes"),
            sources=story.get("sources") or [],
        )

    # ── New-node construction (for `adds`) ──────────────────────────────────

    @staticmethod
    def _build_new_story(story_data: dict[str, Any]) -> IncrementalUserStoryNode:
        return IncrementalUserStoryNode(
            id=story_data.get("user_story_id"),
            user_story_code=story_data.get("user_story_code"),
            title=story_data.get("title"),
            as_a=story_data.get("as_a"),
            i_want_to=story_data.get("i_want_to"),
            so_that=story_data.get("so_that"),
            acceptance_criteria=story_data.get("acceptance_criteria") or [],
            nfrs=story_data.get("nfrs") or [],
            story_points=story_data.get("story_points"),
            technical_notes=story_data.get("technical_notes"),
            sources=story_data.get("sources") or [],
            changed=True,
            changed_action=ChangedAction.CREATE,
            justification=story_data.get("justification"),
        )

    @staticmethod
    def _build_new_feature(feature_data: dict[str, Any]) -> IncrementalFeatureNode:
        return IncrementalFeatureNode(
            id=feature_data.get("feature_id"),
            fea_code=feature_data.get("feature_code"),
            name=feature_data.get("feature_name"),
            description=feature_data.get("feature_description"),
            functions=feature_data.get("functions") or [],
            sources=feature_data.get("sources") or [],
            changed=True,
            changed_action=ChangedAction.CREATE,
            justification=feature_data.get("justification"),
            children=[
                IncrementalUpdatesService._build_new_story(s)
                for s in feature_data.get("user_stories", [])
            ],
        )

    @staticmethod
    def _build_new_module(module_data: dict[str, Any]) -> IncrementalModuleNode:
        return IncrementalModuleNode(
            id=module_data.get("module_id"),
            mod_code=module_data.get("module_code"),
            name=module_data.get("module_name"),
            description=module_data.get("module_description"),
            changed=True,
            changed_action=ChangedAction.CREATE,
            justification=module_data.get("justification"),
            children=[
                IncrementalUpdatesService._build_new_feature(f)
                for f in module_data.get("features", [])
            ],
        )

    # ── Overlay: updates ─────────────────────────────────────────────────────

    def _apply_updates(
        self,
        modules: list[IncrementalModuleNode],
        updates: list[dict[str, Any]],
    ) -> None:
        modules_by_id = {m.id: m for m in modules}
        for module_data in updates:
            module = modules_by_id.get(module_data.get("module_id"))
            if module is None:
                logger.warning(
                    "[INCREMENTAL_TREE] update wrapper not found: module_id=%s",
                    module_data.get("module_id"),
                )
                continue
            self._apply_update_to_module(module, module_data)

    def _apply_update_to_module(
        self,
        module: IncrementalModuleNode,
        module_data: dict[str, Any],
    ) -> None:
        if module_data.get("changed"):
            module.changed = True
            module.changed_action = ChangedAction.UPDATE
            module.justification = module_data.get("justification")
            module.proposed_items = ModuleProposedItems(
                mod_code=module_data.get("module_code"),
                name=module_data.get("module_name"),
                description=module_data.get("module_description"),
            )

        features_by_id = {f.id: f for f in module.children}
        for feature_data in module_data.get("features", []):
            feature = features_by_id.get(feature_data.get("feature_id"))
            if feature is None:
                logger.warning(
                    "[INCREMENTAL_TREE] update wrapper not found: feature_id=%s",
                    feature_data.get("feature_id"),
                )
                continue
            self._apply_update_to_feature(feature, feature_data)

    def _apply_update_to_feature(
        self,
        feature: IncrementalFeatureNode,
        feature_data: dict[str, Any],
    ) -> None:
        if feature_data.get("changed"):
            feature.changed = True
            feature.changed_action = ChangedAction.UPDATE
            feature.justification = feature_data.get("justification")
            feature.proposed_items = FeatureProposedItems(
                fea_code=feature_data.get("feature_code"),
                name=feature_data.get("feature_name"),
                description=feature_data.get("feature_description"),
                functions=feature_data.get("functions"),
                sources=feature_data.get("sources"),
            )

        stories_by_id = {s.id: s for s in feature.children}
        for story_data in feature_data.get("user_stories", []):
            story = stories_by_id.get(story_data.get("user_story_id"))
            if story is None:
                logger.warning(
                    "[INCREMENTAL_TREE] update target not found: user_story_id=%s",
                    story_data.get("user_story_id"),
                )
                continue
            self._apply_update_to_story(story, story_data)

    @staticmethod
    def _apply_update_to_story(
        story: IncrementalUserStoryNode,
        story_data: dict[str, Any],
    ) -> None:
        story.changed = True
        story.changed_action = ChangedAction.UPDATE
        story.justification = story_data.get("justification")
        story.proposed_items = UserStoryProposedItems(
            user_story_code=story_data.get("user_story_code"),
            title=story_data.get("title"),
            as_a=story_data.get("as_a"),
            i_want_to=story_data.get("i_want_to"),
            so_that=story_data.get("so_that"),
            acceptance_criteria=story_data.get("acceptance_criteria") or [],
            nfrs=story_data.get("nfrs") or [],
            story_points=story_data.get("story_points"),
            technical_notes=story_data.get("technical_notes"),
            sources=story_data.get("sources"),
        )

    # ── Overlay: adds ────────────────────────────────────────────────────────

    def _apply_adds(
        self,
        modules: list[IncrementalModuleNode],
        adds: list[dict[str, Any]],
    ) -> None:
        modules_by_id = {m.id: m for m in modules}
        for module_data in adds:
            if module_data.get("changed"):
                modules.append(self._build_new_module(module_data))
                continue

            module = modules_by_id.get(module_data.get("module_id"))
            if module is None:
                logger.warning(
                    "[INCREMENTAL_TREE] add wrapper not found: module_id=%s",
                    module_data.get("module_id"),
                )
                continue
            self._apply_add_to_module(module, module_data)

    def _apply_add_to_module(
        self,
        module: IncrementalModuleNode,
        module_data: dict[str, Any],
    ) -> None:
        features_by_id = {f.id: f for f in module.children}
        for feature_data in module_data.get("features", []):
            if feature_data.get("changed"):
                module.children.append(self._build_new_feature(feature_data))
                continue

            feature = features_by_id.get(feature_data.get("feature_id"))
            if feature is None:
                logger.warning(
                    "[INCREMENTAL_TREE] add wrapper not found: feature_id=%s",
                    feature_data.get("feature_id"),
                )
                continue
            self._apply_add_to_feature(feature, feature_data)

    def _apply_add_to_feature(
        self,
        feature: IncrementalFeatureNode,
        feature_data: dict[str, Any],
    ) -> None:
        for story_data in feature_data.get("user_stories", []):
            if story_data.get("changed"):
                feature.children.append(self._build_new_story(story_data))

    # ── Overlay: deletes ─────────────────────────────────────────────────────

    def _apply_deletes(
        self,
        modules: list[IncrementalModuleNode],
        deletes: list[dict[str, Any]],
    ) -> None:
        for entry in deletes:
            node = self._find_node(modules, entry.get("uuid"), entry.get("type"))
            if node is None:
                logger.warning(
                    "[INCREMENTAL_TREE] delete target not found: uuid=%s type=%s",
                    entry.get("uuid"),
                    entry.get("type"),
                )
                continue
            node.changed = True
            node.changed_action = ChangedAction.DELETE
            node.justification = entry.get("justification")
            node.proposed_items = None

    @staticmethod
    def _find_node(
        modules: list[IncrementalModuleNode],
        target_id: str | None,
        target_type: str | None,
    ) -> IncrementalModuleNode | IncrementalFeatureNode | IncrementalUserStoryNode | None:
        if not target_id:
            return None
        if target_type == "module":
            return next((m for m in modules if m.id == target_id), None)
        if target_type == "feature":
            return next((f for m in modules for f in m.children if f.id == target_id), None)
        if target_type == "user_story":
            return next(
                (s for m in modules for f in m.children for s in f.children if s.id == target_id),
                None,
            )
        return None

    # ── Filtering ────────────────────────────────────────────────────────────

    @staticmethod
    def _filter_unchanged_stories(modules: list[IncrementalModuleNode]) -> None:
        """Drop user stories that the incremental proposal didn't touch."""
        for module in modules:
            for feature in module.children:
                feature.children = [story for story in feature.children if story.changed]

    @staticmethod
    def _prune_empty_unchanged_features(modules: list[IncrementalModuleNode]) -> None:
        """Drop features that are themselves unchanged and have no surviving stories."""
        for module in modules:
            module.children = [
                feature for feature in module.children if feature.changed or feature.children
            ]

    @staticmethod
    def _prune_empty_unchanged_modules(
        modules: list[IncrementalModuleNode],
    ) -> list[IncrementalModuleNode]:
        """Drop modules that are themselves unchanged and have no surviving features."""
        return [module for module in modules if module.changed or module.children]

    # ── Final ordering ───────────────────────────────────────────────────────

    @staticmethod
    def _sort_tree(modules: list[IncrementalModuleNode]) -> None:
        for module in modules:
            for feature in module.children:
                feature.children.sort(key=lambda s: _numeric_code_key(s.user_story_code))
            module.children.sort(key=lambda f: _numeric_code_key(f.fea_code))
        modules.sort(key=lambda m: _numeric_code_key(m.mod_code))
