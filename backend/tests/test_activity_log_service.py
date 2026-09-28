"""Unit tests for ActivityLogService, record_activity, and resolve_actor_from_task.

Strategy:
- ``list_activities`` receives its ``UnitOfWork`` from the caller (request
  path), so it's tested with a plain mocked ``uow`` — no patching needed.
- ``record_activity``/``resolve_actor_from_task`` open their own
  ``UnitOfWork()`` internally, so ``UnitOfWork`` is patched at
  ``app.services.activity_log_service.UnitOfWork`` to return a mock context
  manager, mirroring ``tests/test_notification_service.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import ANY, MagicMock, patch
import uuid

from app.core.constants import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.core.enums.activity_type import ActivityType
from app.core.exceptions import NotFoundError
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.activity_log_model import ActivityLog
from app.services.activity_log_service import (
    ActivityLogService,
    record_activity,
    resolve_actor_from_task,
)
from tests.conftest import make_project, make_user


def _make_activity_log(
    *,
    project_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    activity_type: str = "rfp_modules_generated",
    summary: str = "Summary",
    message: str = "Message",
    data: dict | None = None,
) -> ActivityLog:
    a = ActivityLog()
    a.id = uuid.uuid4()
    a.project_id = project_id or uuid.uuid4()
    a.actor_user_id = actor_user_id
    a.activity_type = activity_type
    a.summary = summary
    a.message = message
    a.data = data
    a.created_at = datetime.now(tz=UTC)
    return a


def _make_uow() -> MagicMock:
    uow = MagicMock(spec=UnitOfWork)
    uow.activity_logs = MagicMock()
    uow.project_tasks = MagicMock()
    uow.projects = MagicMock()
    uow.users = MagicMock()
    uow.users.get_by_ids.return_value = []
    uow.project_members = MagicMock()
    uow.project_members.list_by_project.return_value = []
    uow.commit = MagicMock()
    return uow


def _make_uow_context_manager(uow: MagicMock) -> MagicMock:
    ctor = MagicMock(return_value=uow)
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    return ctor


class TestListActivities:
    def test_returns_paginated_feed(self) -> None:
        uow = _make_uow()
        uow.projects.get_by_uuid.return_value = MagicMock()
        project_id = uuid.uuid4()
        items = [_make_activity_log(project_id=project_id)]
        uow.activity_logs.list_by_project.return_value = (items, 1)

        result = ActivityLogService().list_activities(project_id, skip=0, limit=20, uow=uow)

        assert result.total == 1
        assert len(result.items) == 1
        uow.activity_logs.list_by_project.assert_called_once_with(
            project_id, skip=0, limit=20, activity_type=None
        )

    def test_forwards_activity_type_filter(self) -> None:
        uow = _make_uow()
        uow.projects.get_by_uuid.return_value = MagicMock()
        project_id = uuid.uuid4()
        uow.activity_logs.list_by_project.return_value = ([], 0)

        ActivityLogService().list_activities(
            project_id, activity_type=ActivityType.INCREMENTAL_CHANGE_ACCEPTED, uow=uow
        )

        uow.activity_logs.list_by_project.assert_called_once_with(
            project_id, skip=0, limit=20, activity_type="incremental_change_accepted"
        )

    def test_raises_not_found_when_project_missing(self) -> None:
        uow = _make_uow()
        uow.projects.get_by_uuid.return_value = None

        try:
            ActivityLogService().list_activities(uuid.uuid4(), uow=uow)
            raise AssertionError("expected NotFoundError")
        except NotFoundError:
            pass
        uow.activity_logs.list_by_project.assert_not_called()

    def test_enriches_owner_actor_with_name_and_tenant_role(self) -> None:
        owner_role = MagicMock()
        owner_role.name = ROLE_ADMIN
        owner = make_user(name="Alice Owner", roles=[owner_role])
        project = make_project(owner_id=owner.id)
        uow = _make_uow()
        uow.projects.get_by_uuid.return_value = project
        uow.users.get_by_ids.return_value = [owner]
        activity = _make_activity_log(project_id=project.id, actor_user_id=owner.id)
        uow.activity_logs.list_by_project.return_value = ([activity], 1)

        result = ActivityLogService().list_activities(project.id, uow=uow)

        actor = result.items[0].actor
        assert actor.id == owner.id
        assert actor.name == "Alice Owner"
        assert actor.role == ROLE_ADMIN
        uow.users.get_by_ids.assert_called_once_with([owner.id])

    def test_enriches_non_owner_actor_with_project_member_role(self) -> None:
        member = make_user(name="Bob Member", roles=[])
        project = make_project()
        uow = _make_uow()
        uow.projects.get_by_uuid.return_value = project
        uow.users.get_by_ids.return_value = [member]
        membership = MagicMock(user_id=member.id, role="member")
        uow.project_members.list_by_project.return_value = [membership]
        activity = _make_activity_log(project_id=project.id, actor_user_id=member.id)
        uow.activity_logs.list_by_project.return_value = ([activity], 1)

        result = ActivityLogService().list_activities(project.id, uow=uow)

        actor = result.items[0].actor
        assert actor.id == member.id
        assert actor.name == "Bob Member"
        assert actor.role == "member"

    def test_actor_is_none_when_actor_user_id_missing(self) -> None:
        uow = _make_uow()
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        activity = _make_activity_log(project_id=project.id, actor_user_id=None)
        uow.activity_logs.list_by_project.return_value = ([activity], 1)

        result = ActivityLogService().list_activities(project.id, uow=uow)

        assert result.items[0].actor is None
        uow.users.get_by_ids.assert_called_once_with([])

    def test_falls_back_to_email_and_super_admin_role(self) -> None:
        super_admin_role = MagicMock()
        super_admin_role.name = ROLE_SUPER_ADMIN
        owner = make_user(name=None, email="carol@example.com", roles=[super_admin_role])
        project = make_project(owner_id=owner.id)
        uow = _make_uow()
        uow.projects.get_by_uuid.return_value = project
        uow.users.get_by_ids.return_value = [owner]
        activity = _make_activity_log(project_id=project.id, actor_user_id=owner.id)
        uow.activity_logs.list_by_project.return_value = ([activity], 1)

        result = ActivityLogService().list_activities(project.id, uow=uow)

        actor = result.items[0].actor
        assert actor.name == "carol@example.com"
        assert actor.role == ROLE_SUPER_ADMIN


class TestRecordActivity:
    def test_persists_and_commits(self) -> None:
        uow = _make_uow()
        project_id = uuid.uuid4()
        actor_user_id = uuid.uuid4()
        ctor = _make_uow_context_manager(uow)

        with patch("app.services.activity_log_service.UnitOfWork", ctor):
            record_activity(
                project_id=project_id,
                activity_type=ActivityType.RFP_MODULES_GENERATED,
                summary="Modules & Features Generated",
                message="Generated 5 Modules, 20 Features",
                actor_user_id=actor_user_id,
                data={"total_modules": 5, "total_features": 20},
            )

        uow.activity_logs.create.assert_called_once_with(
            project_id=project_id,
            actor_user_id=actor_user_id,
            activity_type="rfp_modules_generated",
            summary="Modules & Features Generated",
            message="[RFP] Generated 5 Modules, 20 Features",
            data={"total_modules": 5, "total_features": 20},
        )
        uow.projects.update_fields.assert_called_once_with(project_id, last_activity_at=ANY)
        uow.commit.assert_called_once()

    def test_prefixes_message_for_source_code_activity(self) -> None:
        uow = _make_uow()
        ctor = _make_uow_context_manager(uow)

        with patch("app.services.activity_log_service.UnitOfWork", ctor):
            record_activity(
                project_id=uuid.uuid4(),
                activity_type=ActivityType.SOURCE_CODE_PIPELINE_COMPLETED,
                summary="Source Code Pipeline Completed",
                message="Processed source code: 3 Modules, 9 Features, 20 User Stories",
                actor_user_id=None,
            )

        stored_message = uow.activity_logs.create.call_args.kwargs["message"]
        assert stored_message == (
            "[Source Code] Processed source code: 3 Modules, 9 Features, 20 User Stories"
        )

    def test_prefixes_message_for_incremental_changeset_ingested(self) -> None:
        uow = _make_uow()
        ctor = _make_uow_context_manager(uow)

        with patch("app.services.activity_log_service.UnitOfWork", ctor):
            record_activity(
                project_id=uuid.uuid4(),
                activity_type=ActivityType.INCREMENTAL_CHANGESET_INGESTED,
                summary="Incremental Change-Set Ingested",
                message="Ingested change-set: 2 added, 1 updated, 0 deleted",
                actor_user_id=None,
            )

        stored_message = uow.activity_logs.create.call_args.kwargs["message"]
        assert (
            stored_message
            == "[Incremental Update] Ingested change-set: 2 added, 1 updated, 0 deleted"
        )

    def test_does_not_prefix_unmapped_activity_type(self) -> None:
        """PROJECT_CREATED isn't pipeline-related, so it stays unprefixed."""
        uow = _make_uow()
        ctor = _make_uow_context_manager(uow)

        with patch("app.services.activity_log_service.UnitOfWork", ctor):
            record_activity(
                project_id=uuid.uuid4(),
                activity_type=ActivityType.PROJECT_CREATED,
                summary="Project Created",
                message='Created project "Foo"',
                actor_user_id=None,
            )

        stored_message = uow.activity_logs.create.call_args.kwargs["message"]
        assert stored_message == 'Created project "Foo"'

    def test_failure_does_not_raise(self) -> None:
        """A DB failure while logging must never fail the caller's already-successful action."""
        uow = _make_uow()
        uow.activity_logs.create.side_effect = RuntimeError("db down")
        ctor = _make_uow_context_manager(uow)

        with patch("app.services.activity_log_service.UnitOfWork", ctor):
            record_activity(
                project_id=uuid.uuid4(),
                activity_type=ActivityType.RFP_MODULES_GENERATED,
                summary="Summary",
                message="Message",
                actor_user_id=None,
            )
        # No exception propagated — success is simply not raising.


class TestResolveActorFromTask:
    def test_returns_none_when_task_db_id_missing(self) -> None:
        assert resolve_actor_from_task(None) is None
        assert resolve_actor_from_task("") is None

    def test_returns_user_id_when_task_found(self) -> None:
        uow = _make_uow()
        task_id = uuid.uuid4()
        user_id = uuid.uuid4()
        uow.project_tasks.get_by_id.return_value = MagicMock(user_id=user_id)
        ctor = _make_uow_context_manager(uow)

        with patch("app.services.activity_log_service.UnitOfWork", ctor):
            result = resolve_actor_from_task(str(task_id))

        assert result == user_id
        uow.project_tasks.get_by_id.assert_called_once_with(task_id)

    def test_returns_none_when_task_not_found(self) -> None:
        uow = _make_uow()
        uow.project_tasks.get_by_id.return_value = None
        ctor = _make_uow_context_manager(uow)

        with patch("app.services.activity_log_service.UnitOfWork", ctor):
            result = resolve_actor_from_task(str(uuid.uuid4()))

        assert result is None

    def test_returns_none_on_lookup_failure(self) -> None:
        uow = _make_uow()
        uow.project_tasks.get_by_id.side_effect = RuntimeError("db down")
        ctor = _make_uow_context_manager(uow)

        with patch("app.services.activity_log_service.UnitOfWork", ctor):
            result = resolve_actor_from_task(str(uuid.uuid4()))

        assert result is None
