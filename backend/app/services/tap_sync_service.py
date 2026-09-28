"""Service for TAP sync orchestration: stage, notify, pull, and ack.

RIP never pushes the hierarchy directly into a TAP request — that would couple
every sync to TAP's uptime and request-body contract. Instead the flow is
stage → notify → pull → ack, fully decoupled:

* **stage** (:meth:`execute_sync_with_hierarchy`) assigns a RIP-generated
  ``sync_id`` (the ``tap_sync_history`` row id), stores the hierarchy snapshot
  in that row's ``payload``, and records one ``tap_sync_mapping`` per entity
  with ``sync_status="pending_ack"``. It does **not** flip ``is_tap_synced`` —
  an item is only "TAP-synced" once TAP acknowledges it.

* **notify** is a lightweight, best-effort ping (``sync_id`` + a pull-back URL
  + project identification) sent as part of staging. A failed ping never
  discards the staged data or the mapping rows — it's only reflected in the
  history status (``"notify_failed"``) so staging can be retried or TAP can be
  told out-of-band.

* **pull** (:meth:`get_sync_payload`) is the inbound half TAP calls (via the
  ``pull_url`` it was given) to fetch the staged hierarchy whenever it is
  ready — not on RIP's schedule.

* **ack** (:meth:`handle_ack`) is the inbound job-status callback TAP calls
  once it has processed the data, flipping ``is_tap_synced`` on every entity
  that was part of the acknowledged sync run.

Every TAP detail — base URL, API key, app-client id — is per project, read
from that project's ``tap_integrations`` row and decrypted at call time. The
deployment-wide ``TAP_*`` settings are deliberately NOT a fallback: they are
shared across tenants, so using them for a project that never connected would
push its requirements into TAP under another project's credentials. A project
with no connected integration is an error, not a candidate for defaults.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from uuid import UUID

from starlette.concurrency import run_in_threadpool

from app.clients.tap_client import TapClient
from app.core.config import settings
from app.core.enums.activity_type import ActivityType
from app.core.exceptions import ConflictError, NotFoundError
from app.core.messages import (
    MSG_ACTIVITY_TAP_SYNC_COMPLETED,
    MSG_ACTIVITY_TAP_SYNC_FAILED,
    MSG_ACTIVITY_TAP_SYNC_STARTED,
    MSG_TAP_NOT_CONFIGURED,
    MSG_TAP_SYNC_ALREADY_RUNNING,
    MSG_TAP_SYNC_NOTHING_TO_SYNC,
    SUMMARY_ACTIVITY_TAP_SYNC_COMPLETED,
    SUMMARY_ACTIVITY_TAP_SYNC_FAILED,
    SUMMARY_ACTIVITY_TAP_SYNC_STARTED,
)
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.tap_ack_history_model import TapAckHistory
from app.models.postgres.tap_sync_history_model import TapSyncHistory
from app.models.postgres.tap_sync_mapping_model import TapSyncMapping
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.schemas.tap_integration_schema import (
    TapAckRequest,
    TapAckResponse,
    TapSyncDataResponse,
    TapSyncExecuteResponse,
    TapSyncResultItem,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

# How long a run may sit in ``pending_pull`` before a new sync is allowed
# anyway. TAP pulls and acks on its own schedule, so this has to be generous;
# it exists only so a dropped ack cannot block the project forever.
STALE_PENDING_SYNC_AFTER = timedelta(hours=1)


class TapSyncService:
    def __init__(
        self,
        module_feature_repo: ModuleFeatureRepository,
        user_story_repo: UserStoryRepository,
    ) -> None:
        self._module_feature_repo = module_feature_repo
        self._user_story_repo = user_story_repo

    @staticmethod
    def _notify_tap_sync_started(
        *, uow: UnitOfWork, project_id: UUID, actor_user_id: UUID | None
    ) -> None:
        """Record an activity-log entry and notify the project owner and every
        assigned member that a TAP sync has been staged and TAP notified to pull it.

        Never raises — a notification/activity-log failure must not fail an
        already-successful staging step.
        """
        from app.core.enums.notification_type import NotificationType  # noqa: PLC0415
        from app.services.activity_log_service import record_activity  # noqa: PLC0415
        from app.services.notification_service import publish_notification  # noqa: PLC0415

        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            return

        record_activity(
            project_id=project_id,
            activity_type=ActivityType.TAP_SYNC_STARTED,
            summary=SUMMARY_ACTIVITY_TAP_SYNC_STARTED,
            message=MSG_ACTIVITY_TAP_SYNC_STARTED,
            actor_user_id=actor_user_id,
            data={},
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
                    title=SUMMARY_ACTIVITY_TAP_SYNC_STARTED,
                    message=f'{MSG_ACTIVITY_TAP_SYNC_STARTED} in "{project.name}".',
                    notification_type=NotificationType.INFO,
                    data={"project_id": str(project_id)},
                )
            except Exception:
                logger.warning(
                    "_notify_tap_sync_started: failed to notify project_id=%s user_id=%s",
                    project_id,
                    recipient_id,
                    exc_info=True,
                )

    # ── Stage + notify ─────────────────────────────────────────────────────

    async def execute_sync_with_hierarchy(
        self,
        project_id: UUID,
        modules: list,
        triggered_by_id: UUID,
        uow: UnitOfWork,
    ) -> TapSyncExecuteResponse:
        # Credentials come from the project's own verified integration row —
        # never from the deployment-wide TAP_* env vars. Those are shared
        # across every tenant, so falling back to them would let one project
        # push its requirements into TAP under another project's credentials.
        # No connected integration is a hard error, not a reason to guess.
        from app.utils.encryption import decrypt_tap_api_key  # noqa: PLC0415

        tap_config = uow.tap_integrations.get_active_by_project_id(project_id)
        if not tap_config:
            raise NotFoundError(MSG_TAP_NOT_CONFIGURED)

        tap_base_url = tap_config.base_url
        tap_api_key = decrypt_tap_api_key(tap_config.api_key_encrypted)
        tap_client_id = tap_config.client_id

        # Only one sync may be in flight per project. Every mapping stores a
        # single last_push_sync_id, so a second run reassigns all of them and
        # the first run's ack would then match nothing and flip no flags —
        # failing silently, which is the worst way for this to break.
        #
        # Bounded by STALE_PENDING_SYNC_AFTER: a run only leaves pending_pull
        # when TAP acks it, so an ack that never arrives must not lock the
        # project out permanently.
        in_flight = uow.tap_sync_history.get_pending_by_project_id(
            project_id,
            since=datetime.now(UTC) - STALE_PENDING_SYNC_AFTER,
        )
        if in_flight is not None:
            raise ConflictError(MSG_TAP_SYNC_ALREADY_RUNNING.format(sync_id=in_flight.id))

        project = uow.projects.get_by_uuid(project_id)
        project_name = project.name if project else str(project_id)

        serialized_modules = _serialize_modules(modules)

        # The history row's id doubles as the sync_id TAP echoes back on ack
        # (as job_id).
        history = TapSyncHistory(
            project_id=project_id,
            triggered_by_id=triggered_by_id,
            status="pending_pull",
            payload={"modules": serialized_modules},
        )
        uow.tap_sync_history.add(history)
        uow.flush()
        sync_id = history.id

        counters = {"created": 0, "updated": 0, "skipped": 0}
        created_items: list[TapSyncResultItem] = []
        updated_items: list[TapSyncResultItem] = []
        released_ids: list[str] = []

        # ── Record a mapping per entity (status = pending_ack) ──────────────
        for module in modules:
            self._upsert_mapping(
                uow,
                "module",
                module.module_id,
                module.module_code,
                module.module_name,
                None,
                _content_hash("module", module),
                sync_id,
                counters,
                created_items,
                updated_items,
            )
            released_ids.append(str(module.module_id))

            for feature in getattr(module, "features", []) or []:
                self._upsert_mapping(
                    uow,
                    "feature",
                    feature.feature_id,
                    feature.feature_code,
                    feature.feature_name,
                    module.module_id,
                    _content_hash("feature", feature),
                    sync_id,
                    counters,
                    created_items,
                    updated_items,
                )
                released_ids.append(str(feature.feature_id))

                for story in getattr(feature, "user_stories", []) or []:
                    self._upsert_mapping(
                        uow,
                        "user_story",
                        story.user_story_id,
                        story.user_story_code,
                        story.title,
                        feature.feature_id,
                        _content_hash("user_story", story),
                        sync_id,
                        counters,
                        created_items,
                        updated_items,
                    )
                    released_ids.append(str(story.user_story_id))

        total_synced = counters["created"] + counters["updated"]

        # ── Nothing changed since the last sync ─────────────────────────────
        # Every entity hashed identically, so there is nothing for TAP to
        # pull. Record the attempt for audit, but do not ping TAP to come
        # fetch an empty batch, and tell the caller plainly rather than
        # reporting a successful sync of zero items.
        if total_synced == 0:
            history.status = "no_changes"
            history.summary = counters
            history.items_released = released_ids
            history.completed_at = datetime.now(UTC)
            uow.commit()
            logger.info(
                "TAP sync skipped — nothing changed: project_id=%s sync_id=%s skipped=%s",
                project_id,
                sync_id,
                counters["skipped"],
            )
            return TapSyncExecuteResponse(
                sync_id=sync_id,
                pull_url=None,
                created=0,
                updated=0,
                skipped=counters["skipped"],
                total_synced=0,
                message=MSG_TAP_SYNC_NOTHING_TO_SYNC,
                created_items=[],
                updated_items=[],
                errors=[],
                sync_completed_at=history.completed_at,
            )

        # ── Commit BEFORE announcing the sync to TAP ────────────────────────
        # TAP pulls on its own schedule and over its own connection, so it can
        # call GET /sync/{sync_id}/data before this request finishes. Anything
        # still sitting in this open transaction is invisible to that read —
        # TAP would get a 404 for a sync_id RIP just handed it — and a later
        # failure here would roll the run back entirely, leaving TAP chasing a
        # sync that never existed. This is the explicit rule in
        # UnitOfWork's docstring: commit before calling an external system
        # that depends on the data being durable.
        uow.commit()

        self._notify_tap_sync_started(uow=uow, project_id=project_id, actor_user_id=triggered_by_id)

        # ── Notify TAP with a lightweight ping (best-effort) ────────────────
        # A failed ping never discards the staged payload/mappings — TAP can
        # still be told out-of-band, or the button clicked again, without
        # losing anything already recorded. ``pull_url`` is informational for
        # our own response/logs only — TAP's notify contract doesn't take it;
        # TAP derives the pull request itself from project_id + sync_id.
        pull_url = _build_pull_url(project_id, sync_id)
        notify_error: str | None = None
        client = TapClient(
            base_url=tap_base_url,
            auth_config={
                "api_key": tap_api_key,
                "app_client_id": tap_client_id,
            },
        )
        try:
            await client.notify_sync_ready(
                project_name=project_name,
                project_id=str(project_id),
                sync_id=str(sync_id),
            )
        except Exception as exc:  # noqa: BLE001 — surfaced via history status
            notify_error = str(exc)[:500]
            logger.warning(
                "TAP notify failed (data still staged for pull): project_id=%s sync_id=%s error=%s",
                project_id,
                sync_id,
                exc,
            )

        # ── Finalize ────────────────────────────────────────────────────────
        history.status = "notify_failed" if notify_error else "pending_pull"
        history.summary = counters
        history.items_released = released_ids
        history.error_details = (
            [{"stage": "notify", "error": notify_error}] if notify_error else None
        )
        history.completed_at = datetime.now(UTC)
        uow.commit()

        logger.info(
            "TAP sync staged: project_id=%s sync_id=%s created=%s updated=%s skipped=%s notified=%s",
            project_id,
            sync_id,
            counters["created"],
            counters["updated"],
            counters["skipped"],
            notify_error is None,
        )

        if notify_error is None:
            message = (
                f"Staged {total_synced} item(s) for TAP (sync_id={sync_id}); TAP notified to pull."
            )
        else:
            message = (
                f"Staged {total_synced} item(s) for TAP (sync_id={sync_id}); "
                f"notify failed ({notify_error}), data remains available for pull."
            )

        return TapSyncExecuteResponse(
            sync_id=sync_id,
            pull_url=pull_url,
            created=counters["created"],
            updated=counters["updated"],
            skipped=counters["skipped"],
            total_synced=total_synced,
            message=message,
            created_items=created_items,
            updated_items=updated_items,
            errors=[{"stage": "notify", "error": notify_error}] if notify_error else [],
            sync_completed_at=datetime.now(UTC),
        )

    # ── Pull (inbound TAP → RIP) ────────────────────────────────────────────

    def get_sync_payload(
        self, project_id: UUID, sync_id: UUID, uow: UnitOfWork
    ) -> TapSyncDataResponse:
        """Serve the staged hierarchy for ``sync_id`` to TAP.

        TAP builds this request itself from the ``project_id``/``sync_id`` it
        received in the notify ping, and calls it on its own schedule, not
        RIP's. Stamps ``pulled_at`` each call (informational only; safe to
        call more than once).
        """
        record = uow.tap_sync_history.get(sync_id)
        if not record or record.project_id != project_id:
            raise NotFoundError(f"TAP sync {sync_id} not found for project {project_id}.")

        record.pulled_at = datetime.now(UTC)
        uow.flush()

        payload = record.payload or {}
        return TapSyncDataResponse(
            sync_id=record.id,
            modules=payload.get("modules", []),
        )

    # ── Inbound acknowledgement (TAP → RIP) ────────────────────────────────

    async def handle_ack(
        self,
        project_id: UUID,
        sync_id: UUID,
        request: TapAckRequest,
        uow: UnitOfWork,
    ) -> TapAckResponse:
        """Process an inbound TAP job-status acknowledgement.

        ``sync_id`` (from the URL path) correlates this ack back to the sync
        run RIP staged on the originating notify/pull_url; ``job_id`` is
        TAP's own job identifier, kept for audit/logging only. When
        ``status == "COMPLETED"``, every mapping staged under that sync run
        whose ``rip_entity_id`` is in ``request.processed_requirement_ids``
        has its Neo4j ``is_tap_synced`` flag flipped true — this lets one
        sync job be acknowledged across multiple ``order_index``-paginated
        calls, each reporting only the entities it actually processed.
        ``failed_requirement_ids`` is recorded for audit only; a
        ``"FAILED"`` status flips nothing. Either way one
        ``tap_ack_history`` row is written.
        """
        entities_synced = 0
        acked_entities: list[dict] = []
        processed_ids = set(request.processed_requirement_ids)

        if request.status == "COMPLETED" and processed_ids:
            mappings = uow.tap_sync_mappings.list_by_push_sync_id(sync_id)
            for mapping in mappings:
                if mapping.rip_entity_id not in processed_ids:
                    continue
                try:
                    flipped = await self._flip_tap_flag(
                        project_id, mapping.rip_entity_type, mapping.rip_entity_id, mapping
                    )
                    if flipped:
                        entities_synced += 1
                        acked_entities.append(
                            {
                                "rip_entity_type": mapping.rip_entity_type,
                                "rip_entity_id": str(mapping.rip_entity_id),
                            }
                        )
                        mapping.sync_status = "acked"
                        mapping.acked_at = datetime.now(UTC)
                except Exception as exc:  # noqa: BLE001 — per-entity, never abort the ack
                    logger.warning(
                        "TAP ack flag flip failed: entity=%s id=%s error=%s",
                        mapping.rip_entity_type,
                        mapping.rip_entity_id,
                        exc,
                    )

        history = uow.tap_sync_history.get(sync_id)
        if history is not None:
            history.status = "acked" if request.status == "COMPLETED" else "ack_failed"

        ack = TapAckHistory(
            project_id=project_id,
            sync_id=sync_id,
            acked_entities=acked_entities or None,
            summary={
                "sync_id": str(sync_id),
                "job_id": request.job_id,
                "order_index": request.order_index,
                "status": request.status,
                "total_requirements": request.total_requirements,
                "processed_requirements": request.processed_requirements,
                "failed_requirements": request.failed_requirements,
                "total_requirement_ids": [str(i) for i in request.total_requirement_ids],
                "processed_requirement_ids": [str(i) for i in request.processed_requirement_ids],
                "failed_requirement_ids": [str(i) for i in request.failed_requirement_ids],
            },
            error_details=[{"error": request.error_details}] if request.error_details else None,
            source="tap",
        )
        uow.tap_ack_history.add(ack)
        uow.flush()

        logger.info(
            "TAP ack processed: project_id=%s sync_id=%s job_id=%s status=%s entities_synced=%s",
            project_id,
            sync_id,
            request.job_id,
            request.status,
            entities_synced,
        )

        if request.status == "COMPLETED":
            message = f"Acknowledged job {request.job_id}; {entities_synced} entit{'y' if entities_synced == 1 else 'ies'} marked synced."
        else:
            message = f"Recorded failure for job {request.job_id}: {request.error_details or 'no details provided'}."

        # handle_ack runs directly on FastAPI's event loop (it's an async
        # route handler, not a sync one offloaded to a worker thread), so
        # _notify_tap_ack — which calls the sync publish_notification/
        # publish_threadsafe path — must be dispatched to the threadpool.
        # publish_threadsafe assumes its sync caller is already running on a
        # different thread from the loop it schedules onto; calling it
        # directly here would either deadlock or hit "asyncio.run() cannot
        # be called from a running event loop".
        await run_in_threadpool(
            self._notify_tap_ack,
            project_id=project_id,
            status=request.status,
            message=message,
            entities_synced=entities_synced,
            job_id=request.job_id,
            sync_id=sync_id,
        )

        return TapAckResponse(
            ack_history_id=ack.id,
            job_id=request.job_id,
            status=request.status,
            entities_synced=entities_synced,
            message=message,
        )

    @staticmethod
    def _notify_tap_ack(
        *,
        project_id: UUID,
        status: str,
        message: str,
        entities_synced: int,
        job_id: str,
        sync_id: UUID,
    ) -> None:
        """Best-effort: notify the project owner when TAP acknowledges a sync job.

        Mirrors ``_notify_module_feature_status``/``_notify_user_story_status``
        in ``app.workers.document_task_stages`` — surfaces the same response
        TAP just received (job id, status, entities synced) as a notification
        instead of only updating counts silently. A notification failure must
        never fail the ack itself, so this never raises.

        Runs on a threadpool worker thread (see the call site in
        ``handle_ack``), so it opens its own ``UnitOfWork`` rather than
        reusing the caller's — the same pattern ``source_ws.py`` uses for
        sync DB reads dispatched from an async handler.
        """
        from app.core.enums.notification_type import NotificationType  # noqa: PLC0415
        from app.services.activity_log_service import record_activity  # noqa: PLC0415
        from app.services.notification_service import publish_notification  # noqa: PLC0415

        try:
            with UnitOfWork() as uow:
                project = uow.projects.get_by_uuid(project_id)
            if project is None or project.owner_id is None:
                return

            if status == "COMPLETED":
                title = "TAP Sync Acknowledged"
                notification_type = NotificationType.SUCCESS
                record_activity(
                    project_id=project_id,
                    activity_type=ActivityType.TAP_SYNC_COMPLETED,
                    summary=SUMMARY_ACTIVITY_TAP_SYNC_COMPLETED,
                    message=MSG_ACTIVITY_TAP_SYNC_COMPLETED.format(entities_synced=entities_synced),
                    actor_user_id=None,
                    data={
                        "sync_id": str(sync_id),
                        "job_id": job_id,
                        "entities_synced": entities_synced,
                    },
                )
            else:
                title = "TAP Sync Failed"
                notification_type = NotificationType.ERROR
                record_activity(
                    project_id=project_id,
                    activity_type=ActivityType.TAP_SYNC_FAILED,
                    summary=SUMMARY_ACTIVITY_TAP_SYNC_FAILED,
                    message=MSG_ACTIVITY_TAP_SYNC_FAILED.format(error=message[:200]),
                    actor_user_id=None,
                    data={"sync_id": str(sync_id), "job_id": job_id, "error": message},
                )

            publish_notification(
                user_id=project.owner_id,
                title=title,
                message=message,
                notification_type=notification_type,
                data={
                    "project_id": str(project_id),
                    "sync_id": str(sync_id),
                    "job_id": job_id,
                    "status": status,
                    "entities_synced": entities_synced,
                },
            )
        except Exception:
            logger.warning(
                "_notify_tap_ack: failed to notify project_id=%s sync_id=%s job_id=%s",
                project_id,
                sync_id,
                job_id,
                exc_info=True,
            )

    async def _flip_tap_flag(
        self,
        project_id: UUID,
        entity_type: str,
        entity_id: UUID,
        mapping: TapSyncMapping | None,
    ) -> bool:
        """Set ``is_tap_synced=True`` on the RIP Neo4j node. Returns False when
        the node (or, for a feature, its parent module) cannot be resolved."""
        if entity_type == "user_story":
            updated = await self._user_story_repo.update_user_story_sync_flags(
                str(entity_id), is_tap_synced=True
            )
            return updated is not None
        if entity_type == "module":
            updated = await self._module_feature_repo.update_module_sync_flags(
                project_id, str(entity_id), is_tap_synced=True
            )
            return updated is not None
        if entity_type == "feature":
            # update_feature_sync_flags needs the parent module_id, carried on
            # the mapping (rip_parent_id) from the originating push.
            module_id = mapping.rip_parent_id if mapping else None
            if module_id is None:
                return False
            updated = await self._module_feature_repo.update_feature_sync_flags(
                project_id, str(module_id), str(entity_id), is_tap_synced=True
            )
            return updated is not None
        return False

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _upsert_mapping(
        uow: UnitOfWork,
        entity_type: str,
        entity_id: UUID,
        entity_code: str | None,
        title: str,
        parent_id: UUID | None,
        content_hash: str,
        sync_id: UUID,
        counters: dict,
        created_items: list,
        updated_items: list,
    ) -> None:
        """Create or update the mapping for one entity (status = pending_ack).

        Unchanged content (same hash) is skipped and its ack status left
        as-is. The RIP → Neo4j flag is left untouched here regardless — it
        flips only when TAP acknowledges (see handle_ack)."""
        existing = uow.tap_sync_mappings.get_by_rip_entity(
            entity_type=entity_type,
            entity_id=entity_id,
        )

        if existing is None:
            mapping = TapSyncMapping(
                rip_entity_type=entity_type,
                rip_entity_id=entity_id,
                rip_entity_code=entity_code,
                rip_parent_id=parent_id,
                rip_content_hash=content_hash,
                rip_version=1,
                last_push_sync_id=sync_id,
                sync_status="pending_ack",
            )
            uow.tap_sync_mappings.add(mapping)
            counters["created"] += 1
            created_items.append(
                _result_item(entity_id, entity_type, entity_code, title, "created")
            )
        elif existing.rip_content_hash != content_hash:
            existing.rip_content_hash = content_hash
            existing.rip_parent_id = parent_id
            existing.last_push_sync_id = sync_id
            existing.sync_status = "pending_ack"  # content changed → needs a fresh ack
            existing.last_synced_at = datetime.now(UTC)
            counters["updated"] += 1
            updated_items.append(
                _result_item(entity_id, entity_type, entity_code, title, "updated")
            )
        else:
            # Content unchanged, but still re-associate with this sync so an ack
            # referencing this sync_id can find it.
            existing.rip_parent_id = parent_id
            existing.last_push_sync_id = sync_id
            counters["skipped"] += 1


# ── Module-level helpers ─────────────────────────────────────────────────────


def _build_pull_url(project_id: UUID, sync_id: UUID) -> str | None:
    """Build the URL TAP calls back to fetch a staged sync's payload.

    Returns ``None`` when ``RIP_PUBLIC_BASE_URL`` is unset — staging still
    succeeds in that case, it just can't be announced to TAP yet.
    """
    if not settings.RIP_PUBLIC_BASE_URL:
        return None
    base = settings.RIP_PUBLIC_BASE_URL.rstrip("/")
    return f"{base}/api/v1/projects/{project_id}/integrations/tap/sync/{sync_id}/data"


def _result_item(entity_id, entity_type, entity_code, title, status) -> TapSyncResultItem:
    return TapSyncResultItem(
        rip_entity_id=entity_id,
        rip_entity_type=entity_type,
        rip_entity_code=entity_code or "",
        title=title,
        status=status,
    )


def _serialize_modules(modules: list) -> list[dict]:
    """Serialize the payload hierarchy to plain JSON-able dicts for TAP."""
    return [_pyd_dump(m) for m in modules]


def _pyd_dump(model: object) -> dict:
    """Dump a pydantic model (or already-dict) to a JSON-safe dict."""
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return dict(model) if isinstance(model, dict) else {}


def _content_hash(entity_type: str, entity: object) -> str:
    """SHA-256 of the RIP-owned fields, for change detection across re-pushes."""
    if entity_type == "module":
        data = {
            "name": getattr(entity, "module_name", ""),
            "description": getattr(entity, "module_description", ""),
        }
    elif entity_type == "feature":
        data = {
            "name": getattr(entity, "feature_name", ""),
            "description": getattr(entity, "feature_description", ""),
        }
    else:  # user_story
        criteria = getattr(entity, "acceptance_criteria", []) or []
        data = {
            "title": getattr(entity, "title", ""),
            "as_a": getattr(entity, "as_a", ""),
            "i_want_to": getattr(entity, "i_want_to", ""),
            "so_that": getattr(entity, "so_that", ""),
            "acceptance_criteria": [_ac_to_dict(ac) for ac in criteria],
        }
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _ac_to_dict(ac: object) -> dict:
    if isinstance(ac, dict):
        return {k: ac.get(k, "") for k in ("type", "given", "when", "then")}
    return {k: getattr(ac, k, "") for k in ("type", "given", "when", "then")}
