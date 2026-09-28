"""Service that applies the four operation keys from an incremental update result.

After the AI pipeline produces a structured JSON payload with four top-level
keys (``updates``, ``adds``, ``deletes``, ``flags``), this service translates
each key into the corresponding persistence operations:

  updates → update module / feature / user story nodes in Neo4j
  adds    → create module / feature / user story nodes in Neo4j
  deletes → persist the delete manifest in PostgreSQL (incremental_histories)
  flags   → persist the flag manifest in PostgreSQL (incremental_histories)

Each operation is intentionally isolated so a failure in one does not abort
the others.  Callers control transactionality at the UnitOfWork level.

Hierarchy traversal rule (from the LLM prompt contract):
  - ``changed: false`` with non-empty children → recurse into children only,
    do NOT write this node.
  - ``changed: true`` with children → write this node, THEN recurse.
  - ``changed: true`` with no children → write this node and stop.
  - ``changed: false`` with no children → stop (nothing to do).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.core.constants import INITIAL_ENTITY_VERSION
from app.db.unit_of_work import UnitOfWork
from app.models.neo4j.module_feature_model import (
    ChangeType,
    FeatureModel,
    FunctionModel,
    ModuleFeatureStatus,
    ModuleModel,
)
from app.models.neo4j.user_story_model import UserStoryModel
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.schemas.user_story_schema import UserStoryStatus
from app.utils.logger import get_logger

logger = get_logger(__name__)


class IncrementalUpdateProcessorService:
    """Applies incremental AI output to Neo4j and PostgreSQL stores."""

    def __init__(
        self,
        module_feature_repo: ModuleFeatureRepository | None = None,
        user_story_repo: UserStoryRepository | None = None,
    ) -> None:
        self._mf_repo = module_feature_repo or ModuleFeatureRepository()
        self._us_repo = user_story_repo or UserStoryRepository()

    # ── Public interface ───────────────────────────────────────────────────

    async def handle_updates(
        self,
        project_id: UUID,
        updates: list[dict[str, Any]],
        rfp_flag_map: dict[str, dict[str, Any]] | None = None,
        source_ingestion_id: str | None = None,
    ) -> None:
        """Apply update operations: traverse the hierarchy and update changed nodes.

        For each module in ``updates``:
          - If ``changed`` is True, update the module node in Neo4j.
          - Recurse into features; if a feature's ``changed`` is True, update it.
          - Recurse into user_stories; if a story's ``changed`` is True, update it.

        ``rfp_flag_map`` (see ``extract_rfp_flag_map``) maps a module's/
        feature's/user story's per-pass ``item_code`` to the quality-gate flag
        raised against it, when this run failed — matched nodes are persisted
        as FAILED with ``rfp_flagged_item`` set instead of READY.

        ``source_ingestion_id``, when provided, is stamped onto every updated
        node so incremental-ingestion completion tracking (see
        ``SourceIngestionService``) can find it later.
        """
        if not updates:
            return

        logger.info("[INCREMENTAL] handle_updates: project=%s modules=%d", project_id, len(updates))

        for module_data in updates:
            await self._update_module(project_id, module_data, rfp_flag_map, source_ingestion_id)
            for feature_data in module_data.get("features", []):
                feature_id = self._resolve_feature_id(feature_data)
                await self._update_feature(
                    project_id, feature_data, rfp_flag_map, source_ingestion_id
                )
                for story_data in feature_data.get("user_stories", []):
                    await self._update_user_story(
                        project_id, feature_id, story_data, rfp_flag_map, source_ingestion_id
                    )

    async def handle_adds(
        self,
        project_id: UUID,
        adds: list[dict[str, Any]],
        rfp_flag_map: dict[str, dict[str, Any]] | None = None,
        source_ingestion_id: str | None = None,
    ) -> None:
        """Apply add operations: traverse the hierarchy and create changed nodes.

        For each module in ``adds``:
          - If ``changed`` is True, create the module node in Neo4j.
          - Recurse into features; if a feature's ``changed`` is True, create it.
          - Recurse into user_stories; if a story's ``changed`` is True, create it.

        ``rfp_flag_map`` — see ``handle_updates``. ``source_ingestion_id`` —
        see ``handle_updates``; stamped onto every newly created node.
        """
        if not adds:
            return

        logger.info("[INCREMENTAL] handle_adds: project=%s modules=%d", project_id, len(adds))

        for module_data in adds:
            module_id = await self._create_module(
                project_id, module_data, rfp_flag_map, source_ingestion_id
            )
            for feature_data in module_data.get("features", []):
                feature_id = await self._create_feature(
                    project_id, module_id, feature_data, rfp_flag_map, source_ingestion_id
                )
                for story_data in feature_data.get("user_stories", []):
                    await self._create_user_story(
                        project_id, feature_id, story_data, rfp_flag_map, source_ingestion_id
                    )

    async def handle_deletes(
        self,
        project_id: UUID,
        deletes: list[dict[str, Any]],
        source_ingestion_id: str | None = None,
    ) -> None:
        """Flag module/feature/user_story nodes as DELETE_SUGGESTED.

        ``deletes`` is the flat list produced by the AI pipeline; each entry
        carries ``uuid`` (the node id) and ``type`` (module / feature / user_story).
        Nodes are flagged in place — nothing is removed from the graph.

        Before flagging, the current node state is snapshotted into a
        ``*Version`` node — same as ``handle_updates`` — so accepting the
        suggested delete later still leaves a version history behind, and
        rejecting it can restore from that snapshot.

        ``source_ingestion_id``, when provided, is stamped onto every flagged
        node — see ``handle_updates``.
        """
        if not deletes:
            return

        logger.info("[INCREMENTAL] handle_deletes: project=%s entries=%d", project_id, len(deletes))

        for entry in deletes:
            entry_id = entry.get("uuid") or ""
            entry_type = entry.get("type", "")
            justification = entry.get("justification")
            if not entry_id:
                logger.warning(
                    "[INCREMENTAL] handle_deletes skipped — no uuid in entry: project=%s type=%s",
                    project_id,
                    entry_type,
                )
                continue
            try:
                if entry_type == "module":
                    await self._snapshot_before_delete(
                        self._mf_repo.snapshot_module_version, project_id, entry_id
                    )
                    marked = await self._mf_repo.mark_module_delete_suggested(
                        project_id=project_id,
                        module_id=entry_id,
                        justification=justification,
                        source_ingestion_id=source_ingestion_id,
                    )
                elif entry_type == "feature":
                    await self._snapshot_before_delete(
                        self._mf_repo.snapshot_feature_version, project_id, entry_id
                    )
                    marked = await self._mf_repo.mark_feature_delete_suggested(
                        project_id=project_id,
                        feature_id=entry_id,
                        justification=justification,
                        source_ingestion_id=source_ingestion_id,
                    )
                elif entry_type == "user_story":
                    await self._snapshot_before_delete(
                        self._us_repo.snapshot_user_story_version, entry_id
                    )
                    marked = await self._us_repo.mark_user_story_delete_suggested(
                        user_story_id=entry_id,
                        justification=justification,
                        source_ingestion_id=source_ingestion_id,
                    )
                else:
                    logger.warning(
                        "[INCREMENTAL] handle_deletes skipped — unknown type=%s uuid=%s project=%s",
                        entry_type,
                        entry_id,
                        project_id,
                    )
                    continue
                if not marked:
                    logger.warning(
                        "[INCREMENTAL] handle_deletes: no node matched type=%s uuid=%s project=%s",
                        entry_type,
                        entry_id,
                        project_id,
                    )
            except Exception as exc:
                logger.error(
                    "[INCREMENTAL] handle_deletes failed: type=%s uuid=%s project=%s error=%s",
                    entry_type,
                    entry_id,
                    project_id,
                    exc,
                    exc_info=True,
                )

    async def handle_history(
        self,
        project_id: UUID,
        uow: UnitOfWork,
        *,
        updates: list[dict[str, Any]] | None = None,
        adds: list[dict[str, Any]] | None = None,
        deletes: list[dict[str, Any]] | None = None,
        flags: list[dict[str, Any]] | None = None,
        persona_glossary_additions: list[dict[str, Any]] | None = None,
        meeting_summary: dict[str, Any] | None = None,
    ) -> None:
        """Persist all incremental output fields in a single ``incremental_histories`` row.

        All parameters are optional — pass only the keys that were present in
        the AI payload.  A row is always written (even when every list is empty)
        to provide a full audit trail for every run.
        """
        logger.info(
            "[INCREMENTAL] handle_history: project=%s updates=%d adds=%d deletes=%d "
            "flags=%d persona_glossary=%d meeting_summary=%s",
            project_id,
            len(updates or []),
            len(adds or []),
            len(deletes or []),
            len(flags or []),
            len(persona_glossary_additions or []),
            bool(meeting_summary),
        )
        uow.incremental_histories.create(
            project_id=project_id,
            updates_json=updates or None,
            adds_json=adds or None,
            delete_json=deletes or None,
            flag_json=flags or None,
            persona_glossary_additions_json=persona_glossary_additions or None,
            meeting_summary_json=meeting_summary or None,
        )

    @staticmethod
    async def _snapshot_before_delete(snapshot_fn: Any, *args: Any) -> None:
        """Best-effort version snapshot before flagging a node DELETE_SUGGESTED.

        Mirrors the update flow's snapshot-then-write pattern: a snapshot
        failure is logged but never blocks the delete suggestion itself.
        """
        try:
            await snapshot_fn(*args)
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] snapshot before delete-suggest failed: args=%s error=%s",
                args,
                exc,
                exc_info=True,
            )

    # ── Update helpers ─────────────────────────────────────────────────────

    async def _update_module(
        self,
        project_id: UUID,
        module_data: dict[str, Any],
        rfp_flag_map: dict[str, dict[str, Any]] | None = None,
        source_ingestion_id: str | None = None,
    ) -> None:
        if not module_data.get("changed"):
            return
        module_id = module_data.get("module_id") or ""
        if not module_id:
            logger.warning(
                "[INCREMENTAL] update_module skipped — no module_id in payload: project=%s",
                project_id,
            )
            return
        mod_code = module_data.get("module_code", "")
        name = module_data.get("module_name", "")
        description = module_data.get("module_description")
        justification = module_data.get("justification")
        try:
            await self._mf_repo.snapshot_module_version(project_id, module_id)
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] snapshot_module_version failed: module_id=%s project=%s error=%s",
                module_id,
                project_id,
                exc,
                exc_info=True,
            )
        # The LLM emits text_diffs already keyed by field name (see
        # ModuleTextDiffs in rfp_pipeline_v2_graph_schema.py) — no bucketing
        # needed, just pass it straight through.
        text_diffs_json = json.dumps(module_data.get("text_diffs") or {})
        flagged = self._resolve_flag(module_data, rfp_flag_map, "module")
        status = ModuleFeatureStatus.FAILED.value if flagged else ModuleFeatureStatus.READY.value
        rfp_flagged_item_json = json.dumps(flagged) if flagged else None
        try:
            updated = await self._mf_repo.update_module(
                project_id=project_id,
                module_id=module_id,
                mod_code=mod_code,
                name=name,
                description=description,
                incremental_change_type=ChangeType.UPDATED,
                justification=justification,
                text_diffs_json=text_diffs_json,
                status=status,
                rfp_flagged_item_json=rfp_flagged_item_json,
                source_ingestion_id=source_ingestion_id,
            )
            if not updated:
                logger.warning(
                    "[INCREMENTAL] update_module: no node matched module_id=%s project=%s",
                    module_id,
                    project_id,
                )
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] update_module failed: module_id=%s project=%s error=%s",
                module_id,
                project_id,
                exc,
                exc_info=True,
            )

    async def _update_feature(
        self,
        project_id: UUID,
        feature_data: dict[str, Any],
        rfp_flag_map: dict[str, dict[str, Any]] | None = None,
        source_ingestion_id: str | None = None,
    ) -> None:
        if not feature_data.get("changed"):
            return
        feature_id = feature_data.get("feature_id") or ""
        if not feature_id:
            logger.warning(
                "[INCREMENTAL] update_feature skipped — no feature_id in payload: project=%s",
                project_id,
            )
            return
        fea_code = feature_data.get("feature_code", "")
        name = feature_data.get("feature_name", "")
        description = feature_data.get("feature_description")
        justification = feature_data.get("justification")
        functions_json = json.dumps(feature_data.get("functions", []))
        sources_json = json.dumps(feature_data.get("sources", []))
        try:
            await self._mf_repo.snapshot_feature_version(project_id, feature_id)
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] snapshot_feature_version failed: feature_id=%s project=%s error=%s",
                feature_id,
                project_id,
                exc,
                exc_info=True,
            )
        # The LLM emits text_diffs already keyed by field name, with
        # `functions` keyed by fun_code (see FeatureTextDiffs in
        # rfp_pipeline_v2_graph_schema.py) — no bucketing needed.
        text_diffs_json = json.dumps(feature_data.get("text_diffs") or {})
        flagged = self._resolve_flag(feature_data, rfp_flag_map, "feature")
        status = ModuleFeatureStatus.FAILED.value if flagged else ModuleFeatureStatus.READY.value
        rfp_flagged_item_json = json.dumps(flagged) if flagged else None
        try:
            updated = await self._mf_repo.update_feature(
                project_id=project_id,
                feature_id=feature_id,
                fea_code=fea_code,
                name=name,
                description=description,
                functions_json=functions_json,
                sources_json=sources_json,
                incremental_change_type=ChangeType.UPDATED,
                justification=justification,
                text_diffs_json=text_diffs_json,
                status=status,
                rfp_flagged_item_json=rfp_flagged_item_json,
                source_ingestion_id=source_ingestion_id,
            )
            if not updated:
                logger.warning(
                    "[INCREMENTAL] update_feature: no node matched feature_id=%s project=%s",
                    feature_id,
                    project_id,
                )
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] update_feature failed: feature_id=%s project=%s error=%s",
                feature_id,
                project_id,
                exc,
                exc_info=True,
            )

    async def _update_user_story(
        self,
        project_id: UUID,
        feature_id: str | None,
        story_data: dict[str, Any],
        rfp_flag_map: dict[str, dict[str, Any]] | None = None,
        source_ingestion_id: str | None = None,
    ) -> None:
        if not story_data.get("changed"):
            return
        story_id = story_data.get("user_story_id") or ""
        if not story_id:
            logger.warning(
                "[INCREMENTAL] update_user_story skipped — no user_story_id in payload: project=%s",
                project_id,
            )
            return
        try:
            await self._us_repo.snapshot_user_story_version(story_id)
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] snapshot_user_story_version failed: story_id=%s project=%s error=%s",
                story_id,
                project_id,
                exc,
                exc_info=True,
            )
        model = await self._build_user_story_model_for_update(
            story_data, feature_id, project_id, rfp_flag_map, source_ingestion_id
        )
        try:
            await self._us_repo.bulk_upsert_user_stories_for_project(
                project_id=project_id,
                user_stories=[model],
            )
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] update_user_story failed: story_id=%s project=%s error=%s",
                story_id,
                project_id,
                exc,
                exc_info=True,
            )
        # bulk_upsert's coalesce() preserves an existing node's status/
        # rfp_flagged_item (so a human's approved/needs_edit review state
        # survives regeneration) — hard-set both to this run's verdict here,
        # same as the pre-existing unconditional READY reset, now extended to
        # also apply/clear the flag.
        try:
            await self._us_repo.set_user_story_status_and_flag(
                story_id, status=model.status, rfp_flagged_item=model.rfp_flagged_item
            )
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] update_user_story status/flag reset failed: story_id=%s project=%s error=%s",
                story_id,
                project_id,
                exc,
                exc_info=True,
            )
        # Content changed under this incremental run — a previously-synced
        # Jira/TAP copy is now stale, so the sync flags must not survive the
        # update (mirrors the source-code feature-regeneration flow's reset
        # in app/workers/source_code_task.py's _apply_feature_regeneration).
        try:
            await self._us_repo.update_user_story_sync_flags(
                story_id, is_jira_synced=False, is_tap_synced=False
            )
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] update_user_story sync-flag reset failed: story_id=%s project=%s error=%s",
                story_id,
                project_id,
                exc,
                exc_info=True,
            )

    # ── Create helpers ─────────────────────────────────────────────────────

    async def _create_module(
        self,
        project_id: UUID,
        module_data: dict[str, Any],
        rfp_flag_map: dict[str, dict[str, Any]] | None = None,
        source_ingestion_id: str | None = None,
    ) -> str:
        """Create the module node if changed; return the module node id."""
        if not module_data.get("changed"):
            # Wrapper node — module already exists; read its id from the payload
            # so child features can link to the correct Neo4j node.
            return module_data.get("module_id") or str(uuid4())

        # Auto-generate the next mod_code from the current max stored in Neo4j.
        max_code = await self._mf_repo.get_max_mod_code(project_id)
        mod_code = str(max_code + 1)

        module_id = module_data.get("module_id") or str(uuid4())
        name = module_data.get("module_name", "")
        description = module_data.get("module_description")
        flagged = self._resolve_flag(module_data, rfp_flag_map, "module")
        module_model = ModuleModel(
            id=module_id,
            project_id=project_id,
            mod_code=mod_code,
            name=name,
            description=description,
            features=[],
            status=ModuleFeatureStatus.FAILED if flagged else ModuleFeatureStatus.READY,
            justification=module_data.get("justification"),
            incremental_change_type=ChangeType.ADDED,
            rfp_flagged_item=flagged,
            source_ingestion_id=source_ingestion_id,
        )
        try:
            returned = await self._mf_repo.create_module(project_id=project_id, module=module_model)
            # Use the id that Neo4j actually stored — MERGE may have matched an
            # existing node (same project_id + mod_code), in which case the
            # returned id is the pre-existing one, not the locally generated one.
            module_id = returned.id
            logger.info(
                "[INCREMENTAL] create_module: mod_code=%s module_id=%s project=%s",
                mod_code,
                module_id,
                project_id,
            )
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] create_module failed: mod_code=%s project=%s error=%s",
                mod_code,
                project_id,
                exc,
                exc_info=True,
            )
        return module_id

    async def _create_feature(
        self,
        project_id: UUID,
        module_id: str,
        feature_data: dict[str, Any],
        rfp_flag_map: dict[str, dict[str, Any]] | None = None,
        source_ingestion_id: str | None = None,
    ) -> str:
        """Create the feature node if changed; return the feature node id."""
        feature_id = feature_data.get("feature_id") or str(uuid4())

        if not feature_data.get("changed"):
            # Wrapper — return existing id for child user story linking.
            return feature_id

        # Auto-generate the next fea_code from the current max stored in Neo4j.
        mod_code, max_suffix = await self._mf_repo.get_max_fea_code(project_id, module_id)
        if not mod_code:
            logger.warning(
                "[INCREMENTAL] _create_feature: skipping — module_id=%s not found in project=%s",
                module_id,
                project_id,
            )
            return feature_id
        fea_code = f"{mod_code}.{max_suffix + 1}"

        name = feature_data.get("feature_name", "")
        description = feature_data.get("feature_description")
        functions = [
            FunctionModel(
                fun_code=fn.get("fun_code"),
                name=fn.get("name", ""),
                description=fn.get("description"),
            )
            for fn in feature_data.get("functions", [])
        ]
        flagged = self._resolve_flag(feature_data, rfp_flag_map, "feature")
        feature_model = FeatureModel(
            id=feature_id,
            project_id=project_id,
            module_id=module_id,
            fea_code=fea_code,
            name=name,
            description=description,
            functions=functions,
            sources=feature_data.get("sources", []),
            status=ModuleFeatureStatus.FAILED if flagged else ModuleFeatureStatus.READY,
            justification=feature_data.get("justification"),
            incremental_change_type=ChangeType.ADDED,
            mfu_id=feature_data.get("mfu_id"),
            rfp_flagged_item=flagged,
            source_ingestion_id=source_ingestion_id,
        )
        try:
            returned = await self._mf_repo.create_feature(
                project_id=project_id,
                module_id=module_id,
                feature=feature_model,
            )
            # Use the id that Neo4j actually stored — MERGE may have matched an
            # existing node, in which case the returned id is the pre-existing one.
            feature_id = returned.id
            logger.info(
                "[INCREMENTAL] create_feature: fea_code=%s feature_id=%s project=%s",
                fea_code,
                feature_id,
                project_id,
            )
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] create_feature failed: fea_code=%s project=%s error=%s",
                fea_code,
                project_id,
                exc,
                exc_info=True,
            )
        return feature_id

    async def _create_user_story(
        self,
        project_id: UUID,
        feature_id: str | None,
        story_data: dict[str, Any],
        rfp_flag_map: dict[str, dict[str, Any]] | None = None,
        source_ingestion_id: str | None = None,
    ) -> None:
        if not story_data.get("changed"):
            return
        model = await self._build_user_story_model_for_create(
            story_data, feature_id, project_id, rfp_flag_map, source_ingestion_id
        )
        try:
            await self._us_repo.bulk_upsert_user_stories_for_project(
                project_id=project_id,
                user_stories=[model],
            )
            logger.info(
                "[INCREMENTAL] create_user_story: story_code=%s project=%s",
                model.user_story_code,
                project_id,
            )
        except Exception as exc:
            logger.error(
                "[INCREMENTAL] create_user_story failed: story_code=%s project=%s error=%s",
                story_data.get("user_story_code"),
                project_id,
                exc,
                exc_info=True,
            )

    # ── Model builders ──────────────────────────────────────────────────────

    async def _build_user_story_model_for_create(
        self,
        story_data: dict[str, Any],
        feature_id: str | None,
        project_id: UUID,
        rfp_flag_map: dict[str, dict[str, Any]] | None = None,
        source_ingestion_id: str | None = None,
    ) -> UserStoryModel:
        """Map an ``adds`` story dict → ``UserStoryModel`` for a brand-new UserStory node.

        Auto-generates the next ``user_story_code`` (and the node's id derived
        from it) from the current max stored in Neo4j, instead of trusting the
        AI-supplied code. Always starts at version 1.
        """
        user_story_code = story_data.get("user_story_code", "")
        if feature_id:
            fea_code, max_suffix = await self._us_repo.get_max_user_story_code(
                project_id, feature_id
            )
            if fea_code:
                user_story_code = f"U.S {fea_code}.{max_suffix + 1}"
        story_id = self._generate_user_story_id(
            project_id=str(project_id),
            user_story_code=user_story_code,
        )

        incoming_nfrs = story_data.get("nfrs")
        if incoming_nfrs:
            from app.services.user_story_service import UserStoryService  # noqa: PLC0415

            nfrs = UserStoryService._normalize_story_nfrs(incoming_nfrs)
        else:
            nfrs = []

        rfp_flagged_item = (rfp_flag_map or {}).get(story_data.get("item_code") or "")

        return UserStoryModel(
            id=story_id,
            user_story_code=user_story_code,
            title=story_data.get("title", ""),
            description=story_data.get("description"),
            consensus=float(story_data.get("consensus") or 1.0),
            status=UserStoryStatus.FAILED.value
            if rfp_flagged_item
            else UserStoryStatus.READY.value,
            version=INITIAL_ENTITY_VERSION,
            feature_id=feature_id,
            project_id=project_id,
            as_a=story_data.get("as_a"),
            i_want_to=story_data.get("i_want_to"),
            so_that=story_data.get("so_that"),
            acceptance_criteria=story_data.get("acceptance_criteria", []),
            nfrs=nfrs,
            technical_notes=story_data.get("technical_notes"),
            story_points=story_data.get("story_points"),
            justification=story_data.get("justification"),
            incremental_change_type=ChangeType.ADDED,
            sources=story_data.get("sources", []),
            # text_diffs is an updates-only concept — a brand-new story has no
            # prior version to diff against.
            text_diffs={},
            rfp_flagged_item=rfp_flagged_item,
            source_ingestion_id=source_ingestion_id,
        )

    async def _build_user_story_model_for_update(
        self,
        story_data: dict[str, Any],
        feature_id: str | None,
        project_id: UUID,
        rfp_flag_map: dict[str, dict[str, Any]] | None = None,
        source_ingestion_id: str | None = None,
    ) -> UserStoryModel:
        """Map an ``updates`` story dict → ``UserStoryModel`` for an existing UserStory node.

        Fetches the current version from Neo4j by ``user_story_id`` and
        increments it by 1. Falls back to version 1 if the fetch fails.
        """
        story_id = story_data.get("user_story_id") or ""
        user_story_code = story_data.get("user_story_code", "")
        existing = None
        try:
            existing = await self._us_repo.get_user_story_detail_for_project(
                project_id=project_id,
                user_story_id=story_id,
            )
        except Exception as exc:
            logger.warning(
                "[INCREMENTAL] could not fetch existing story for story_id=%s project=%s error=%s",
                story_id,
                project_id,
                exc,
            )

        # Fetched by id alone (not the project-scoped traversal above) so a
        # not-yet-established relationship link can't cause this to miss an
        # existing story and silently reset its version to 1.
        next_version = INITIAL_ENTITY_VERSION
        try:
            current_version = await self._us_repo.get_user_story_version_by_id(story_id)
            if current_version is not None:
                next_version = current_version + 1
        except Exception as exc:
            logger.warning(
                "[INCREMENTAL] could not fetch current version for story_id=%s project=%s error=%s",
                story_id,
                project_id,
                exc,
            )

        # Use LLM-supplied NFRs when the payload provides them (explicit NFR
        # change). Fall back to the stored value so existing NFRs are not
        # silently wiped on every story update.
        incoming_nfrs = story_data.get("nfrs")
        if incoming_nfrs:
            from app.services.user_story_service import UserStoryService  # noqa: PLC0415

            nfrs = UserStoryService._normalize_story_nfrs(incoming_nfrs)
        else:
            nfrs = (existing.nfrs if existing else None) or []

        rfp_flagged_item = (rfp_flag_map or {}).get(story_data.get("item_code") or "")

        return UserStoryModel(
            id=story_id,
            user_story_code=user_story_code,
            title=story_data.get("title", ""),
            description=story_data.get("description"),
            consensus=float(story_data.get("consensus") or 1.0),
            status=UserStoryStatus.FAILED.value
            if rfp_flagged_item
            else UserStoryStatus.READY.value,
            version=next_version,
            feature_id=feature_id,
            project_id=project_id,
            as_a=story_data.get("as_a"),
            i_want_to=story_data.get("i_want_to"),
            so_that=story_data.get("so_that"),
            acceptance_criteria=story_data.get("acceptance_criteria", []),
            nfrs=nfrs,
            technical_notes=story_data.get("technical_notes"),
            story_points=story_data.get("story_points"),
            justification=story_data.get("justification"),
            incremental_change_type=ChangeType.UPDATED,
            sources=story_data.get("sources", []),
            # The LLM emits text_diffs already keyed by field name, with
            # acceptance_criteria keyed by ac_code (see UserStoryTextDiffs in
            # rfp_pipeline_v2_graph_schema.py) — no bucketing needed.
            text_diffs=story_data.get("text_diffs") or {},
            rfp_flagged_item=rfp_flagged_item,
            source_ingestion_id=source_ingestion_id,
        )

    @staticmethod
    def _resolve_feature_id(feature_data: dict[str, Any]) -> str | None:
        """Return the feature's stable id for child user story linking."""
        return feature_data.get("feature_id") or None

    @staticmethod
    def _resolve_flag(
        item_data: dict[str, Any],
        rfp_flag_map: dict[str, dict[str, Any]] | None,
        entity_type: str,
    ) -> dict[str, Any] | None:
        """Return the flagged-item dict for this node's item_code, if it matches entity_type.

        Guards against an (unexpected) item_code collision across module/
        feature/user_story — a flag raised against a feature must never get
        applied to a module or story that happens to share the same per-pass
        tracking id.
        """
        flagged = (rfp_flag_map or {}).get(item_data.get("item_code") or "")
        if flagged and flagged.get("entity_type") == entity_type:
            return flagged
        return None

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
    def extract_rfp_flag_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Map a module's/feature's/user story's per-pass ``item_code`` ->
        flagged-item dict, for nodes the incremental quality gate rejected.

        Mirrors ``UserStoryService._extract_rfp_flag_map`` for the RFP
        generation flow, adapted for incremental update: a node is only ever
        known by its generator-assigned ``item_code`` (e.g. "U3") within a
        single pass — a SUBSET-mode add's real ``user_story_code``/
        ``user_story_id``/``feature_id``/``module_id`` is still null at that
        point — so ``flagged_items`` entries key off ``item_code``, not the
        node's real identifier. The map is keyed by ``item_code`` alone (not
        also entity_type) since a per-pass tracking id is assigned uniquely
        across the whole payload; callers still double-check
        ``entity_type`` before applying a match (see ``_resolve_flag``) as a
        defensive guard against an unexpected collision.

        Only populated when the run itself failed — top-level ``status`` or
        ``generation_metadata.final_status`` contains ``"FAIL"`` (e.g.
        ``"FAIL_CORRECTION_EXHAUSTED"``). A clean run returns an empty map,
        leaving every node at the default READY status.
        """
        top_status = str(result.get("status") or "").upper()
        generation_metadata = result.get("generation_metadata") or {}
        final_status = str(generation_metadata.get("final_status") or "").upper()
        if "FAIL" not in top_status and "FAIL" not in final_status:
            return {}

        flag_map: dict[str, dict[str, Any]] = {}
        for item in generation_metadata.get("flagged_items") or []:
            if not isinstance(item, dict) or item.get("entity_type") not in (
                "module",
                "feature",
                "user_story",
            ):
                continue
            entity_id = item.get("entity_id")
            if not entity_id:
                continue
            flag_map[str(entity_id)] = {
                "entity_id": str(entity_id),
                "entity_type": str(item.get("entity_type") or ""),
                "issue": str(item.get("user_summary") or ""),
                "suggested_fix": "",
            }
        return flag_map
