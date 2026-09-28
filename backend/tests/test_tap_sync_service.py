"""Unit tests for TapSyncService (stage+notify, pull, and inbound ack).

Neo4j repos are ``MagicMock`` with ``AsyncMock`` methods; the ``UnitOfWork`` is
a ``MagicMock``. ``TapClient`` is patched at the service import site so no real
HTTP is attempted. ``add`` side-effects assign the DB-generated ``id`` that a
real flush would populate (needed because ``sync_id == tap_sync_history.id``).
Credentials come from the project's own ``tap_integrations`` row, which
``_make_uow`` stubs — the deployment-wide ``TAP_*`` env vars are no longer a
fallback, since they are shared across tenants and would let one project push
to TAP under another's credentials. ``RIP_PUBLIC_BASE_URL`` is still patched
per-test; it only affects the informational ``pull_url``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.enums.activity_type import ActivityType
from app.core.enums.notification_type import NotificationType
from app.core.exceptions import ConflictError, NotFoundError
from app.core.messages import MSG_TAP_SYNC_NOTHING_TO_SYNC
from app.models.postgres.tap_sync_mapping_model import TapSyncMapping
from app.schemas.tap_integration_schema import (
    TapAckRequest,
    TapFeature,
    TapModule,
    TapUserStory,
)
from app.services.tap_sync_service import STALE_PENDING_SYNC_AFTER, TapSyncService


def _assign_id_on_add(entity) -> None:
    if getattr(entity, "id", None) is None:
        entity.id = uuid.uuid4()


# The real TAP deployments RIP talks to. Kept as the fixture value so the
# tests exercise the same shape of base_url production stores, not a
# placeholder host.
TAP_DEV_BASE_URL = "https://tap-dev-api.bjitgroup.com"
TAP_QA_BASE_URL = "https://tap-qa-api.bjitgroup.com"


def _make_tap_integration(base_url: str = TAP_DEV_BASE_URL) -> MagicMock:
    """A connected per-project TAP integration row.

    ``api_key_encrypted`` holds a real Fernet token so the decrypt on the sync
    path runs the same code the app does, rather than being mocked past.
    """
    from app.utils.encryption import encrypt_tap_api_key

    return MagicMock(
        base_url=base_url,
        api_key_encrypted=encrypt_tap_api_key("key-123"),
        client_id="APP-1",
        is_active=True,
    )


def _make_uow() -> MagicMock:
    uow = MagicMock()
    uow.projects.get_by_uuid.return_value = MagicMock(name="project", id=uuid.uuid4())
    uow.projects.get_by_uuid.return_value.name = "Orange HRM"
    uow.tap_integrations.get_active_by_project_id.return_value = _make_tap_integration()
    # No sync already in flight — the concurrency guard lets this run proceed.
    uow.tap_sync_history.get_pending_by_project_id.return_value = None
    uow.tap_sync_history.add = MagicMock(side_effect=_assign_id_on_add)
    uow.tap_ack_history.add = MagicMock(side_effect=_assign_id_on_add)
    uow.tap_sync_mappings.add = MagicMock()
    uow.tap_sync_mappings.get_by_rip_entity.return_value = None
    uow.flush = MagicMock()
    return uow


def _make_service() -> TapSyncService:
    module_repo = MagicMock()
    module_repo.update_module_sync_flags = AsyncMock(return_value=object())
    module_repo.update_feature_sync_flags = AsyncMock(return_value=object())
    story_repo = MagicMock()
    story_repo.update_user_story_sync_flags = AsyncMock(return_value=object())
    return TapSyncService(module_feature_repo=module_repo, user_story_repo=story_repo)


def _sample_modules() -> list[TapModule]:
    return [
        TapModule(
            module_id=uuid.uuid4(),
            module_code="1",
            module_name="Mod",
            module_description="desc",
            features=[
                TapFeature(
                    feature_id=uuid.uuid4(),
                    feature_code="1.1",
                    feature_name="Feat",
                    feature_description="fdesc",
                    user_stories=[
                        TapUserStory(
                            user_story_id=uuid.uuid4(),
                            user_story_code="1.1.1",
                            title="Story",
                            as_a="user",
                            i_want_to="do",
                            so_that="benefit",
                        )
                    ],
                )
            ],
        )
    ]


class TestExecuteSyncStage:
    async def test_stages_mappings_and_notifies_tap(self) -> None:
        uow = _make_uow()
        service = _make_service()
        project_id = uuid.uuid4()

        with (
            patch("app.services.tap_sync_service.settings.RIP_PUBLIC_BASE_URL", "https://rip.test"),
            patch("app.services.tap_sync_service.TapClient") as MockClient,
            patch("app.services.activity_log_service.record_activity") as mock_record,
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            MockClient.return_value.notify_sync_ready = AsyncMock(
                return_value={"status": "NOTIFICATION_RECEIVED"}
            )
            result = await service.execute_sync_with_hierarchy(
                project_id=project_id,
                modules=_sample_modules(),
                triggered_by_id=uuid.uuid4(),
                uow=uow,
            )

        # 1 module + 1 feature + 1 story = 3 new mappings
        assert result.created == 3
        assert result.total_synced == 3
        assert result.sync_id is not None
        assert result.pull_url is not None
        assert str(result.sync_id) in result.pull_url
        assert not result.errors
        assert uow.tap_sync_mappings.add.call_count == 3
        MockClient.return_value.notify_sync_ready.assert_awaited_once_with(
            project_name="Orange HRM",
            project_id=str(project_id),
            sync_id=str(result.sync_id),
        )
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["activity_type"] == ActivityType.TAP_SYNC_STARTED
        mock_publish.assert_called_once()
        assert mock_publish.call_args.kwargs["notification_type"] == NotificationType.INFO

    async def test_notify_failure_keeps_data_staged(self) -> None:
        uow = _make_uow()
        service = _make_service()

        with (
            patch("app.services.tap_sync_service.settings.RIP_PUBLIC_BASE_URL", "https://rip.test"),
            patch("app.services.tap_sync_service.TapClient") as MockClient,
        ):
            MockClient.return_value.notify_sync_ready = AsyncMock(
                side_effect=RuntimeError("TAP down")
            )
            result = await service.execute_sync_with_hierarchy(
                project_id=uuid.uuid4(),
                modules=_sample_modules(),
                triggered_by_id=uuid.uuid4(),
                uow=uow,
            )

        assert result.errors  # notify failure recorded
        assert result.created == 3
        assert uow.tap_sync_mappings.add.call_count == 3  # mappings still staged

    async def test_missing_public_base_url_still_notifies_tap(self) -> None:
        # RIP_PUBLIC_BASE_URL only affects the informational pull_url in our
        # own response — TAP's notify contract doesn't need it (TAP derives
        # its pull request from project_id + sync_id), so notify still
        # proceeds and succeeds normally.
        uow = _make_uow()
        service = _make_service()

        with (
            patch("app.services.tap_sync_service.settings.RIP_PUBLIC_BASE_URL", ""),
            patch("app.services.tap_sync_service.TapClient") as MockClient,
        ):
            MockClient.return_value.notify_sync_ready = AsyncMock(
                return_value={"status": "NOTIFICATION_RECEIVED"}
            )
            result = await service.execute_sync_with_hierarchy(
                project_id=uuid.uuid4(),
                modules=_sample_modules(),
                triggered_by_id=uuid.uuid4(),
                uow=uow,
            )

        assert result.pull_url is None
        assert not result.errors
        MockClient.return_value.notify_sync_ready.assert_awaited_once()
        assert result.created == 3

    async def test_project_without_a_connected_integration_raises(self) -> None:
        """No connected integration is a hard error, never a silent fallback.

        The deployment-wide TAP_* env vars are shared by every tenant, so
        syncing with them on behalf of a project that never connected would
        push that project's requirements into TAP under someone else's
        credentials.
        """
        uow = _make_uow()
        uow.tap_integrations.get_active_by_project_id.return_value = None
        service = _make_service()

        with (
            patch("app.services.tap_sync_service.TapClient") as MockClient,
            pytest.raises(NotFoundError),
        ):
            await service.execute_sync_with_hierarchy(
                project_id=uuid.uuid4(),
                modules=_sample_modules(),
                triggered_by_id=uuid.uuid4(),
                uow=uow,
            )

        MockClient.assert_not_called()

    async def test_rejects_a_second_sync_while_one_is_in_flight(self) -> None:
        """A concurrent run would steal every mapping's last_push_sync_id, so
        the first run's ack would match nothing and flip no flags — a silent
        failure. Reject instead."""
        uow = _make_uow()
        uow.tap_sync_history.get_pending_by_project_id.return_value = MagicMock(id=uuid.uuid4())
        service = _make_service()

        with (
            patch("app.services.tap_sync_service.TapClient") as MockClient,
            pytest.raises(ConflictError),
        ):
            await service.execute_sync_with_hierarchy(
                project_id=uuid.uuid4(),
                modules=_sample_modules(),
                triggered_by_id=uuid.uuid4(),
                uow=uow,
            )

        MockClient.assert_not_called()
        uow.tap_sync_history.add.assert_not_called()

    async def test_in_flight_check_is_bounded_so_a_lost_ack_cannot_block_forever(self) -> None:
        """A run only leaves pending_pull when TAP acks it. The guard must ask
        for recent runs only, or one dropped ack bricks the project."""
        uow = _make_uow()
        service = _make_service()

        with (
            patch("app.services.tap_sync_service.settings.RIP_PUBLIC_BASE_URL", "https://rip.test"),
            patch("app.services.tap_sync_service.TapClient") as MockClient,
        ):
            MockClient.return_value.notify_sync_ready = AsyncMock(
                return_value={"status": "NOTIFICATION_RECEIVED"}
            )
            await service.execute_sync_with_hierarchy(
                project_id=uuid.uuid4(),
                modules=_sample_modules(),
                triggered_by_id=uuid.uuid4(),
                uow=uow,
            )

        since = uow.tap_sync_history.get_pending_by_project_id.call_args.kwargs["since"]
        assert since is not None
        # Roughly one staleness window back, not "all time".
        expected = datetime.now(UTC) - STALE_PENDING_SYNC_AFTER
        assert abs((since - expected).total_seconds()) < 60

    async def test_nothing_to_sync_does_not_notify_tap(self) -> None:
        """Every entity unchanged → no point telling TAP to pull an empty batch."""
        uow = _make_uow()
        service = _make_service()

        with (
            patch("app.services.tap_sync_service.settings.RIP_PUBLIC_BASE_URL", "https://rip.test"),
            patch("app.services.tap_sync_service.TapClient") as MockClient,
            # Pin every entity's hash and hand back a mapping already holding
            # it, so all three entities take the "unchanged" branch.
            patch("app.services.tap_sync_service._content_hash", return_value="SAME"),
        ):
            uow.tap_sync_mappings.get_by_rip_entity.return_value = MagicMock(
                rip_content_hash="SAME"
            )
            result = await service.execute_sync_with_hierarchy(
                project_id=uuid.uuid4(),
                modules=_sample_modules(),
                triggered_by_id=uuid.uuid4(),
                uow=uow,
            )

        assert result.total_synced == 0
        assert result.created == 0 and result.updated == 0
        assert result.skipped == 3
        assert result.pull_url is None
        assert result.message == MSG_TAP_SYNC_NOTHING_TO_SYNC
        MockClient.assert_not_called()

    async def test_commits_before_telling_tap_to_pull(self) -> None:
        """TAP pulls over its own connection, so the staged run must be
        durable before the notify — otherwise TAP can 404 on a sync_id RIP
        just handed it."""
        uow = _make_uow()
        service = _make_service()
        order: list[str] = []
        uow.commit = MagicMock(side_effect=lambda: order.append("commit"))

        with (
            patch("app.services.tap_sync_service.settings.RIP_PUBLIC_BASE_URL", "https://rip.test"),
            patch("app.services.tap_sync_service.TapClient") as MockClient,
        ):
            MockClient.return_value.notify_sync_ready = AsyncMock(
                side_effect=lambda **_: order.append("notify") or {"status": "OK"}
            )
            await service.execute_sync_with_hierarchy(
                project_id=uuid.uuid4(),
                modules=_sample_modules(),
                triggered_by_id=uuid.uuid4(),
                uow=uow,
            )

        assert order.index("commit") < order.index("notify"), (
            f"expected a commit before the notify, got {order}"
        )

    async def test_sync_uses_the_projects_own_stored_credentials(self) -> None:
        """The decrypted per-project key is what reaches TapClient."""
        uow = _make_uow()
        service = _make_service()

        with (
            patch("app.services.tap_sync_service.settings.RIP_PUBLIC_BASE_URL", "https://rip.test"),
            patch("app.services.tap_sync_service.TapClient") as MockClient,
        ):
            MockClient.return_value.notify_sync_ready = AsyncMock(
                return_value={"status": "NOTIFICATION_RECEIVED"}
            )
            await service.execute_sync_with_hierarchy(
                project_id=uuid.uuid4(),
                modules=_sample_modules(),
                triggered_by_id=uuid.uuid4(),
                uow=uow,
            )

        kwargs = MockClient.call_args.kwargs
        assert kwargs["base_url"] == TAP_DEV_BASE_URL
        # Decrypted from the row, not read from settings.
        assert kwargs["auth_config"]["api_key"] == "key-123"
        assert kwargs["auth_config"]["app_client_id"] == "APP-1"


class TestNotifyTapSyncStarted:
    def _make_uow(self, *, owner_id, member_id=None):
        project = SimpleNamespace(name="Demo Project", owner_id=owner_id)
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = project
        uow.project_members.list_by_project.return_value = (
            [SimpleNamespace(user_id=member_id)] if member_id else []
        )
        return uow

    def test_notifies_owner_and_members(self) -> None:
        owner_id = uuid.uuid4()
        member_id = uuid.uuid4()
        uow = self._make_uow(owner_id=owner_id, member_id=member_id)
        project_id = uuid.uuid4()
        actor_id = uuid.uuid4()

        with (
            patch("app.services.activity_log_service.record_activity") as mock_record,
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            TapSyncService._notify_tap_sync_started(
                uow=uow, project_id=project_id, actor_user_id=actor_id
            )

        mock_record.assert_called_once_with(
            project_id=project_id,
            activity_type=ActivityType.TAP_SYNC_STARTED,
            summary="TAP Sync Started",
            message="Started syncing to TAP",
            actor_user_id=actor_id,
            data={},
        )
        notified_ids = {c.kwargs["user_id"] for c in mock_publish.call_args_list}
        assert notified_ids == {owner_id, member_id}
        for c in mock_publish.call_args_list:
            assert c.kwargs["notification_type"] == NotificationType.INFO

    def test_no_project_does_not_notify(self) -> None:
        uow = MagicMock()
        uow.projects.get_by_uuid.return_value = None

        with (
            patch("app.services.activity_log_service.record_activity") as mock_record,
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            TapSyncService._notify_tap_sync_started(
                uow=uow, project_id=uuid.uuid4(), actor_user_id=None
            )

        mock_record.assert_not_called()
        mock_publish.assert_not_called()


class TestGetSyncPayload:
    def test_returns_staged_payload_and_stamps_pulled_at(self) -> None:
        uow = _make_uow()
        service = _make_service()

        project_id = uuid.uuid4()
        sync_id = uuid.uuid4()
        record = MagicMock()
        record.id = sync_id
        record.project_id = project_id
        record.payload = {"modules": [{"module_id": str(uuid.uuid4())}]}
        record.pulled_at = None
        uow.tap_sync_history.get.return_value = record

        result = service.get_sync_payload(project_id=project_id, sync_id=sync_id, uow=uow)

        assert result.sync_id == sync_id
        assert result.modules == record.payload["modules"]
        assert record.pulled_at is not None  # stamped
        uow.flush.assert_called()

    def test_wrong_project_raises_not_found(self) -> None:
        uow = _make_uow()
        service = _make_service()

        record = MagicMock()
        record.project_id = uuid.uuid4()  # different project
        uow.tap_sync_history.get.return_value = record

        with pytest.raises(NotFoundError):
            service.get_sync_payload(project_id=uuid.uuid4(), sync_id=uuid.uuid4(), uow=uow)

    def test_missing_sync_raises_not_found(self) -> None:
        uow = _make_uow()
        service = _make_service()
        uow.tap_sync_history.get.return_value = None

        with pytest.raises(NotFoundError):
            service.get_sync_payload(project_id=uuid.uuid4(), sync_id=uuid.uuid4(), uow=uow)


class TestNotifyTapAck:
    """_notify_tap_ack runs on a threadpool thread and opens its own
    UnitOfWork (see handle_ack's call site) — patch app.services.
    tap_sync_service.UnitOfWork so it doesn't try a real DB connection."""

    def _patched_uow(self, *, owner_id):
        cm = MagicMock()
        cm.__enter__.return_value = MagicMock(
            projects=MagicMock(
                get_by_uuid=MagicMock(return_value=SimpleNamespace(owner_id=owner_id))
            )
        )
        cm.__exit__.return_value = None
        return patch("app.services.tap_sync_service.UnitOfWork", return_value=cm)

    def test_completed_records_activity_and_notifies(self) -> None:
        owner_id = uuid.uuid4()
        project_id = uuid.uuid4()
        sync_id = uuid.uuid4()

        with (
            self._patched_uow(owner_id=owner_id),
            patch("app.services.activity_log_service.record_activity") as mock_record,
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            TapSyncService._notify_tap_ack(
                project_id=project_id,
                status="COMPLETED",
                message="Acknowledged job tap-job-1; 2 entities marked synced.",
                entities_synced=2,
                job_id="tap-job-1",
                sync_id=sync_id,
            )

        mock_record.assert_called_once_with(
            project_id=project_id,
            activity_type=ActivityType.TAP_SYNC_COMPLETED,
            summary="TAP Sync Completed",
            message="TAP acknowledged sync: 2 item(s) marked synced",
            actor_user_id=None,
            data={"sync_id": str(sync_id), "job_id": "tap-job-1", "entities_synced": 2},
        )
        mock_publish.assert_called_once_with(
            user_id=owner_id,
            title="TAP Sync Acknowledged",
            message="Acknowledged job tap-job-1; 2 entities marked synced.",
            notification_type=NotificationType.SUCCESS,
            data={
                "project_id": str(project_id),
                "sync_id": str(sync_id),
                "job_id": "tap-job-1",
                "status": "COMPLETED",
                "entities_synced": 2,
            },
        )

    def test_failed_records_activity_and_notifies(self) -> None:
        owner_id = uuid.uuid4()
        project_id = uuid.uuid4()
        sync_id = uuid.uuid4()

        with (
            self._patched_uow(owner_id=owner_id),
            patch("app.services.activity_log_service.record_activity") as mock_record,
            patch("app.services.notification_service.publish_notification") as mock_publish,
        ):
            TapSyncService._notify_tap_ack(
                project_id=project_id,
                status="FAILED",
                message="Recorded failure for job tap-job-2: bad payload.",
                entities_synced=0,
                job_id="tap-job-2",
                sync_id=sync_id,
            )

        mock_record.assert_called_once_with(
            project_id=project_id,
            activity_type=ActivityType.TAP_SYNC_FAILED,
            summary="TAP Sync Failed",
            message="TAP sync failed: Recorded failure for job tap-job-2: bad payload.",
            actor_user_id=None,
            data={
                "sync_id": str(sync_id),
                "job_id": "tap-job-2",
                "error": "Recorded failure for job tap-job-2: bad payload.",
            },
        )
        assert mock_publish.call_args.kwargs["notification_type"] == NotificationType.ERROR


class TestHandleAck:
    async def test_completed_flips_mappings_in_processed_ids(self) -> None:
        uow = _make_uow()
        service = _make_service()

        sync_id = uuid.uuid4()
        story_mapping = TapSyncMapping()
        story_mapping.rip_entity_type = "user_story"
        story_mapping.rip_entity_id = uuid.uuid4()
        story_mapping.sync_status = "pending_ack"
        uow.tap_sync_mappings.list_by_push_sync_id.return_value = [story_mapping]
        uow.tap_sync_history.get.return_value = MagicMock(status="pending_pull")

        request = TapAckRequest(
            job_id="tap-job-abc",
            order_index=1,
            status="COMPLETED",
            total_requirements=1,
            processed_requirements=1,
            failed_requirements=0,
            total_requirement_ids=[story_mapping.rip_entity_id],
            processed_requirement_ids=[story_mapping.rip_entity_id],
        )

        result = await service.handle_ack(
            project_id=uuid.uuid4(), sync_id=sync_id, request=request, uow=uow
        )

        assert result.status == "COMPLETED"
        assert result.entities_synced == 1
        assert story_mapping.sync_status == "acked"
        uow.tap_sync_mappings.list_by_push_sync_id.assert_called_once_with(sync_id)
        service._user_story_repo.update_user_story_sync_flags.assert_awaited_once_with(
            str(story_mapping.rip_entity_id), is_tap_synced=True
        )
        uow.tap_ack_history.add.assert_called_once()

    async def test_completed_does_not_flip_mappings_outside_processed_ids(self) -> None:
        uow = _make_uow()
        service = _make_service()

        sync_id = uuid.uuid4()
        story_mapping = TapSyncMapping()
        story_mapping.rip_entity_type = "user_story"
        story_mapping.rip_entity_id = uuid.uuid4()
        story_mapping.sync_status = "pending_ack"
        other_mapping = TapSyncMapping()
        other_mapping.rip_entity_type = "user_story"
        other_mapping.rip_entity_id = uuid.uuid4()
        other_mapping.sync_status = "pending_ack"
        uow.tap_sync_mappings.list_by_push_sync_id.return_value = [
            story_mapping,
            other_mapping,
        ]
        uow.tap_sync_history.get.return_value = MagicMock(status="pending_pull")

        # Simulates a paginated ack: only one entity was processed in this
        # particular order_index batch, the other is still pending.
        request = TapAckRequest(
            job_id="tap-job-abc",
            order_index=1,
            status="COMPLETED",
            total_requirements=2,
            processed_requirements=1,
            failed_requirements=0,
            total_requirement_ids=[story_mapping.rip_entity_id, other_mapping.rip_entity_id],
            processed_requirement_ids=[story_mapping.rip_entity_id],
        )

        result = await service.handle_ack(
            project_id=uuid.uuid4(), sync_id=sync_id, request=request, uow=uow
        )

        assert result.entities_synced == 1
        assert story_mapping.sync_status == "acked"
        assert other_mapping.sync_status == "pending_ack"

    async def test_failed_status_flips_nothing(self) -> None:
        uow = _make_uow()
        service = _make_service()

        sync_id = uuid.uuid4()
        request = TapAckRequest(
            job_id="tap-job-abc",
            order_index=1,
            status="FAILED",
            total_requirements=5,
            processed_requirements=2,
            failed_requirements=3,
            error_details="TAP validation error",
        )

        result = await service.handle_ack(
            project_id=uuid.uuid4(), sync_id=sync_id, request=request, uow=uow
        )

        assert result.status == "FAILED"
        assert result.entities_synced == 0
        uow.tap_sync_mappings.list_by_push_sync_id.assert_not_called()
        uow.tap_ack_history.add.assert_called_once()

    async def test_completed_with_no_processed_ids_skips_mapping_lookup(self) -> None:
        uow = _make_uow()
        service = _make_service()

        sync_id = uuid.uuid4()
        request = TapAckRequest(
            job_id="tap-job-abc",
            status="COMPLETED",
            total_requirements=1,
            processed_requirements=0,
            failed_requirements=1,
            total_requirement_ids=[uuid.uuid4()],
            failed_requirement_ids=[uuid.uuid4()],
        )

        result = await service.handle_ack(
            project_id=uuid.uuid4(), sync_id=sync_id, request=request, uow=uow
        )

        assert result.entities_synced == 0
        uow.tap_sync_mappings.list_by_push_sync_id.assert_not_called()
        uow.tap_ack_history.add.assert_called_once()

    async def test_completed_with_unmatched_processed_id_records_ack_without_flipping(
        self,
    ) -> None:
        uow = _make_uow()
        service = _make_service()
        uow.tap_sync_mappings.list_by_push_sync_id.return_value = []
        uow.tap_sync_history.get.return_value = None

        sync_id = uuid.uuid4()
        request = TapAckRequest(
            job_id="tap-job-abc",
            status="COMPLETED",
            total_requirements=1,
            processed_requirements=1,
            failed_requirements=0,
            processed_requirement_ids=[uuid.uuid4()],
        )

        result = await service.handle_ack(
            project_id=uuid.uuid4(), sync_id=sync_id, request=request, uow=uow
        )

        assert result.entities_synced == 0
        uow.tap_sync_mappings.list_by_push_sync_id.assert_called_once_with(sync_id)
        uow.tap_ack_history.add.assert_called_once()
