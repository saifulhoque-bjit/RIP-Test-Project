"""Service for Jira sync orchestration: preview, execute, and history."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from uuid import UUID

from app.clients.jira_client import JiraCloudClient
from app.core.config import settings
from app.core.enums.activity_type import ActivityType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.messages import (
    MSG_ACTIVITY_JIRA_SYNC_COMPLETED,
    MSG_ACTIVITY_JIRA_SYNC_FAILED,
    MSG_ACTIVITY_JIRA_SYNC_STARTED,
    MSG_JIRA_INTEGRATION_NOT_FOUND,
    MSG_JIRA_SYNC_NOTHING_TO_SYNC,
    SUMMARY_ACTIVITY_JIRA_SYNC_COMPLETED,
    SUMMARY_ACTIVITY_JIRA_SYNC_FAILED,
    SUMMARY_ACTIVITY_JIRA_SYNC_STARTED,
)
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.jira_sync_history_model import JiraSyncHistory
from app.models.postgres.jira_sync_mapping_model import JiraSyncMapping
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.schemas.jira_integration_schema import (
    JiraSyncExecuteResponse,
    JiraSyncHistoryListResponse,
    JiraSyncHistoryResponse,
    JiraSyncPreviewItem,
    JiraSyncPreviewResponse,
    JiraSyncResultItem,
)
from app.utils.encryption import decrypt_token
from app.utils.logger import get_logger

logger = get_logger(__name__)

# RIP custom fields that get auto-provisioned in Jira on first sync
_RIP_CUSTOM_FIELDS = {
    "rip_id": {
        "name": "RIP ID",
        "type": "com.atlassian.jira.plugin.system.customfieldtypes:textfield",
        "searcherKey": "com.atlassian.jira.plugin.system.customfieldtypes:textsearcher",
    },
    "rip_module_id": {
        "name": "RIP Module ID",
        "type": "com.atlassian.jira.plugin.system.customfieldtypes:textfield",
        "searcherKey": "com.atlassian.jira.plugin.system.customfieldtypes:textsearcher",
    },
    "rip_feature_id": {
        "name": "RIP Feature ID",
        "type": "com.atlassian.jira.plugin.system.customfieldtypes:textfield",
        "searcherKey": "com.atlassian.jira.plugin.system.customfieldtypes:textsearcher",
    },
    "rip_version": {
        "name": "RIP Version",
        "type": "com.atlassian.jira.plugin.system.customfieldtypes:textfield",
        "searcherKey": "com.atlassian.jira.plugin.system.customfieldtypes:textsearcher",
    },
    "rip_content_hash": {
        "name": "RIP Content Hash",
        "type": "com.atlassian.jira.plugin.system.customfieldtypes:textfield",
        "searcherKey": "com.atlassian.jira.plugin.system.customfieldtypes:textsearcher",
    },
}


class JiraSyncService:
    def __init__(
        self,
        module_feature_repo: ModuleFeatureRepository,
        user_story_repo: UserStoryRepository,
    ) -> None:
        self._module_feature_repo = module_feature_repo
        self._user_story_repo = user_story_repo

    @staticmethod
    async def _mark_synced(entity_type: str, updater, *, log_ctx: str = "") -> None:
        """Set the Neo4j ``is_jira_synced`` flag after a successful JIRA sync.

        ``updater`` is a zero-arg callable returning the repository coroutine.
        A failure here must not undo an already-successful JIRA sync, so any
        error is logged and swallowed rather than raised.
        """
        try:
            await updater()
        except Exception as exc:  # noqa: BLE001 — best-effort flag update
            logger.warning(
                "Failed to set is_jira_synced flag: entity=%s %s error=%s",
                entity_type,
                log_ctx,
                exc,
            )

    @staticmethod
    def _notify_jira_sync_status(
        *,
        uow: UnitOfWork,
        project_id: UUID,
        status: str,
        actor_user_id: UUID | None,
        created: int = 0,
        updated: int = 0,
        deprecated: int = 0,
        errors_count: int = 0,
        error: str | None = None,
    ) -> None:
        """Record an activity-log entry and notify the project owner and every
        assigned member about a Jira sync's start, completion, or failure.

        Never raises — a notification/activity-log failure must not fail an
        already-successful (or already-failed) sync.
        """
        from app.core.enums.notification_type import NotificationType  # noqa: PLC0415
        from app.services.activity_log_service import record_activity  # noqa: PLC0415
        from app.services.notification_service import publish_notification  # noqa: PLC0415

        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            return

        if status == "started":
            activity_type = ActivityType.JIRA_SYNC_STARTED
            summary = SUMMARY_ACTIVITY_JIRA_SYNC_STARTED
            message = MSG_ACTIVITY_JIRA_SYNC_STARTED
            notification_type = NotificationType.INFO
            data: dict[str, object] = {}
        elif status == "completed":
            activity_type = ActivityType.JIRA_SYNC_COMPLETED
            summary = SUMMARY_ACTIVITY_JIRA_SYNC_COMPLETED
            message = MSG_ACTIVITY_JIRA_SYNC_COMPLETED.format(
                created=created, updated=updated, deprecated=deprecated, errors=errors_count
            )
            notification_type = NotificationType.SUCCESS
            data = {
                "created": created,
                "updated": updated,
                "deprecated": deprecated,
                "errors": errors_count,
            }
        else:
            activity_type = ActivityType.JIRA_SYNC_FAILED
            summary = SUMMARY_ACTIVITY_JIRA_SYNC_FAILED
            message = MSG_ACTIVITY_JIRA_SYNC_FAILED.format(error=(error or "")[:200])
            notification_type = NotificationType.ERROR
            data = {"error": error}

        record_activity(
            project_id=project_id,
            activity_type=activity_type,
            summary=summary,
            message=message,
            actor_user_id=actor_user_id,
            data=data,
        )

        recipient_ids = {
            member.user_id for member in uow.project_members.list_by_project(project_id)
        }
        if project.owner_id is not None:
            recipient_ids.add(project.owner_id)

        for recipient_id in recipient_ids:
            try:
                publish_notification(
                    user_id=recipient_id,
                    title=summary,
                    message=f'{message} in "{project.name}".',
                    notification_type=notification_type,
                    data={"project_id": str(project_id), **data},
                )
            except Exception:
                logger.warning(
                    "_notify_jira_sync_status: failed to notify project_id=%s user_id=%s",
                    project_id,
                    recipient_id,
                    exc_info=True,
                )

    # ── Preview (Sync Tray) ────────────────────────────────────────────────

    async def compute_sync_preview(
        self, project_id: UUID, uow: UnitOfWork
    ) -> JiraSyncPreviewResponse:
        integration = uow.jira_integrations.get_active_by_project_id(project_id)
        if not integration:
            raise NotFoundError(MSG_JIRA_INTEGRATION_NOT_FOUND.format(project_id=project_id))

        modules = await self._module_feature_repo.list_modules_by_project(project_id)
        all_stories, _ = await self._user_story_repo.list_user_stories_for_project(
            project_id=project_id, skip=0, limit=10_000
        )

        existing_mappings = uow.jira_sync_mappings.list_by_integration(integration.id)
        mapping_index: dict[tuple[str, str], JiraSyncMapping] = {
            (m.rip_entity_type, str(m.rip_entity_id)): m for m in existing_mappings
        }

        items: list[JiraSyncPreviewItem] = []
        current_entity_ids: set[str] = set()

        # Classify features
        for module in modules:
            for feature in getattr(module, "features", []):
                fid = str(feature.id)
                current_entity_ids.add(fid)
                key = ("feature", fid)
                mapping = mapping_index.get(key)
                content_hash = _compute_content_hash("feature", feature)

                if mapping is None:
                    items.append(
                        JiraSyncPreviewItem(
                            rip_entity_id=UUID(fid),
                            rip_entity_type="feature",
                            rip_entity_code=getattr(feature, "fea_code", None),
                            title=feature.name,
                            version=1,
                            change_type="new",
                        )
                    )
                elif mapping.rip_content_hash != content_hash:
                    items.append(
                        JiraSyncPreviewItem(
                            rip_entity_id=UUID(fid),
                            rip_entity_type="feature",
                            rip_entity_code=getattr(feature, "fea_code", None),
                            title=feature.name,
                            version=getattr(feature, "version", 1)
                            if hasattr(feature, "version")
                            else 1,
                            change_type="changed",
                        )
                    )

        # Classify user stories
        for story in all_stories:
            sid = str(story.id)
            current_entity_ids.add(sid)
            key = ("user_story", sid)
            mapping = mapping_index.get(key)
            content_hash = _compute_content_hash("user_story", story)
            version = getattr(story, "version", 1) or 1
            flags = []
            if mapping and mapping.rip_version < version:
                flags.append("re_test_required")

            if mapping is None:
                items.append(
                    JiraSyncPreviewItem(
                        rip_entity_id=UUID(sid),
                        rip_entity_type="user_story",
                        rip_entity_code=getattr(story, "user_story_code", None),
                        title=getattr(story, "title", ""),
                        version=version,
                        change_type="new",
                        flags=flags,
                    )
                )
            elif mapping.rip_content_hash != content_hash:
                items.append(
                    JiraSyncPreviewItem(
                        rip_entity_id=UUID(sid),
                        rip_entity_type="user_story",
                        rip_entity_code=getattr(story, "user_story_code", None),
                        title=getattr(story, "title", ""),
                        version=version,
                        change_type="changed",
                        flags=flags,
                    )
                )

        # Detect deprecated — mapped but no longer in hierarchy
        for (etype, eid), mapping in mapping_index.items():
            if mapping.sync_status == "deprecated":
                continue
            if eid not in current_entity_ids:
                items.append(
                    JiraSyncPreviewItem(
                        rip_entity_id=UUID(eid),
                        rip_entity_type=etype,
                        rip_entity_code=mapping.rip_entity_code,
                        title=f"[Deprecated] {mapping.jira_issue_key}",
                        version=mapping.rip_version,
                        change_type="deprecated",
                    )
                )

        new_count = sum(1 for i in items if i.change_type == "new")
        changed_count = sum(1 for i in items if i.change_type == "changed")
        deprecated_count = sum(1 for i in items if i.change_type == "deprecated")
        total_current = len(current_entity_ids)
        unchanged_count = total_current - new_count - changed_count

        return JiraSyncPreviewResponse(
            new_count=new_count,
            changed_count=changed_count,
            deprecated_count=deprecated_count,
            unchanged_count=max(0, unchanged_count),
            items=items,
        )

    # ── Execute sync ───────────────────────────────────────────────────────

    async def execute_sync(
        self,
        project_id: UUID,
        released_entity_ids: list[UUID],
        held_entity_ids: list[UUID],
        triggered_by_id: UUID,
        uow: UnitOfWork,
    ) -> JiraSyncHistory:
        import asyncio

        integration = uow.jira_integrations.get_active_by_project_id(project_id)
        if not integration:
            raise NotFoundError(MSG_JIRA_INTEGRATION_NOT_FOUND.format(project_id=project_id))

        if not released_entity_ids:
            raise ValidationError(MSG_JIRA_SYNC_NOTHING_TO_SYNC)

        # Guard against concurrent sync runs for the same integration
        latest = uow.jira_sync_history.get_latest_by_project(project_id)
        if latest and latest.status == "in_progress":
            raise ConflictError(
                "A sync is already in progress for this project. Please wait for it to complete."
            )

        token = decrypt_token(integration.jira_api_token_encrypted)
        client = JiraCloudClient(
            base_url=integration.jira_base_url,
            email=integration.jira_user_email,
            api_token=token,
        )

        # Create history record
        history = JiraSyncHistory(
            integration_id=integration.id,
            project_id=project_id,
            triggered_by_id=triggered_by_id,
            status="in_progress",
            items_released=[str(eid) for eid in released_entity_ids],
            items_held=[str(eid) for eid in held_entity_ids],
        )
        uow.jira_sync_history.add(history)
        uow.flush()

        released_set = {str(eid) for eid in released_entity_ids}
        delay = settings.JIRA_SYNC_REQUEST_DELAY_MS / 1000

        # Ensure traceability custom fields exist
        traceability_ids = await _ensure_custom_fields(client, integration, uow)

        # Load hierarchy
        modules = await self._module_feature_repo.list_modules_by_project(project_id)
        all_stories, _ = await self._user_story_repo.list_user_stories_for_project(
            project_id=project_id, skip=0, limit=10_000
        )

        existing_mappings = uow.jira_sync_mappings.list_by_integration(integration.id)
        mapping_index: dict[tuple[str, str], JiraSyncMapping] = {
            (m.rip_entity_type, str(m.rip_entity_id)): m for m in existing_mappings
        }

        # Build lookup structures
        module_by_feature: dict[str, object] = {}
        feature_by_id: dict[str, object] = {}
        for module in modules:
            for feature in getattr(module, "features", []):
                feature_by_id[str(feature.id)] = feature
                module_by_feature[str(feature.id)] = module

        story_by_id: dict[str, object] = {}
        for story in all_stories:
            story_by_id[str(story.id)] = story

        counters = {"created": 0, "updated": 0, "deprecated": 0, "skipped": 0, "errors": 0}
        error_details: list[dict] = []
        current_entity_ids: set[str] = set()

        # ── Sync components (modules) ──────────────────────────────────────
        existing_components = await client.get_components(integration.jira_project_key)
        component_names = {c["name"] for c in existing_components}

        for module in modules:
            module_name = module.name
            if module_name not in component_names:
                try:
                    await client.create_component(
                        integration.jira_project_key,
                        module_name,
                        f"RIP Module: {module_name} | {getattr(module, 'mod_code', '')}",
                    )
                    component_names.add(module_name)
                    await asyncio.sleep(delay)
                except Exception as exc:
                    logger.warning("Failed to create component %s: %s", module_name, exc)

        # ── Sync features → Epics ──────────────────────────────────────────
        epic_key_by_feature_id: dict[str, str] = {}

        for module in modules:
            for feature in getattr(module, "features", []):
                fid = str(feature.id)
                current_entity_ids.add(fid)

                if fid not in released_set:
                    counters["skipped"] += 1
                    # Still need the epic key for story parent linking
                    m = mapping_index.get(("feature", fid))
                    if m:
                        epic_key_by_feature_id[fid] = m.jira_issue_key
                    continue

                mapping = mapping_index.get(("feature", fid))
                content_hash = _compute_content_hash("feature", feature)

                try:
                    if mapping is None:
                        # Guard: search Jira by RIP ID before creating to prevent duplicates
                        # on retry after partial DB failure
                        existing_key = await _find_existing_jira_issue(
                            client, str(feature.id), traceability_ids, integration.jira_project_key
                        )
                        if existing_key:
                            _save_mapping(
                                uow,
                                integration.id,
                                "feature",
                                fid,
                                getattr(feature, "fea_code", None),
                                existing_key["key"],
                                existing_key["id"],
                                content_hash,
                                1,
                            )
                            epic_key_by_feature_id[fid] = existing_key["key"]
                            counters["skipped"] += 1
                        else:
                            fields = _build_epic_fields(feature, module, traceability_ids)
                            fields["issuetype"] = {"name": integration.epic_issue_type_name}
                            fields["project"] = {"key": integration.jira_project_key}
                            result = await client.create_issue(fields)
                            _save_mapping(
                                uow,
                                integration.id,
                                "feature",
                                fid,
                                getattr(feature, "fea_code", None),
                                result["key"],
                                result["id"],
                                content_hash,
                                1,
                            )
                            epic_key_by_feature_id[fid] = result["key"]
                            counters["created"] += 1
                    elif mapping.rip_content_hash != content_hash:
                        fields = _build_epic_fields(feature, module, traceability_ids)
                        await client.update_issue(mapping.jira_issue_key, fields)
                        mapping.rip_content_hash = content_hash
                        mapping.last_synced_at = datetime.now(UTC)
                        epic_key_by_feature_id[fid] = mapping.jira_issue_key
                        counters["updated"] += 1
                    else:
                        epic_key_by_feature_id[fid] = mapping.jira_issue_key
                        counters["skipped"] += 1

                    await asyncio.sleep(delay)

                except Exception as exc:
                    counters["errors"] += 1
                    error_details.append(
                        {"entity_id": fid, "type": "feature", "error": str(exc)[:500]}
                    )
                    logger.warning("Failed to sync feature: feature_id=%s error=%s", fid, exc)
                    if mapping:
                        epic_key_by_feature_id[fid] = mapping.jira_issue_key

        # ── Sync user stories → Stories ────────────────────────────────────
        for story in all_stories:
            sid = str(story.id)
            current_entity_ids.add(sid)

            if sid not in released_set:
                counters["skipped"] += 1
                continue

            feature_id = str(getattr(story, "feature_id", ""))
            module = module_by_feature.get(feature_id)
            feature = feature_by_id.get(feature_id)
            mapping = mapping_index.get(("user_story", sid))
            content_hash = _compute_content_hash("user_story", story)
            version = getattr(story, "version", 1) or 1

            try:
                epic_key = epic_key_by_feature_id.get(feature_id)

                if mapping is None:
                    # Guard: search Jira by RIP ID before creating
                    existing_key = await _find_existing_jira_issue(
                        client, sid, traceability_ids, integration.jira_project_key
                    )
                    if existing_key:
                        _save_mapping(
                            uow,
                            integration.id,
                            "user_story",
                            sid,
                            getattr(story, "user_story_code", None),
                            existing_key["key"],
                            existing_key["id"],
                            content_hash,
                            version,
                        )
                        counters["skipped"] += 1
                    else:
                        fields = _build_story_fields(
                            story, feature, module, traceability_ids, integration
                        )
                        if epic_key:
                            fields["parent"] = {"key": epic_key}
                        fields["issuetype"] = {"name": integration.issue_type_name}
                        fields["project"] = {"key": integration.jira_project_key}
                        result = await client.create_issue(fields)
                        _save_mapping(
                            uow,
                            integration.id,
                            "user_story",
                            sid,
                            getattr(story, "user_story_code", None),
                            result["key"],
                            result["id"],
                            content_hash,
                            version,
                        )
                        counters["created"] += 1
                elif mapping.rip_content_hash != content_hash:
                    fields = _build_story_fields(
                        story, feature, module, traceability_ids, integration
                    )
                    await client.update_issue(mapping.jira_issue_key, fields)
                    mapping.rip_content_hash = content_hash
                    mapping.rip_version = version
                    mapping.last_synced_at = datetime.now(UTC)
                    counters["updated"] += 1
                else:
                    counters["skipped"] += 1

                await asyncio.sleep(delay)

            except Exception as exc:
                counters["errors"] += 1
                error_details.append(
                    {"entity_id": sid, "type": "user_story", "error": str(exc)[:500]}
                )
                logger.warning("Failed to sync story: story_id=%s error=%s", sid, exc)

        # ── Deprecate removed ──────────────────────────────────────────────
        deprecated_transition = integration.deprecated_transition_id
        for (etype, eid), mapping in mapping_index.items():
            if mapping.sync_status == "deprecated":
                continue
            if eid not in current_entity_ids and eid in released_set:
                try:
                    if deprecated_transition:
                        await client.transition_issue(mapping.jira_issue_key, deprecated_transition)
                    mapping.sync_status = "deprecated"
                    mapping.last_synced_at = datetime.now(UTC)
                    counters["deprecated"] += 1
                    await asyncio.sleep(delay)
                except Exception as exc:
                    counters["errors"] += 1
                    error_details.append({"entity_id": eid, "type": etype, "error": str(exc)[:500]})

        # ── Finalize ──────────────────────────────────────────────────────
        history.status = "completed" if counters["errors"] == 0 else "partial"
        history.summary = counters
        history.error_details = error_details or None
        history.completed_at = datetime.now(UTC)
        integration.last_synced_at = datetime.now(UTC)
        uow.flush()

        logger.info(
            "Jira sync finished: project_id=%s created=%s updated=%s deprecated=%s errors=%s",
            project_id,
            counters["created"],
            counters["updated"],
            counters["deprecated"],
            counters["errors"],
        )
        return history

    # ── History ────────────────────────────────────────────────────────────

    def get_sync_history(
        self,
        project_id: UUID,
        skip: int,
        limit: int,
        uow: UnitOfWork,
    ) -> JiraSyncHistoryListResponse:
        items, total = uow.jira_sync_history.list_by_project(project_id, skip, limit)
        return JiraSyncHistoryListResponse(
            total=total,
            skip=skip,
            limit=limit,
            items=[JiraSyncHistoryResponse.model_validate(h) for h in items],
        )

    def get_sync_history_detail(
        self,
        sync_id: UUID,
        uow: UnitOfWork,
    ) -> JiraSyncHistoryResponse:
        record = uow.jira_sync_history.get(sync_id)
        if not record:
            raise NotFoundError(f"Sync history {sync_id} not found.")
        return JiraSyncHistoryResponse.model_validate(record)

    async def execute_sync_with_hierarchy(
        self,
        project_id: UUID,
        modules: list,
        triggered_by_id: UUID,
        uow: UnitOfWork,
    ) -> JiraSyncExecuteResponse:
        """Execute sync with full RIP hierarchy (modules/features/stories).

        Processes the provided hierarchy and syncs to JIRA:
        - Modules → Components (with RIP Module ID custom field)
        - Features → Epics (with RIP Feature ID custom field)
        - User Stories → Stories (with Gherkin AC in ADF description)

        Returns counts and details of created/updated/deprecated items.
        """
        import asyncio

        from app.schemas.jira_integration_schema import (
            JiraSyncExecuteResponse,
        )

        # Validate integration exists
        integration = uow.jira_integrations.get_active_by_project_id(project_id)
        if not integration:
            raise NotFoundError(MSG_JIRA_INTEGRATION_NOT_FOUND.format(project_id=project_id))

        self._notify_jira_sync_status(
            uow=uow, project_id=project_id, status="started", actor_user_id=triggered_by_id
        )

        # Decrypt token and initialize JIRA client
        token = decrypt_token(integration.jira_api_token_encrypted)
        client = JiraCloudClient(
            base_url=integration.jira_base_url,
            email=integration.jira_user_email,
            api_token=token,
        )

        # Initialize response counters
        counters = {
            "created": 0,
            "updated": 0,
            "deprecated": 0,
            "skipped": 0,
            "errors": 0,
        }
        created_items = []
        updated_items = []
        deprecated_items = []
        errors = []

        delay = settings.JIRA_SYNC_REQUEST_DELAY_MS / 1000.0

        try:
            # Ensure custom fields exist and get their IDs
            custom_field_ids = await _ensure_custom_fields(client, integration, uow)

            # RIP traceability field IDs — used to strip only these on fallback
            rip_field_ids = set(custom_field_ids.values())

            # Discover how this project links Story→Epic (Epic Link field vs parent)
            epic_link_field = await _discover_epic_link_field(client)

            # Pre-flight: surface the project's valid issue types so a bad
            # configured name (the usual cause of per-issue "Specify a valid
            # issue type" 400s) is obvious in the log instead of failing
            # silently per story.
            try:
                project_issue_types = await client.get_issue_types(integration.jira_project_key)
                valid_type_names = {t.get("name") for t in project_issue_types if t.get("name")}
                for label, name in (
                    ("issue_type_name", integration.issue_type_name),
                    ("epic_issue_type_name", integration.epic_issue_type_name),
                ):
                    if name and name not in valid_type_names:
                        logger.warning(
                            "Configured %s=%r is not a valid issue type in Jira "
                            "project %s — issues of this type will fail. Valid "
                            "types: %s",
                            label,
                            name,
                            integration.jira_project_key,
                            ", ".join(sorted(valid_type_names)) or "(none)",
                        )
            except Exception as exc:  # noqa: BLE001 — diagnostic only
                logger.warning("Could not pre-check project issue types: %s", exc)

            # ── Process Modules → Components ──────────────────────────────
            for module in modules:
                try:
                    module_id = module.module_id
                    module_code = module.module_code
                    module_name = module.module_name
                    module_description = module.module_description

                    # Check if already synced
                    existing_mapping = uow.jira_sync_mappings.get_by_rip_entity(
                        integration_id=integration.id,
                        entity_type="module",
                        entity_id=module_id,
                    )

                    # Create or update component
                    try:
                        # Check if component already exists
                        existing_components = await client.get_components(
                            integration.jira_project_key
                        )
                        existing_component = next(
                            (c for c in existing_components if c.get("name") == module_name),
                            None,
                        )

                        if existing_component:
                            # Component exists - use existing (components have id, not key)
                            jira_id = existing_component.get("id")
                            jira_key = f"COMP-{jira_id}"  # Components use ID, not issue key
                            logger.info(
                                "Component already exists: module_id=%s jira_id=%s",
                                module_id,
                                jira_id,
                            )
                        else:
                            # Component doesn't exist - create it
                            component = await client.create_component(
                                project_key=integration.jira_project_key,
                                name=module_name,
                                description=module_description,
                            )
                            jira_id = component.get("id")
                            jira_key = f"COMP-{jira_id}"  # Components use ID for tracking
                            logger.info(
                                "Component created: module_id=%s jira_id=%s",
                                module_id,
                                jira_id,
                            )

                        if existing_mapping:
                            existing_mapping.jira_issue_key = jira_key
                            existing_mapping.jira_issue_id = jira_id
                            existing_mapping.rip_content_hash = _compute_content_hash(
                                "module", module
                            )
                            existing_mapping.sync_status = "synced"
                            existing_mapping.last_synced_at = datetime.now(UTC)
                            counters["updated"] += 1
                            updated_items.append(
                                JiraSyncResultItem(
                                    rip_entity_id=module_id,
                                    rip_entity_type="module",
                                    rip_entity_code=module_code,
                                    title=module_name,
                                    jira_key=jira_key,
                                    jira_id=jira_id,
                                    status="updated",
                                )
                            )
                        else:
                            module_hash = _compute_content_hash("module", module)
                            mapping = JiraSyncMapping(
                                integration_id=integration.id,
                                rip_entity_type="module",
                                rip_entity_id=module_id,
                                rip_entity_code=module_code,
                                jira_issue_key=jira_key,
                                jira_issue_id=jira_id,
                                rip_content_hash=module_hash,
                                rip_version=1,
                                sync_status="synced",
                            )
                            uow.jira_sync_mappings.add(mapping)
                            counters["created"] += 1
                            created_items.append(
                                JiraSyncResultItem(
                                    rip_entity_id=module_id,
                                    rip_entity_type="module",
                                    rip_entity_code=module_code,
                                    title=module_name,
                                    jira_key=jira_key,
                                    jira_id=jira_id,
                                    status="created",
                                )
                            )

                        await asyncio.sleep(delay)

                    except Exception as exc:
                        counters["errors"] += 1
                        errors.append(
                            {
                                "entity_type": "module",
                                "entity_code": module_code,
                                "error": str(exc)[:500],
                            }
                        )
                        logger.warning(
                            "Failed to sync module: module_id=%s error=%s",
                            module_id,
                            exc,
                        )
                        continue

                    # Component synced successfully → flag the Module in Neo4j.
                    await self._mark_synced(
                        "module",
                        lambda: self._module_feature_repo.update_module_sync_flags(
                            project_id, str(module_id), is_jira_synced=True
                        ),
                        log_ctx=f"module_id={module_id}",
                    )

                    # ── Process Features → Epics ──────────────────────────
                    feature_code = (
                        ""  # Initialize before loop to prevent NameError in exception handler
                    )
                    feature_id = (
                        None  # Initialize before loop to prevent unbound reference in handler
                    )
                    for feature in getattr(module, "features", []) or []:
                        try:
                            feature_id = feature.feature_id
                            feature_code = feature.feature_code or ""
                            feature_name = feature.feature_name
                            feature_description = feature.feature_description

                            # Build epic fields (description must be ADF for Jira Cloud v3 API)
                            epic_fields = {
                                "project": {"key": integration.jira_project_key},
                                "issuetype": {"name": integration.epic_issue_type_name},
                                "summary": feature_name,
                                "description": _build_adf_document(
                                    [
                                        _adf_paragraph(feature_description or " "),
                                    ]
                                ),
                                # Attach the module's Jira Component to the Epic
                                "components": [{"name": module_name}],
                            }

                            # Add custom fields only if on screen, skip with graceful fallback if not
                            if custom_field_ids.get("rip_id"):
                                epic_fields[custom_field_ids["rip_id"]] = str(feature_id)
                            if custom_field_ids.get("rip_module_id"):
                                epic_fields[custom_field_ids["rip_module_id"]] = str(module_id)
                            if custom_field_ids.get("rip_version"):
                                epic_fields[custom_field_ids["rip_version"]] = "1"
                            if custom_field_ids.get("rip_content_hash"):
                                epic_fields[custom_field_ids["rip_content_hash"]] = (
                                    _compute_content_hash("feature", feature)
                                )

                            existing_feature_mapping = uow.jira_sync_mappings.get_by_rip_entity(
                                integration_id=integration.id,
                                entity_type="feature",
                                entity_id=feature_id,
                            )

                            if existing_feature_mapping:
                                # project/issuetype cannot be changed on an existing issue
                                update_fields = {
                                    k: v
                                    for k, v in epic_fields.items()
                                    if k not in ("project", "issuetype")
                                }
                                await _submit_issue_fields(
                                    client,
                                    update_fields,
                                    issue_key=existing_feature_mapping.jira_issue_key,
                                    rip_field_ids=rip_field_ids,
                                    epic_link_field=epic_link_field,
                                    log_ctx=f"feature_id={feature_id}",
                                )
                                existing_feature_mapping.rip_content_hash = _compute_content_hash(
                                    "feature", feature
                                )
                                existing_feature_mapping.sync_status = "synced"
                                existing_feature_mapping.last_synced_at = datetime.now(UTC)
                                counters["updated"] += 1
                                updated_items.append(
                                    JiraSyncResultItem(
                                        rip_entity_id=feature_id,
                                        rip_entity_type="feature",
                                        rip_entity_code=feature_code,
                                        title=feature_name,
                                        jira_key=existing_feature_mapping.jira_issue_key,
                                        jira_id=existing_feature_mapping.jira_issue_id,
                                        status="updated",
                                    )
                                )
                            else:
                                # Duplicate guard: an epic with no DB mapping may still
                                # exist in Jira from a prior partially-failed run. Search
                                # by RIP ID (if custom field available), else by summary.
                                existing_jira = await _find_existing_jira_issue(
                                    client,
                                    str(feature_id),
                                    custom_field_ids,
                                    integration.jira_project_key,
                                ) or await _find_existing_by_summary(
                                    client,
                                    feature_name,
                                    integration.epic_issue_type_name,
                                    integration.jira_project_key,
                                )

                                if existing_jira:
                                    jira_epic_key = existing_jira["key"]
                                    jira_epic_id = existing_jira["id"]
                                    logger.info(
                                        "Epic already exists in Jira, linking mapping and applying fields: feature_id=%s jira_key=%s",
                                        feature_id,
                                        jira_epic_key,
                                    )
                                    # Apply fields (incl. component) to the existing epic
                                    epic_update_fields = {
                                        k: v
                                        for k, v in epic_fields.items()
                                        if k not in ("project", "issuetype")
                                    }
                                    await _submit_issue_fields(
                                        client,
                                        epic_update_fields,
                                        issue_key=jira_epic_key,
                                        rip_field_ids=rip_field_ids,
                                        epic_link_field=epic_link_field,
                                        log_ctx=f"feature_id={feature_id}",
                                    )
                                    status_label = "updated"
                                else:
                                    epic = await _submit_issue_fields(
                                        client,
                                        epic_fields,
                                        rip_field_ids=rip_field_ids,
                                        epic_link_field=epic_link_field,
                                        log_ctx=f"feature_id={feature_id}",
                                    )
                                    jira_epic_key = epic.get("key")
                                    jira_epic_id = epic.get("id")
                                    status_label = "created"

                                feature_mapping = JiraSyncMapping(
                                    integration_id=integration.id,
                                    rip_entity_type="feature",
                                    rip_entity_id=feature_id,
                                    rip_entity_code=feature_code,
                                    jira_issue_key=jira_epic_key,
                                    jira_issue_id=jira_epic_id,
                                    rip_content_hash=_compute_content_hash("feature", feature),
                                    rip_version=1,
                                    sync_status="synced",
                                )
                                uow.jira_sync_mappings.add(feature_mapping)
                                counters[status_label] += 1
                                result_item = JiraSyncResultItem(
                                    rip_entity_id=feature_id,
                                    rip_entity_type="feature",
                                    rip_entity_code=feature_code,
                                    title=feature_name,
                                    jira_key=jira_epic_key,
                                    jira_id=jira_epic_id,
                                    status=status_label,
                                )
                                if status_label == "created":
                                    created_items.append(result_item)
                                else:
                                    updated_items.append(result_item)

                                # Ensure epic mapping is available to child stories for parent linking
                                existing_feature_mapping = feature_mapping

                            await asyncio.sleep(delay)

                            # Epic synced successfully → flag the Feature in Neo4j.
                            await self._mark_synced(
                                "feature",
                                lambda: self._module_feature_repo.update_feature_sync_flags(
                                    project_id, str(module_id), str(feature_id), is_jira_synced=True
                                ),
                                log_ctx=f"feature_id={feature_id}",
                            )

                            # ── Process User Stories → Stories ────────────
                            story_code = ""  # Initialize before loop to prevent NameError in exception handler
                            story_id = None  # Initialize before loop to prevent unbound reference in handler
                            for story in getattr(feature, "user_stories", []) or []:
                                try:
                                    story_id = story.user_story_id
                                    story_code = story.user_story_code
                                    story_title = story.title

                                    # Build Gherkin AC text
                                    ac_lines = []
                                    for ac in getattr(story, "acceptance_criteria", []) or []:
                                        ac_lines.append(f"Scenario ({ac.type}):")
                                        ac_lines.append(f"  Given {ac.given}")
                                        ac_lines.append(f"  When {ac.when}")
                                        ac_lines.append(f"  Then {ac.then}")
                                        ac_lines.append("")
                                    ac_text = "\n".join(ac_lines).strip()

                                    # Build NFR bullet lines (one paragraph per NFR)
                                    nfr_list = getattr(story, "nfrs", []) or []

                                    # Build story description as ADF (required by Jira Cloud v3 API)
                                    narrative = (
                                        f"As a {getattr(story, 'as_a', '') or 'user'}, "
                                        f"I want to {getattr(story, 'i_want_to', '') or ''}, "
                                        f"so that {getattr(story, 'so_that', '') or ''}."
                                    )
                                    description_nodes = [_adf_paragraph(narrative)]
                                    if ac_text:
                                        description_nodes.append(
                                            _adf_heading("Acceptance Criteria", level=3)
                                        )
                                        description_nodes.append(
                                            _adf_code_block(ac_text, language="gherkin")
                                        )
                                    if nfr_list:
                                        description_nodes.append(
                                            _adf_heading("Non-Functional Requirements", level=3)
                                        )
                                        for nfr in nfr_list:
                                            nfr_dict = _nfr_to_dict(nfr)
                                            line = f"• [{nfr_dict['category']}] {nfr_dict['requirement']}"
                                            if nfr_dict["description"]:
                                                line += f" — {nfr_dict['description']}"
                                            description_nodes.append(_adf_paragraph(line))
                                    story_description = _build_adf_document(description_nodes)

                                    # Build story fields
                                    story_fields = {
                                        "project": {"key": integration.jira_project_key},
                                        "issuetype": {"name": integration.issue_type_name},
                                        "summary": story_title,
                                        "description": story_description,
                                        # Attach the module's Jira Component to the Story
                                        "components": [{"name": module_name}],
                                    }

                                    # Add custom fields only if available
                                    if custom_field_ids.get("rip_id"):
                                        story_fields[custom_field_ids["rip_id"]] = str(story_id)
                                    if custom_field_ids.get("rip_feature_id"):
                                        story_fields[custom_field_ids["rip_feature_id"]] = str(
                                            feature_id
                                        )
                                    if custom_field_ids.get("rip_version"):
                                        story_fields[custom_field_ids["rip_version"]] = "1"
                                    if custom_field_ids.get("rip_content_hash"):
                                        story_fields[custom_field_ids["rip_content_hash"]] = (
                                            _compute_content_hash("user_story", story)
                                        )

                                    # Link story to its parent epic. Modern Jira (team-
                                    # managed and company-managed) uses the native
                                    # "parent" field; only legacy company-managed projects
                                    # need the "Epic Link" field (handled as a fallback in
                                    # _create_or_update_with_epic_link).
                                    epic_key = (
                                        existing_feature_mapping.jira_issue_key
                                        if existing_feature_mapping
                                        else None
                                    )
                                    if epic_key:
                                        story_fields["parent"] = {"key": epic_key}

                                    existing_story_mapping = (
                                        uow.jira_sync_mappings.get_by_rip_entity(
                                            integration_id=integration.id,
                                            entity_type="user_story",
                                            entity_id=story_id,
                                        )
                                    )

                                    if existing_story_mapping:
                                        # project/issuetype cannot change on an existing issue,
                                        # but KEEP parent / Epic Link so the story gets (re)linked
                                        # to its epic on re-sync.
                                        update_fields = {
                                            k: v
                                            for k, v in story_fields.items()
                                            if k not in ("project", "issuetype")
                                        }
                                        await _submit_issue_fields(
                                            client,
                                            update_fields,
                                            issue_key=existing_story_mapping.jira_issue_key,
                                            rip_field_ids=rip_field_ids,
                                            epic_link_field=epic_link_field,
                                            log_ctx=f"story_id={story_id}",
                                        )
                                        existing_story_mapping.rip_content_hash = (
                                            _compute_content_hash("user_story", story)
                                        )
                                        existing_story_mapping.sync_status = "synced"
                                        existing_story_mapping.last_synced_at = datetime.now(UTC)
                                        counters["updated"] += 1
                                        updated_items.append(
                                            JiraSyncResultItem(
                                                rip_entity_id=story_id,
                                                rip_entity_type="user_story",
                                                rip_entity_code=story_code,
                                                title=story_title,
                                                jira_key=existing_story_mapping.jira_issue_key,
                                                jira_id=existing_story_mapping.jira_issue_id,
                                                status="updated",
                                            )
                                        )
                                    else:
                                        # Duplicate guard: a story with no DB mapping may still
                                        # exist in Jira from a prior partially-failed run. Search
                                        # by RIP ID (if custom field available), else by summary.
                                        existing_jira = await _find_existing_jira_issue(
                                            client,
                                            str(story_id),
                                            custom_field_ids,
                                            integration.jira_project_key,
                                        ) or await _find_existing_by_summary(
                                            client,
                                            story_title,
                                            integration.issue_type_name,
                                            integration.jira_project_key,
                                        )

                                        if existing_jira:
                                            jira_story_key = existing_jira["key"]
                                            jira_story_id = existing_jira["id"]
                                            logger.info(
                                                "Story already exists in Jira, linking mapping and applying fields: story_id=%s jira_key=%s",
                                                story_id,
                                                jira_story_key,
                                            )
                                            # Apply fields (incl. component + parent) to the existing story
                                            story_update_fields = {
                                                k: v
                                                for k, v in story_fields.items()
                                                if k not in ("project", "issuetype")
                                            }
                                            await _submit_issue_fields(
                                                client,
                                                story_update_fields,
                                                issue_key=jira_story_key,
                                                rip_field_ids=rip_field_ids,
                                                epic_link_field=epic_link_field,
                                                log_ctx=f"story_id={story_id}",
                                            )
                                            status_label = "updated"
                                        else:
                                            story_issue = await _submit_issue_fields(
                                                client,
                                                story_fields,
                                                rip_field_ids=rip_field_ids,
                                                epic_link_field=epic_link_field,
                                                log_ctx=f"story_id={story_id}",
                                            )
                                            jira_story_key = story_issue.get("key")
                                            jira_story_id = story_issue.get("id")
                                            status_label = "created"

                                        story_mapping = JiraSyncMapping(
                                            integration_id=integration.id,
                                            rip_entity_type="user_story",
                                            rip_entity_id=story_id,
                                            rip_entity_code=story_code,
                                            jira_issue_key=jira_story_key,
                                            jira_issue_id=jira_story_id,
                                            rip_content_hash=_compute_content_hash(
                                                "user_story", story
                                            ),
                                            rip_version=1,
                                            sync_status="synced",
                                        )
                                        uow.jira_sync_mappings.add(story_mapping)
                                        counters[status_label] += 1
                                        result_item = JiraSyncResultItem(
                                            rip_entity_id=story_id,
                                            rip_entity_type="user_story",
                                            rip_entity_code=story_code,
                                            title=story_title,
                                            jira_key=jira_story_key,
                                            jira_id=jira_story_id,
                                            status=status_label,
                                        )
                                        if status_label == "created":
                                            created_items.append(result_item)
                                        else:
                                            updated_items.append(result_item)

                                    # Story synced successfully → flag the UserStory in Neo4j.
                                    await self._mark_synced(
                                        "user_story",
                                        lambda: self._user_story_repo.update_user_story_sync_flags(
                                            str(story_id), is_jira_synced=True
                                        ),
                                        log_ctx=f"story_id={story_id}",
                                    )

                                    await asyncio.sleep(delay)

                                except Exception as exc:
                                    counters["errors"] += 1
                                    errors.append(
                                        {
                                            "entity_type": "user_story",
                                            "entity_code": story_code,
                                            "error": str(exc)[:500],
                                        }
                                    )
                                    logger.warning(
                                        "Failed to sync story: story_id=%s error=%s",
                                        story_id,
                                        exc,
                                    )

                        except Exception as exc:
                            counters["errors"] += 1
                            errors.append(
                                {
                                    "entity_type": "feature",
                                    "entity_code": feature_code,
                                    "error": str(exc)[:500],
                                }
                            )
                            logger.warning(
                                "Failed to sync feature: feature_id=%s error=%s",
                                feature_id,
                                exc,
                            )

                except Exception as exc:
                    counters["errors"] += 1
                    errors.append(
                        {
                            "entity_type": "module",
                            "entity_code": module_code,
                            "error": str(exc)[:500],
                        }
                    )
                    logger.warning(
                        "Failed to process module: module_id=%s error=%s",
                        module_id,
                        exc,
                    )

        except Exception as exc:
            logger.error(
                "Sync failed with critical error: project_id=%s error=%s",
                project_id,
                exc,
            )
            self._notify_jira_sync_status(
                uow=uow,
                project_id=project_id,
                status="failed",
                actor_user_id=triggered_by_id,
                error=str(exc),
            )
            raise

        # Commit all changes
        uow.flush()

        total_synced = counters["created"] + counters["updated"] + counters["deprecated"]

        logger.info(
            "Sync with hierarchy completed: project_id=%s created=%s updated=%s deprecated=%s errors=%s",
            project_id,
            counters["created"],
            counters["updated"],
            counters["deprecated"],
            counters["errors"],
        )
        self._notify_jira_sync_status(
            uow=uow,
            project_id=project_id,
            status="completed",
            actor_user_id=triggered_by_id,
            created=counters["created"],
            updated=counters["updated"],
            deprecated=counters["deprecated"],
            errors_count=counters["errors"],
        )

        return JiraSyncExecuteResponse(
            created=counters["created"],
            updated=counters["updated"],
            deprecated=counters["deprecated"],
            skipped=counters["skipped"],
            total_synced=total_synced,
            message=f"Synced {total_synced} items to JIRA",
            created_items=created_items,
            updated_items=updated_items,
            deprecated_items=deprecated_items,
            errors=errors,
            sync_completed_at=datetime.now(UTC),
        )


# ── Helpers ────────────────────────────────────────────────────────────────


def _first_attr(entity: object, *names: str, default: str = "") -> object:
    """Return the first present, non-None attribute among ``names``.

    Handles both the Neo4j domain models (``name``/``description``) and the
    request-payload schemas (``module_name``/``feature_name`` etc.) so the
    content hash is stable regardless of which sync path produced the entity.
    """
    for name in names:
        value = getattr(entity, name, None)
        if value is not None:
            return value
    return default


def _compute_content_hash(entity_type: str, entity: object) -> str:
    """SHA-256 of RIP-owned fields for change detection.

    Accepts either the Neo4j domain models or the request-payload schemas by
    checking both field-naming conventions for module/feature attributes.
    """
    if entity_type == "module":
        data = {
            "name": _first_attr(entity, "name", "module_name"),
            "description": _first_attr(entity, "description", "module_description"),
        }
    elif entity_type == "feature":
        data = {
            "name": _first_attr(entity, "name", "feature_name"),
            "description": _first_attr(entity, "description", "feature_description"),
        }
    else:  # user_story
        criteria = getattr(entity, "acceptance_criteria", []) or []
        nfrs = getattr(entity, "nfrs", []) or []
        data = {
            "title": _first_attr(entity, "title"),
            "as_a": _first_attr(entity, "as_a"),
            "i_want_to": _first_attr(entity, "i_want_to"),
            "so_that": _first_attr(entity, "so_that"),
            "acceptance_criteria": [_ac_to_dict(ac) for ac in criteria],
            "nfrs": [_nfr_to_dict(nfr) for nfr in nfrs],
        }
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _ac_to_dict(ac: object) -> dict:
    """Normalize an acceptance criterion (pydantic model or dict) to a dict.

    Ensures the hash is identical whether the criterion arrived as a
    ``JiraAcceptanceCriteria`` payload model or a stored dict.
    """
    if isinstance(ac, dict):
        return {
            "type": ac.get("type", ""),
            "given": ac.get("given", ""),
            "when": ac.get("when", ""),
            "then": ac.get("then", ""),
        }
    return {
        "type": getattr(ac, "type", ""),
        "given": getattr(ac, "given", ""),
        "when": getattr(ac, "when", ""),
        "then": getattr(ac, "then", ""),
    }


def _nfr_to_dict(nfr: object) -> dict:
    """Normalize a non-functional requirement (pydantic model or dict) to a dict."""
    if isinstance(nfr, dict):
        return {
            "category": nfr.get("category", ""),
            "requirement": nfr.get("requirement", ""),
            "description": nfr.get("description", ""),
        }
    return {
        "category": getattr(nfr, "category", ""),
        "requirement": getattr(nfr, "requirement", ""),
        "description": getattr(nfr, "description", ""),
    }


async def _ensure_custom_fields(
    client: JiraCloudClient, integration: object, uow: UnitOfWork
) -> dict:
    """Ensure all 5 RIP traceability custom fields exist in Jira.

    Returns a dict like {"rip_id": "customfield_10200", ...}.
    Caches the result in integration.traceability_field_ids.
    """
    if integration.traceability_field_ids:
        return integration.traceability_field_ids

    existing_fields = await client.get_fields()
    name_to_id = {f["name"]: f["id"] for f in existing_fields if f.get("custom")}

    field_ids: dict[str, str] = {}
    for key, spec in _RIP_CUSTOM_FIELDS.items():
        if spec["name"] in name_to_id:
            field_ids[key] = name_to_id[spec["name"]]
        else:
            result = await client.create_field(spec["name"], spec["type"], spec["searcherKey"])
            field_ids[key] = result["id"]

    integration.traceability_field_ids = field_ids
    uow.flush()
    return field_ids


async def _discover_epic_link_field(client: JiraCloudClient) -> str | None:
    """Return the "Epic Link" custom-field id for company-managed projects.

    Company-managed (classic) Jira projects link a Story to its Epic through a
    dedicated "Epic Link" custom field, whereas team-managed (next-gen)
    projects use the native ``parent`` field. Returns the field id (e.g.
    ``customfield_10014``) when present, else ``None`` (use ``parent``).
    """
    try:
        fields = await client.get_fields()
    except Exception as exc:
        logger.warning("Could not list fields for Epic Link discovery: %s", exc)
        return None
    for field in fields:
        if field.get("name") == "Epic Link":
            return field.get("id")
    return None


async def _submit_issue_fields(
    client: JiraCloudClient,
    fields: dict,
    *,
    issue_key: str | None = None,
    rip_field_ids: set[str],
    epic_link_field: str | None,
    log_ctx: str = "",
) -> dict | None:
    """Create (``issue_key=None``) or update an issue with graceful fallbacks.

    Retries progressively when Jira rejects fields:
      1. RIP traceability custom fields not on the screen → drop them.
      2. ``parent`` not accepted (legacy company-managed project) → convert the
         parent link to the "Epic Link" custom field, if one exists.

    Returns the created-issue dict on create, or ``None`` on update.
    """
    attempt = dict(fields)
    tried_drop_rip = False
    tried_epic_link = False

    while True:
        try:
            if issue_key is None:
                return await client.create_issue(attempt)
            await client.update_issue(issue_key, attempt)
            return None
        except Exception as exc:
            msg = str(exc)
            screen_err = "cannot be set" in msg or "not on the appropriate screen" in msg

            # Fallback 1: RIP custom fields not on screen → drop them.
            if not tried_drop_rip and screen_err and any(f in attempt for f in rip_field_ids):
                attempt = {k: v for k, v in attempt.items() if k not in rip_field_ids}
                tried_drop_rip = True
                logger.info("Retrying without RIP custom fields: %s", log_ctx)
                continue

            # Fallback 2: parent rejected → use the legacy Epic Link field.
            if (
                not tried_epic_link
                and "parent" in attempt
                and epic_link_field
                and ("parent" in msg.lower() or screen_err)
            ):
                parent = attempt.pop("parent")
                key = parent.get("key") if isinstance(parent, dict) else None
                if key:
                    attempt[epic_link_field] = key
                tried_epic_link = True
                logger.info("Retrying story-epic link via Epic Link field: %s", log_ctx)
                continue

            raise


def _build_epic_fields(feature: object, module: object, traceability_ids: dict) -> dict:
    """Build Jira issue fields for a Feature → Epic."""
    fields: dict = {
        "summary": getattr(feature, "name", ""),
        "description": _build_adf_document(
            [
                _adf_paragraph(getattr(feature, "description", "") or ""),
                _adf_paragraph(
                    f"RIP Feature Code: {getattr(feature, 'fea_code', '')}  "
                    f"|  Module: {getattr(module, 'name', '')} ({getattr(module, 'mod_code', '')})",
                    italic=True,
                ),
            ]
        ),
        "components": [{"name": getattr(module, "name", "")}],
    }
    if traceability_ids.get("rip_id"):
        fields[traceability_ids["rip_id"]] = str(feature.id)
    if traceability_ids.get("rip_module_id"):
        fields[traceability_ids["rip_module_id"]] = str(module.id)
    if traceability_ids.get("rip_version"):
        fields[traceability_ids["rip_version"]] = "1"
    if traceability_ids.get("rip_content_hash"):
        fields[traceability_ids["rip_content_hash"]] = _compute_content_hash("feature", feature)
    return fields


def _build_story_fields(
    story: object,
    feature: object | None,
    module: object | None,
    traceability_ids: dict,
    integration: object,
) -> dict:
    """Build Jira issue fields for a User Story → Story."""
    story_code = getattr(story, "user_story_code", "") or ""
    title = getattr(story, "title", "") or ""
    summary = f"{story_code} · {title}" if story_code else title

    # Build ADF description with user story + Gherkin AC
    doc_nodes = []

    # User story section
    doc_nodes.append(_adf_heading("User Story", level=3))
    as_a = getattr(story, "as_a", "")
    i_want_to = getattr(story, "i_want_to", "")
    so_that = getattr(story, "so_that", "")
    if as_a or i_want_to or so_that:
        doc_nodes.append(_adf_paragraph(f"As a {as_a}, I want to {i_want_to} so that {so_that}"))
    else:
        doc_nodes.append(_adf_paragraph(getattr(story, "description", "") or ""))

    # Acceptance criteria as Gherkin
    ac_list = getattr(story, "acceptance_criteria", []) or []
    if ac_list:
        doc_nodes.append(_adf_heading("Acceptance Criteria", level=3))
        gherkin_lines = []
        for ac in ac_list:
            if isinstance(ac, dict):
                ac_type = ac.get("type", "Scenario")
                gherkin_lines.append(f"# {ac_type}")
                gherkin_lines.append(f"Scenario: {ac_type}")
                if ac.get("given"):
                    gherkin_lines.append(f"  Given {ac['given']}")
                if ac.get("when"):
                    gherkin_lines.append(f"  When {ac['when']}")
                if ac.get("then"):
                    gherkin_lines.append(f"  Then {ac['then']}")
                gherkin_lines.append("")
        doc_nodes.append(_adf_code_block("\n".join(gherkin_lines).strip(), language="gherkin"))

    # Non-functional requirements
    nfr_list = getattr(story, "nfrs", []) or []
    if nfr_list:
        doc_nodes.append(_adf_heading("Non-Functional Requirements", level=3))
        for nfr in nfr_list:
            nfr_dict = _nfr_to_dict(nfr)
            line = f"• [{nfr_dict['category']}] {nfr_dict['requirement']}"
            if nfr_dict["description"]:
                line += f" — {nfr_dict['description']}"
            doc_nodes.append(_adf_paragraph(line))

    # Traceability footer
    module_name = getattr(module, "name", "") if module else ""
    module_code = getattr(module, "mod_code", "") if module else ""
    feature_name = getattr(feature, "name", "") if feature else ""
    feature_code = getattr(feature, "fea_code", "") if feature else ""
    doc_nodes.append(_adf_heading("Traceability", level=3))
    doc_nodes.append(
        _adf_paragraph(
            f"Module: {module_name} ({module_code})  |  Feature: {feature_name} ({feature_code})  |  Story: {story_code}",
            italic=True,
        )
    )

    fields: dict = {
        "summary": summary,
        "description": _build_adf_document(doc_nodes),
    }

    if module:
        fields["components"] = [{"name": module_name}]

    version = getattr(story, "version", 1) or 1
    if traceability_ids.get("rip_id"):
        fields[traceability_ids["rip_id"]] = str(story.id)
    if traceability_ids.get("rip_module_id") and module:
        fields[traceability_ids["rip_module_id"]] = str(module.id)
    if traceability_ids.get("rip_feature_id") and feature:
        fields[traceability_ids["rip_feature_id"]] = str(feature.id)
    if traceability_ids.get("rip_version"):
        fields[traceability_ids["rip_version"]] = str(version)
    if traceability_ids.get("rip_content_hash"):
        fields[traceability_ids["rip_content_hash"]] = _compute_content_hash("user_story", story)

    return fields


def _save_mapping(
    uow: UnitOfWork,
    integration_id: UUID,
    entity_type: str,
    entity_id: str,
    entity_code: str | None,
    jira_key: str,
    jira_id: str,
    content_hash: str,
    version: int,
) -> None:
    mapping = JiraSyncMapping(
        integration_id=integration_id,
        rip_entity_type=entity_type,
        rip_entity_id=UUID(entity_id),
        rip_entity_code=entity_code,
        jira_issue_key=jira_key,
        jira_issue_id=str(jira_id),
        rip_content_hash=content_hash,
        rip_version=version,
        sync_status="synced",
    )
    uow.jira_sync_mappings.add(mapping)


async def _find_existing_jira_issue(
    client: JiraCloudClient,
    rip_entity_id: str,
    traceability_ids: dict,
    project_key: str,
) -> dict | None:
    """Search Jira by RIP ID custom field before creating.

    Prevents duplicate issues when a Celery task retries after the Jira API
    call succeeded but the DB transaction rolled back (losing the mapping row).
    Returns {"key": "MER-1", "id": "10001"} or None.
    """
    rip_id_field = traceability_ids.get("rip_id")
    if not rip_id_field:
        return None

    # Extract the field number from "customfield_10200" for JQL
    field_num = rip_id_field.replace("customfield_", "")
    jql = f'project = "{project_key}" AND cf[{field_num}] = "{rip_entity_id}"'

    try:
        issues = await client.search_issues(jql, fields=["id", "key", "summary"], max_results=1)
        if issues:
            issue = issues[0]
            return {"key": issue["key"], "id": issue["id"]}
    except Exception as exc:
        logger.warning(
            "RIP ID search failed — will attempt create: rip_id=%s error=%s", rip_entity_id, exc
        )
    return None


def _escape_jql(value: str) -> str:
    """Escape a string for safe embedding inside a double-quoted JQL literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


async def _find_existing_by_summary(
    client: JiraCloudClient,
    summary: str,
    issue_type_name: str,
    project_key: str,
) -> dict | None:
    """Find an existing Jira issue by exact summary + issue type in a project.

    A fallback duplicate guard used when the RIP ID custom field is not
    available for searching (e.g. not on the issue screen). Prevents creating
    duplicate epics/stories when a Jira issue exists but its DB mapping was
    lost (rolled-back or partially-failed prior run). Returns
    ``{"key": ..., "id": ...}`` on an exact-summary match or ``None``.
    """
    if not summary:
        return None

    safe_summary = _escape_jql(summary)
    safe_type = _escape_jql(issue_type_name)
    # summary ~ is a text search; we re-check for an exact match below.
    jql = (
        f'project = "{project_key}" AND issuetype = "{safe_type}" '
        f'AND summary ~ "\\"{safe_summary}\\""'
    )
    try:
        issues = await client.search_issues(jql, fields=["id", "key", "summary"], max_results=50)
        for issue in issues:
            if issue.get("fields", {}).get("summary") == summary:
                return {"key": issue["key"], "id": issue["id"]}
    except Exception as exc:
        logger.warning(
            "Summary search failed — will attempt create: summary=%s error=%s",
            summary,
            exc,
        )
    return None


# ── ADF (Atlassian Document Format) helpers ────────────────────────────────


def _build_adf_document(content_nodes: list[dict]) -> dict:
    return {"type": "doc", "version": 1, "content": content_nodes}


def _adf_paragraph(text: str, *, italic: bool = False) -> dict:
    node: dict = {"type": "text", "text": text}
    if italic:
        node["marks"] = [{"type": "em"}]
    return {"type": "paragraph", "content": [node]}


def _adf_heading(text: str, *, level: int = 3) -> dict:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [{"type": "text", "text": text}],
    }


def _adf_code_block(text: str, *, language: str = "gherkin") -> dict:
    return {
        "type": "codeBlock",
        "attrs": {"language": language},
        "content": [{"type": "text", "text": text}],
    }
