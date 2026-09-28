"""Unit tests for ProjectService.

Tests verify service layer responsibilities:
- Business validation (conflicts, ownership, permissions)
- Authorization checks
- UnitOfWork orchestration
- Response DTO assembly (now owned by the service, not the postgres repo)

ProjectService is instantiated with a ``mock_neo4j_project_repo`` fixture
(defined in conftest.py) so tests exercise the service in complete isolation
from Neo4j.  ``get_user_story_counts`` is mocked on the repo fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from pydantic import ValidationError
import pytest

from app.core.constants import ROLE_ADMIN, ROLE_MEMBER, ROLE_SUPER_ADMIN
from app.core.enums.activity_type import ActivityType
from app.core.enums.notification_type import NotificationType
from app.core.enums.project_progress_filter import ProjectProgressFilter
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError as AppValidationError,
)
from app.core.messages import (
    MSG_ACTIVITY_PROJECT_CREATED,
    MSG_ACTIVITY_PROJECT_UPDATED,
    SUMMARY_ACTIVITY_PROJECT_CREATED,
    SUMMARY_ACTIVITY_PROJECT_UPDATED,
)
from app.repositories.neo4j.project_repository import ProjectProgressFlags, UserStoryCounts
from app.schemas.project_schema import (
    ProjectCreate,
    ProjectResponse,
    ProjectSummaryResponse,
    ProjectUpdate,
)
from app.services.project_service import ProjectService
from tests.conftest import make_project


@pytest.fixture(autouse=True)
def _mock_record_activity():
    """Prevent create_project's activity-log call from opening a real
    UnitOfWork — patched at ``app.services.project_service`` since it's
    imported at module level there (not lazily, unlike other services).
    """
    with patch("app.services.project_service.record_activity") as mock:
        yield mock


@pytest.fixture(autouse=True)
def _mock_publish_notification():
    """Prevent create_project's notification call from opening a real
    UnitOfWork/Redis publish — patched at ``app.services.project_service``
    since it's imported at module level there, matching ``_mock_record_activity``.
    """
    with patch("app.services.project_service.publish_notification") as mock:
        yield mock


# ── Helpers ────────────────────────────────────────────────────────────────


def _stamp(entity: object) -> None:
    """Side-effect for ``uow.projects.create`` / ``uow.projects.update`` —
    populate DB-generated fields the real flush+refresh would provide."""
    if not getattr(entity, "id", None):
        entity.id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    entity.created_at = now
    entity.updated_at = now
    if getattr(entity, "status", None) is None:
        entity.status = "active"
    if getattr(entity, "version", None) is None:
        entity.version = 1
    return entity


def _make_response(**kwargs) -> ProjectResponse:
    defaults = {
        "id": uuid.uuid4(),
        "name": "Test Project",
        "code": "TEST-0001",
        "description": "desc",
        "llm_provider": None,
        "llm_model": None,
        "status": "active",
        "files": 0,
        "user_stories": 0,
        "team_members": 0,
        "owner_id": uuid.uuid4(),
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
    }
    defaults.update(kwargs)
    return ProjectResponse(**defaults)


def _make_service(
    mock_neo4j_project_repo: MagicMock,
    mock_publisher: MagicMock | None = None,
    mock_setting_service: MagicMock | None = None,
    mock_module_feature_repo: MagicMock | None = None,
    mock_user_story_repo: MagicMock | None = None,
) -> ProjectService:
    if mock_publisher is None:
        mock_publisher = MagicMock()
    if mock_setting_service is None:
        from tests.conftest import _make_mock_setting_service

        mock_setting_service = _make_mock_setting_service()
    if mock_module_feature_repo is None:
        mock_module_feature_repo = MagicMock()
        mock_module_feature_repo.are_all_modules_approved = AsyncMock(return_value=True)
        mock_module_feature_repo.are_all_features_approved = AsyncMock(return_value=True)
    if mock_user_story_repo is None:
        mock_user_story_repo = MagicMock()
        mock_user_story_repo.are_all_user_stories_approved = AsyncMock(return_value=True)
    return ProjectService(
        neo4j_project_repo=mock_neo4j_project_repo,
        event_publisher=mock_publisher,
        setting_service=mock_setting_service,
        module_feature_repo=mock_module_feature_repo,
        user_story_repo=mock_user_story_repo,
    )


# ── Create ─────────────────────────────────────────────────────────────────


class TestCreateProject:
    def test_create_success(
        self, uow, mock_neo4j_project_repo, _mock_record_activity, _mock_publish_notification
    ):
        owner_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = None
        uow.projects.create.side_effect = _stamp
        uow.sources.get_source_counts_by_project.return_value = {}
        mock_publisher = MagicMock()

        payload = ProjectCreate(
            name="My Project",
            description="desc",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
        )
        svc = _make_service(mock_neo4j_project_repo, mock_publisher)
        result = svc.create_project(payload, uow, owner_id, requester_roles=[])
        uow.projects.get_by_name_and_owner.assert_called_once_with("My Project", owner_id)
        uow.projects.create.assert_called_once()
        uow.commit.assert_called_once()
        mock_publisher.project_upserted.assert_called_once()
        assert result.name == "My Project"
        assert result.llm_provider == "anthropic"
        assert result.llm_model == "claude_sonnet_4_6"

        _mock_record_activity.assert_called_once_with(
            project_id=result.id,
            activity_type=ActivityType.PROJECT_CREATED,
            summary=SUMMARY_ACTIVITY_PROJECT_CREATED,
            message=MSG_ACTIVITY_PROJECT_CREATED.format(
                project_name="My Project",
                project_type="rfp",
                llm_provider="anthropic",
                llm_model="claude_sonnet_4_6",
            ),
            actor_user_id=owner_id,
            data={
                "project_name": "My Project",
                "llm_provider": "anthropic",
                "llm_model": "claude_sonnet_4_6",
                "project_type": "rfp",
            },
        )

        _mock_publish_notification.assert_called_once_with(
            user_id=owner_id,
            title=SUMMARY_ACTIVITY_PROJECT_CREATED,
            message=MSG_ACTIVITY_PROJECT_CREATED.format(
                project_name="My Project",
                project_type="rfp",
                llm_provider="anthropic",
                llm_model="claude_sonnet_4_6",
            ),
            notification_type=NotificationType.SUCCESS,
            data={"project_id": str(result.id)},
        )

    def test_create_succeeds_when_notification_publish_fails(
        self,
        uow,
        mock_neo4j_project_repo,
        _mock_record_activity,
        _mock_publish_notification,
    ):
        """A notification failure is best-effort and must not affect the
        create-project response — mirrors ``_notify_if_llm_api_key_missing``'s
        never-raises contract."""
        owner_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = None
        uow.projects.create.side_effect = _stamp
        uow.sources.get_source_counts_by_project.return_value = {}
        _mock_publish_notification.side_effect = Exception("redis down")
        payload = ProjectCreate(
            name="My Project",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
        )

        result = _make_service(mock_neo4j_project_repo).create_project(
            payload, uow, owner_id, requester_roles=[]
        )

        assert result.name == "My Project"
        _mock_publish_notification.assert_called_once()

    def test_create_duplicate_name_does_not_record_activity(
        self, uow, mock_neo4j_project_repo, _mock_record_activity
    ):
        owner_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = make_project(name="Dup")
        payload = ProjectCreate(
            name="Dup",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
        )

        with pytest.raises(ConflictError):
            _make_service(mock_neo4j_project_repo).create_project(
                payload, uow, owner_id, requester_roles=[]
            )

        _mock_record_activity.assert_not_called()

    def test_create_duplicate_name_raises_conflict(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = make_project(name="Dup")
        payload = ProjectCreate(
            name="Dup",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
        )

        with pytest.raises(ConflictError):
            _make_service(mock_neo4j_project_repo).create_project(
                payload, uow, owner_id, requester_roles=[]
            )

        uow.commit.assert_not_called()

    def test_create_llm_provider_rejected_when_not_enabled_for_tenant(
        self, uow, mock_neo4j_project_repo
    ):
        owner_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = None
        uow.tenants = MagicMock()
        uow.tenants.get.return_value = MagicMock(llm_providers=[MagicMock(provider="deepseek")])
        payload = ProjectCreate(
            name="My Project",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
        )

        with pytest.raises(AppValidationError):
            _make_service(mock_neo4j_project_repo).create_project(
                payload, uow, owner_id, requester_roles=[], owner_tenant_id=tenant_id
            )

        uow.projects.create.assert_not_called()

    def test_create_llm_provider_allowed_when_enabled_for_tenant(
        self, uow, mock_neo4j_project_repo
    ):
        owner_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = None
        uow.projects.create.side_effect = _stamp
        uow.sources.get_source_counts_by_project.return_value = {}
        uow.tenants = MagicMock()
        uow.tenants.get.return_value = MagicMock(
            llm_providers=[MagicMock(provider="anthropic"), MagicMock(provider="deepseek")]
        )
        uow.tenants.increment_project_sequence.return_value = ("ACME", 1)
        payload = ProjectCreate(
            name="My Project",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
        )

        result = _make_service(mock_neo4j_project_repo).create_project(
            payload, uow, owner_id, requester_roles=[], owner_tenant_id=tenant_id
        )

        assert result.llm_provider == "anthropic"

    def test_create_llm_provider_unrestricted_when_tenant_has_no_providers_configured(
        self, uow, mock_neo4j_project_repo
    ):
        owner_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = None
        uow.projects.create.side_effect = _stamp
        uow.sources.get_source_counts_by_project.return_value = {}
        uow.tenants = MagicMock()
        uow.tenants.get.return_value = MagicMock(llm_providers=[])
        uow.tenants.increment_project_sequence.return_value = ("ACME", 1)
        payload = ProjectCreate(
            name="My Project",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
        )

        result = _make_service(mock_neo4j_project_repo).create_project(
            payload, uow, owner_id, requester_roles=[], owner_tenant_id=tenant_id
        )

        assert result.llm_provider == "anthropic"

    def test_create_llm_model_rejected_when_not_under_provider(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = None
        payload = ProjectCreate(
            name="My Project", llm_provider="anthropic", llm_model="gpt-5", project_type="rfp"
        )

        with pytest.raises(AppValidationError):
            _make_service(mock_neo4j_project_repo).create_project(
                payload, uow, owner_id, requester_roles=[]
            )

        uow.projects.create.assert_not_called()

    def test_create_llm_model_requires_provider_at_schema_level(self):
        with pytest.raises(ValidationError):
            ProjectCreate(name="My Project", llm_model="gpt-5", project_type="rfp")

    def test_create_llm_model_allowed_when_under_provider(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = None
        uow.projects.create.side_effect = _stamp
        uow.sources.get_source_counts_by_project.return_value = {}
        payload = ProjectCreate(
            name="My Project",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
        )

        result = _make_service(mock_neo4j_project_repo).create_project(
            payload, uow, owner_id, requester_roles=[]
        )

        assert result.llm_model == "claude_sonnet_4_6"

    def test_create_super_admin_can_assign_arbitrary_tenant(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        own_tenant_id = uuid.uuid4()
        target_tenant_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = None
        uow.projects.create.side_effect = _stamp
        uow.sources.get_source_counts_by_project.return_value = {}
        uow.tenants = MagicMock()
        uow.tenants.get.return_value = MagicMock(llm_providers=[])
        uow.tenants.increment_project_sequence.return_value = ("ACME", 1)
        payload = ProjectCreate(
            name="My Project",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
            tenant_id=target_tenant_id,
        )

        result = _make_service(mock_neo4j_project_repo).create_project(
            payload,
            uow,
            owner_id,
            requester_roles=[ROLE_SUPER_ADMIN],
            owner_tenant_id=own_tenant_id,
        )

        assert result.tenant_id == target_tenant_id
        # Called multiple times with the same tenant_id: once to resolve/validate
        # the requested tenant, once for the llm_provider-enabled check, and once
        # for the post-create "API key configured" notification lookup.
        uow.tenants.get.assert_any_call(target_tenant_id)

    def test_create_non_super_admin_cannot_send_tenant_id(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = None
        payload = ProjectCreate(
            name="My Project",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
            tenant_id=uuid.uuid4(),
        )

        with pytest.raises(ForbiddenError):
            _make_service(mock_neo4j_project_repo).create_project(
                payload, uow, owner_id, requester_roles=[ROLE_ADMIN]
            )

        uow.projects.create.assert_not_called()

    def test_create_super_admin_unknown_tenant_raises_not_found(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = None
        uow.tenants = MagicMock()
        uow.tenants.get.return_value = None
        payload = ProjectCreate(
            name="My Project",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
            tenant_id=uuid.uuid4(),
        )

        with pytest.raises(NotFoundError):
            _make_service(mock_neo4j_project_repo).create_project(
                payload, uow, owner_id, requester_roles=[ROLE_SUPER_ADMIN]
            )

        uow.projects.create.assert_not_called()


class TestGenerateProjectCode:
    def test_tenanted_project_uses_tenant_code_and_sequence(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = None
        uow.projects.create.side_effect = _stamp
        uow.sources.get_source_counts_by_project.return_value = {}
        uow.tenants = MagicMock()
        uow.tenants.get.return_value = MagicMock(llm_providers=[])
        uow.tenants.increment_project_sequence.return_value = ("ACME", 7)
        payload = ProjectCreate(
            name="My Project",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
        )

        result = _make_service(mock_neo4j_project_repo).create_project(
            payload, uow, owner_id, requester_roles=[], owner_tenant_id=tenant_id
        )

        uow.tenants.increment_project_sequence.assert_called_once_with(tenant_id)
        uow.projects.next_untenanted_code_sequence.assert_not_called()
        assert result.code == "ACME-0007"

    def test_untenanted_project_uses_global_sequence(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = None
        uow.projects.create.side_effect = _stamp
        uow.sources.get_source_counts_by_project.return_value = {}
        uow.projects.next_untenanted_code_sequence.return_value = 42
        payload = ProjectCreate(
            name="My Project",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
        )

        result = _make_service(mock_neo4j_project_repo).create_project(
            payload, uow, owner_id, requester_roles=[]
        )

        uow.projects.next_untenanted_code_sequence.assert_called_once()
        uow.tenants.increment_project_sequence.assert_not_called()
        assert result.code == "PRJ-0042"

    def test_unknown_tenant_id_raises_not_found(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        uow.projects.get_by_name_and_owner.return_value = None
        uow.tenants = MagicMock()
        uow.tenants.get.return_value = MagicMock(llm_providers=[])
        uow.tenants.increment_project_sequence.return_value = None
        payload = ProjectCreate(
            name="My Project",
            llm_provider="anthropic",
            llm_model="claude_sonnet_4_6",
            project_type="rfp",
        )

        with pytest.raises(NotFoundError):
            _make_service(mock_neo4j_project_repo).create_project(
                payload, uow, owner_id, requester_roles=[], owner_tenant_id=tenant_id
            )

        uow.projects.create.assert_not_called()


# ── Get ────────────────────────────────────────────────────────────────────


class TestGetProject:
    @pytest.mark.asyncio
    async def test_get_success(self, uow, mock_neo4j_project_repo):
        project_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        project = make_project()
        project.id = project_id
        project.owner_id = owner_id
        project.last_activity_at = datetime.now(tz=UTC)
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_source_counts_by_project.return_value = {project_id: 5}
        mock_neo4j_project_repo.get_user_story_counts.return_value = {
            project_id: UserStoryCounts(total=7, approved=3)
        }

        result = await _make_service(mock_neo4j_project_repo).get_project(
            project_id, uow, requester_id=owner_id, requester_roles=[]
        )

        assert result.id == project_id
        assert result.files == 5
        assert result.user_stories == 7
        assert result.approved_user_stories == 3
        assert result.last_activity_at == project.last_activity_at
        uow.projects.get_by_uuid.assert_called_once_with(project_id)

    @pytest.mark.asyncio
    async def test_get_returns_approval_flags_from_neo4j(self, uow, mock_neo4j_project_repo):
        """Each of the 3 approval flags is a straight pass-through of its own
        repo call — a mix of True/False here proves they aren't conflated."""
        project_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        project = make_project()
        project.id = project_id
        project.owner_id = owner_id
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_source_counts_by_project.return_value = {}

        mock_module_feature_repo = MagicMock()
        mock_module_feature_repo.are_all_modules_approved = AsyncMock(return_value=True)
        mock_module_feature_repo.are_all_features_approved = AsyncMock(return_value=False)
        mock_user_story_repo = MagicMock()
        mock_user_story_repo.are_all_user_stories_approved = AsyncMock(return_value=False)

        result = await _make_service(
            mock_neo4j_project_repo,
            mock_module_feature_repo=mock_module_feature_repo,
            mock_user_story_repo=mock_user_story_repo,
        ).get_project(project_id, uow, requester_id=owner_id, requester_roles=[])

        assert result.all_modules_approved is True
        assert result.all_features_approved is False
        assert result.all_user_stories_approved is False
        mock_module_feature_repo.are_all_modules_approved.assert_awaited_once_with(project_id)
        mock_module_feature_repo.are_all_features_approved.assert_awaited_once_with(project_id)
        mock_user_story_repo.are_all_user_stories_approved.assert_awaited_once_with(project_id)

    @pytest.mark.asyncio
    async def test_get_admin_can_access_any_project_in_same_tenant(
        self, uow, mock_neo4j_project_repo
    ):
        project_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        project = make_project(tenant_id=tenant_id)
        project.id = project_id
        project.owner_id = uuid.uuid4()  # different owner
        uow.projects.get_by_uuid.return_value = project
        uow.sources.get_source_counts_by_project.return_value = {}

        result = await _make_service(mock_neo4j_project_repo).get_project(
            project_id,
            uow,
            requester_id=uuid.uuid4(),
            requester_roles=["admin"],
            requester_tenant_id=tenant_id,
        )

        assert result.id == project_id

    @pytest.mark.asyncio
    async def test_get_admin_forbidden_for_other_tenant_project(self, uow, mock_neo4j_project_repo):
        project = make_project(tenant_id=uuid.uuid4())
        uow.projects.get_by_uuid.return_value = project

        with pytest.raises(ForbiddenError):
            await _make_service(mock_neo4j_project_repo).get_project(
                project.id,
                uow,
                requester_id=uuid.uuid4(),
                requester_roles=["admin"],
                requester_tenant_id=uuid.uuid4(),
            )

    @pytest.mark.asyncio
    async def test_get_super_admin_has_no_bypass(self, uow, mock_neo4j_project_repo):
        """super_admin's platform-level role does not extend to viewing an
        individual project — only project list/summary (GET /projects/all,
        dashboard stats), not a single project's detail."""
        project = make_project(tenant_id=uuid.uuid4())
        uow.projects.get_by_uuid.return_value = project

        with pytest.raises(ForbiddenError):
            await _make_service(mock_neo4j_project_repo).get_project(
                project.id,
                uow,
                requester_id=uuid.uuid4(),
                requester_roles=["super_admin"],
                requester_tenant_id=uuid.uuid4(),
            )

    @pytest.mark.asyncio
    async def test_get_forbidden_non_owner(self, uow, mock_neo4j_project_repo):
        project = make_project(owner_id=uuid.uuid4())
        uow.projects.get_by_uuid.return_value = project

        with pytest.raises(ForbiddenError):
            await _make_service(mock_neo4j_project_repo).get_project(
                project.id, uow, requester_id=uuid.uuid4(), requester_roles=["pm"]
            )

    @pytest.mark.asyncio
    async def test_get_not_found(self, uow, mock_neo4j_project_repo):
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            await _make_service(mock_neo4j_project_repo).get_project(
                uuid.uuid4(), uow, requester_id=uuid.uuid4(), requester_roles=[]
            )


# ── List (owner) ───────────────────────────────────────────────────────────


class TestListProjects:
    def test_list_returns_paginated(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        projects = [make_project(owner_id=owner_id) for _ in range(3)]
        uow.projects.get_paginated.return_value = (projects, 3)
        uow.sources.get_source_counts_by_project.return_value = {}

        result = _make_service(mock_neo4j_project_repo).list_projects(
            owner_id=owner_id, skip=0, limit=10, uow=uow
        )

        assert result.total == 3
        assert len(result.items) == 3
        uow.projects.get_paginated.assert_called_once_with(
            owner_id=owner_id, skip=0, limit=10, search=None, member_user_id=owner_id
        )

    def test_list_empty(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        uow.projects.get_paginated.return_value = ([], 0)

        result = _make_service(mock_neo4j_project_repo).list_projects(
            owner_id=owner_id, skip=0, limit=10, uow=uow
        )

        assert result.total == 0
        assert result.items == []

    def test_list_with_search(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        uow.projects.get_paginated.return_value = ([], 0)

        _make_service(mock_neo4j_project_repo).list_projects(
            owner_id=owner_id, skip=0, limit=10, uow=uow, search="keyword"
        )

        call_kwargs = uow.projects.get_paginated.call_args.kwargs
        assert call_kwargs["search"] == "keyword"


# ── List all (admin) ──────────────────────────────────────────────────────


class TestListAllProjects:
    def test_list_all_super_admin_sees_every_tenant(self, uow, mock_neo4j_project_repo):
        projects = [make_project() for _ in range(2)]
        uow.projects.get_paginated.return_value = (projects, 2)
        uow.sources.get_source_counts_by_project.return_value = {}

        result = _make_service(mock_neo4j_project_repo).list_all_projects(
            skip=0,
            limit=20,
            uow=uow,
            requester_roles=["super_admin"],
            requester_tenant_id=uuid.uuid4(),
        )

        assert result.total == 2
        assert len(result.items) == 2
        uow.projects.get_paginated.assert_called_once_with(
            skip=0, limit=20, search=None, tenant_id=None
        )

    def test_list_all_admin_scoped_to_own_tenant(self, uow, mock_neo4j_project_repo):
        tenant_id = uuid.uuid4()
        uow.projects.get_paginated.return_value = ([], 0)

        _make_service(mock_neo4j_project_repo).list_all_projects(
            skip=0,
            limit=20,
            uow=uow,
            requester_roles=["admin"],
            requester_tenant_id=tenant_id,
        )

        uow.projects.get_paginated.assert_called_once_with(
            skip=0, limit=20, search=None, tenant_id=tenant_id
        )

    def test_list_all_super_admin_can_narrow_to_one_tenant(self, uow, mock_neo4j_project_repo):
        chosen_tenant_id = uuid.uuid4()
        uow.projects.get_paginated.return_value = ([], 0)

        _make_service(mock_neo4j_project_repo).list_all_projects(
            skip=0,
            limit=20,
            uow=uow,
            requester_roles=["super_admin"],
            requester_tenant_id=uuid.uuid4(),
            tenant_id=chosen_tenant_id,
        )

        uow.projects.get_paginated.assert_called_once_with(
            skip=0, limit=20, search=None, tenant_id=chosen_tenant_id
        )

    def test_list_all_admin_tenant_id_param_ignored(self, uow, mock_neo4j_project_repo):
        own_tenant_id = uuid.uuid4()
        uow.projects.get_paginated.return_value = ([], 0)

        _make_service(mock_neo4j_project_repo).list_all_projects(
            skip=0,
            limit=20,
            uow=uow,
            requester_roles=["admin"],
            requester_tenant_id=own_tenant_id,
            tenant_id=uuid.uuid4(),
        )

        uow.projects.get_paginated.assert_called_once_with(
            skip=0, limit=20, search=None, tenant_id=own_tenant_id
        )


# ── List summary (role-scoped) ────────────────────────────────────────────


class TestListProjectsSummary:
    def test_admin_scoped_to_own_tenant(self, uow, mock_neo4j_project_repo):
        tenant_id = uuid.uuid4()
        project = make_project()
        uow.projects.list_for_scope.return_value = [project]
        uow.sources.get_source_counts_by_project.return_value = {project.id: 5}

        result = _make_service(mock_neo4j_project_repo).list_projects_summary(
            uow=uow,
            requester_id=uuid.uuid4(),
            requester_roles=[ROLE_ADMIN],
            requester_tenant_id=tenant_id,
        )

        uow.projects.list_for_scope.assert_called_once_with(tenant_id=tenant_id, search=None)
        assert result == [ProjectSummaryResponse(id=project.id, name=project.name, files=5)]
        mock_neo4j_project_repo.get_user_story_counts.assert_not_called()
        mock_neo4j_project_repo.get_progress_flags.assert_not_called()

    def test_super_admin_sees_every_tenant(self, uow, mock_neo4j_project_repo):
        uow.projects.list_for_scope.return_value = []

        _make_service(mock_neo4j_project_repo).list_projects_summary(
            uow=uow,
            requester_id=uuid.uuid4(),
            requester_roles=[ROLE_SUPER_ADMIN],
            requester_tenant_id=uuid.uuid4(),
        )

        uow.projects.list_for_scope.assert_called_once_with(tenant_id=None, search=None)

    def test_member_scoped_to_owned_or_assigned(self, uow, mock_neo4j_project_repo):
        requester_id = uuid.uuid4()
        uow.projects.list_for_scope.return_value = []

        _make_service(mock_neo4j_project_repo).list_projects_summary(
            uow=uow,
            requester_id=requester_id,
            requester_roles=[ROLE_MEMBER],
            requester_tenant_id=uuid.uuid4(),
        )

        uow.projects.list_for_scope.assert_called_once_with(
            owner_id=requester_id, member_user_id=requester_id, search=None
        )

    def test_missing_files_count_defaults_to_zero(self, uow, mock_neo4j_project_repo):
        project = make_project()
        uow.projects.list_for_scope.return_value = [project]
        uow.sources.get_source_counts_by_project.return_value = {}

        result = _make_service(mock_neo4j_project_repo).list_projects_summary(
            uow=uow,
            requester_id=uuid.uuid4(),
            requester_roles=[ROLE_MEMBER],
        )

        assert result[0].files == 0

    def test_empty(self, uow, mock_neo4j_project_repo):
        uow.projects.list_for_scope.return_value = []

        result = _make_service(mock_neo4j_project_repo).list_projects_summary(
            uow=uow,
            requester_id=uuid.uuid4(),
            requester_roles=[ROLE_MEMBER],
        )

        assert result == []

    def _make_three_stage_projects(self, uow, mock_neo4j_project_repo):
        """Fixture-ish helper: one project per progress bucket (fresh, module/feature
        only, user story created), wired up for a stage-filter test."""
        fresh = make_project()
        module_feature_only = make_project()
        with_stories = make_project()
        uow.projects.list_for_scope.return_value = [fresh, module_feature_only, with_stories]
        uow.sources.get_source_counts_by_project.return_value = {}
        mock_neo4j_project_repo.get_progress_flags.return_value = {
            fresh.id: ProjectProgressFlags(has_module_feature=False, has_user_story=False),
            module_feature_only.id: ProjectProgressFlags(
                has_module_feature=True, has_user_story=False
            ),
            with_stories.id: ProjectProgressFlags(has_module_feature=True, has_user_story=True),
        }
        return fresh, module_feature_only, with_stories

    def test_stage_fresh_keeps_only_projects_with_no_backlog(self, uow, mock_neo4j_project_repo):
        fresh, _module_feature_only, _with_stories = self._make_three_stage_projects(
            uow, mock_neo4j_project_repo
        )

        result = _make_service(mock_neo4j_project_repo).list_projects_summary(
            uow=uow,
            requester_id=uuid.uuid4(),
            requester_roles=[ROLE_ADMIN],
            stage=ProjectProgressFilter.FRESH,
        )

        assert [p.id for p in result] == [fresh.id]

    def test_stage_module_feature_only_excludes_projects_with_user_stories(
        self, uow, mock_neo4j_project_repo
    ):
        _fresh, module_feature_only, _with_stories = self._make_three_stage_projects(
            uow, mock_neo4j_project_repo
        )

        result = _make_service(mock_neo4j_project_repo).list_projects_summary(
            uow=uow,
            requester_id=uuid.uuid4(),
            requester_roles=[ROLE_ADMIN],
            stage=ProjectProgressFilter.MODULE_FEATURE_ONLY,
        )

        assert [p.id for p in result] == [module_feature_only.id]

    def test_stage_user_story_created_keeps_only_projects_with_stories(
        self, uow, mock_neo4j_project_repo
    ):
        _fresh, _module_feature_only, with_stories = self._make_three_stage_projects(
            uow, mock_neo4j_project_repo
        )

        result = _make_service(mock_neo4j_project_repo).list_projects_summary(
            uow=uow,
            requester_id=uuid.uuid4(),
            requester_roles=[ROLE_ADMIN],
            stage=ProjectProgressFilter.USER_STORY_CREATED,
        )

        assert [p.id for p in result] == [with_stories.id]

    def test_stage_neo4j_failure_excludes_every_project(self, uow, mock_neo4j_project_repo):
        project = make_project()
        uow.projects.list_for_scope.return_value = [project]
        uow.sources.get_source_counts_by_project.return_value = {}
        mock_neo4j_project_repo.get_progress_flags.side_effect = Exception("neo4j down")

        result = _make_service(mock_neo4j_project_repo).list_projects_summary(
            uow=uow,
            requester_id=uuid.uuid4(),
            requester_roles=[ROLE_ADMIN],
            stage=ProjectProgressFilter.USER_STORY_CREATED,
        )

        assert result == []


# ── Update ─────────────────────────────────────────────────────────────────


class TestUpdateProject:
    def test_update_payload_requires_at_least_one_field(self) -> None:
        with pytest.raises(ValidationError, match="At least one field must be provided"):
            ProjectUpdate()

    def test_update_name_success(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        project = make_project(name="Old", owner_id=owner_id)
        project_id = project.id
        uow.projects.get_by_uuid.return_value = project
        uow.projects.get_by_name_and_owner.return_value = None
        uow.projects.update.side_effect = _stamp
        uow.sources.get_source_counts_by_project.return_value = {project_id: 0}
        mock_publisher = MagicMock()

        payload = ProjectUpdate(name="New")
        result = _make_service(mock_neo4j_project_repo, mock_publisher).update_project(
            project_id, payload, owner_id, ["pm"], uow
        )

        assert result.name == "New"
        uow.projects.get_by_name_and_owner.assert_called_once_with("New", owner_id)
        uow.projects.update.assert_called_once_with(project)
        uow.commit.assert_called_once()
        mock_publisher.project_upserted.assert_called_once()

    def test_update_llm_fields_success(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        project = make_project(name="Old", owner_id=owner_id)
        uow.projects.get_by_uuid.return_value = project
        uow.projects.update.side_effect = _stamp
        uow.sources.get_source_counts_by_project.return_value = {project.id: 0}

        payload = ProjectUpdate(llm_provider="openai", llm_model="gpt-5")
        result = _make_service(mock_neo4j_project_repo).update_project(
            project.id, payload, owner_id, ["pm"], uow
        )

        assert result.llm_provider == "openai"
        assert result.llm_model == "gpt-5"
        uow.projects.update.assert_called_once_with(project)

    def test_update_llm_provider_blocked_while_pipeline_running(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        project = make_project(name="Old", owner_id=owner_id)
        uow.projects.get_by_uuid.return_value = project
        uow.source_ingestions.list_running_by_project.return_value = [MagicMock(id=uuid.uuid4())]

        with pytest.raises(ConflictError):
            _make_service(mock_neo4j_project_repo).update_project(
                project.id, ProjectUpdate(llm_provider="openai"), owner_id, ["pm"], uow
            )

        uow.source_ingestions.list_running_by_project.assert_called_once_with(project.id)
        uow.projects.update.assert_not_called()
        uow.commit.assert_not_called()

    def test_update_llm_model_blocked_while_pipeline_running(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        project = make_project(
            name="Old", owner_id=owner_id, llm_provider="anthropic", llm_model="claude_sonnet_4_6"
        )
        uow.projects.get_by_uuid.return_value = project
        uow.source_ingestions.list_running_by_project.return_value = [MagicMock(id=uuid.uuid4())]

        with pytest.raises(ConflictError):
            _make_service(mock_neo4j_project_repo).update_project(
                project.id,
                ProjectUpdate(llm_model="claude_sonnet_4_5"),
                owner_id,
                ["pm"],
                uow,
            )

        uow.projects.update.assert_not_called()

    def test_update_any_field_blocked_while_pipeline_running(self, uow, mock_neo4j_project_repo):
        """The running-pipeline guard now gates the whole PATCH, not just
        llm_provider/llm_model — a plain name/description change is blocked too."""
        owner_id = uuid.uuid4()
        project = make_project(name="Old", owner_id=owner_id)
        uow.projects.get_by_uuid.return_value = project
        uow.source_ingestions.list_running_by_project.return_value = [MagicMock(id=uuid.uuid4())]

        with pytest.raises(ConflictError):
            _make_service(mock_neo4j_project_repo).update_project(
                project.id, ProjectUpdate(name="New"), owner_id, ["pm"], uow
            )

        uow.source_ingestions.list_running_by_project.assert_called_once_with(project.id)
        uow.projects.update.assert_not_called()
        uow.commit.assert_not_called()

    def test_update_records_activity_with_llm_and_type_fields(
        self, uow, mock_neo4j_project_repo, _mock_record_activity
    ):
        owner_id = uuid.uuid4()
        project = make_project(name="Old", owner_id=owner_id)
        uow.projects.get_by_uuid.return_value = project
        uow.projects.update.side_effect = _stamp
        uow.sources.get_source_counts_by_project.return_value = {project.id: 0}

        payload = ProjectUpdate(llm_provider="openai", llm_model="gpt-5", project_type="rfp")
        result = _make_service(mock_neo4j_project_repo).update_project(
            project.id, payload, owner_id, ["pm"], uow
        )

        _mock_record_activity.assert_called_once_with(
            project_id=result.id,
            activity_type=ActivityType.PROJECT_UPDATED,
            summary=SUMMARY_ACTIVITY_PROJECT_UPDATED,
            message=MSG_ACTIVITY_PROJECT_UPDATED.format(
                project_name="Old",
                project_type="rfp",
                llm_provider="openai",
                llm_model="gpt-5",
            ),
            actor_user_id=owner_id,
            data={
                "project_name": "Old",
                "llm_provider": "openai",
                "llm_model": "gpt-5",
                "project_type": "rfp",
            },
        )

    def test_update_records_activity_with_not_set_fallback_for_missing_fields(
        self, uow, mock_neo4j_project_repo, _mock_record_activity
    ):
        """llm_provider/llm_model/project_type are all optional — the message
        must fall back to a readable placeholder instead of literal "None"."""
        owner_id = uuid.uuid4()
        project = make_project(name="Old", owner_id=owner_id)
        project.llm_provider = None
        project.llm_model = None
        project.project_type = None
        uow.projects.get_by_uuid.return_value = project
        uow.projects.get_by_name_and_owner.return_value = None
        uow.projects.update.side_effect = _stamp
        uow.sources.get_source_counts_by_project.return_value = {project.id: 0}

        _make_service(mock_neo4j_project_repo).update_project(
            project.id, ProjectUpdate(name="Old renamed"), owner_id, ["pm"], uow
        )

        call_kwargs = _mock_record_activity.call_args.kwargs
        assert "Not set" in call_kwargs["message"]
        assert call_kwargs["data"]["llm_provider"] is None
        assert call_kwargs["data"]["llm_model"] is None
        assert call_kwargs["data"]["project_type"] is None

    def test_update_not_found_does_not_record_activity(
        self, uow, mock_neo4j_project_repo, _mock_record_activity
    ):
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            _make_service(mock_neo4j_project_repo).update_project(
                uuid.uuid4(), ProjectUpdate(name="New"), uuid.uuid4(), ["pm"], uow
            )

        _mock_record_activity.assert_not_called()

    def test_update_llm_provider_rejected_when_not_enabled_for_tenant(
        self, uow, mock_neo4j_project_repo
    ):
        owner_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        project = make_project(name="Old", owner_id=owner_id, tenant_id=tenant_id)
        uow.projects.get_by_uuid.return_value = project
        uow.tenants = MagicMock()
        uow.tenants.get.return_value = MagicMock(llm_providers=[MagicMock(provider="deepseek")])

        payload = ProjectUpdate(llm_provider="openai")

        with pytest.raises(AppValidationError):
            _make_service(mock_neo4j_project_repo).update_project(
                project.id, payload, owner_id, ["pm"], uow
            )

        uow.projects.update.assert_not_called()

    def test_update_llm_model_rejected_when_not_under_provider(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        project = make_project(name="Old", owner_id=owner_id)
        uow.projects.get_by_uuid.return_value = project

        payload = ProjectUpdate(llm_provider="anthropic", llm_model="gpt-5")

        with pytest.raises(AppValidationError):
            _make_service(mock_neo4j_project_repo).update_project(
                project.id, payload, owner_id, ["pm"], uow
            )

        uow.projects.update.assert_not_called()

    def test_update_llm_model_rejected_against_existing_provider_when_only_model_changes(
        self, uow, mock_neo4j_project_repo
    ):
        owner_id = uuid.uuid4()
        project = make_project(name="Old", owner_id=owner_id, llm_provider="anthropic")
        uow.projects.get_by_uuid.return_value = project

        payload = ProjectUpdate(llm_model="gpt-5")

        with pytest.raises(AppValidationError):
            _make_service(mock_neo4j_project_repo).update_project(
                project.id, payload, owner_id, ["pm"], uow
            )

        uow.projects.update.assert_not_called()

    def test_update_llm_model_rejected_without_any_provider(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        project = make_project(name="Old", owner_id=owner_id, llm_provider=None)
        uow.projects.get_by_uuid.return_value = project

        payload = ProjectUpdate(llm_model="gpt-5")

        with pytest.raises(AppValidationError):
            _make_service(mock_neo4j_project_repo).update_project(
                project.id, payload, owner_id, ["pm"], uow
            )

        uow.projects.update.assert_not_called()

    def test_update_not_found(self, uow, mock_neo4j_project_repo):
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            _make_service(mock_neo4j_project_repo).update_project(
                uuid.uuid4(), ProjectUpdate(name="X"), uuid.uuid4(), [], uow
            )

    def test_update_forbidden_non_owner(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        project = make_project(owner_id=owner_id)
        uow.projects.get_by_uuid.return_value = project

        with pytest.raises(ForbiddenError):
            _make_service(mock_neo4j_project_repo).update_project(
                project.id, ProjectUpdate(name="X"), uuid.uuid4(), ["pm"], uow
            )

    def test_update_admin_bypass_same_tenant(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        project = make_project(owner_id=owner_id, tenant_id=tenant_id)
        project_id = project.id
        uow.projects.get_by_uuid.return_value = project
        uow.projects.get_by_name_and_owner.return_value = None
        uow.projects.update.side_effect = _stamp
        uow.sources.get_source_counts_by_project.return_value = {project_id: 0}

        result = _make_service(mock_neo4j_project_repo).update_project(
            project_id,
            ProjectUpdate(name="AdminEdit"),
            uuid.uuid4(),
            ["admin"],
            uow,
            requester_tenant_id=tenant_id,
        )

        assert result.name == "AdminEdit"
        uow.commit.assert_called_once()

    def test_update_admin_forbidden_other_tenant(self, uow, mock_neo4j_project_repo):
        project = make_project(tenant_id=uuid.uuid4())
        uow.projects.get_by_uuid.return_value = project

        with pytest.raises(ForbiddenError):
            _make_service(mock_neo4j_project_repo).update_project(
                project.id,
                ProjectUpdate(name="AdminEdit"),
                uuid.uuid4(),
                ["admin"],
                uow,
                requester_tenant_id=uuid.uuid4(),
            )

    def test_update_name_conflict(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        project = make_project(name="A", owner_id=owner_id)
        clash = make_project(name="B")
        uow.projects.get_by_uuid.return_value = project
        uow.projects.get_by_name_and_owner.return_value = clash

        with pytest.raises(ConflictError):
            _make_service(mock_neo4j_project_repo).update_project(
                project.id, ProjectUpdate(name="B"), owner_id, [], uow
            )

    def test_update_super_admin_has_no_bypass(self, uow, mock_neo4j_project_repo):
        """super_admin can no longer update any project it doesn't own —
        including to reassign/clear its tenant — since super_admin's role
        is platform administration, not individual project management."""
        project = make_project(tenant_id=uuid.uuid4())
        uow.projects.get_by_uuid.return_value = project

        payload = ProjectUpdate(tenant_id=uuid.uuid4())
        with pytest.raises(ForbiddenError):
            _make_service(mock_neo4j_project_repo).update_project(
                project.id, payload, uuid.uuid4(), [ROLE_SUPER_ADMIN], uow
            )

        uow.projects.update.assert_not_called()

    def test_update_non_super_admin_cannot_send_tenant_id(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        project = make_project(owner_id=owner_id, tenant_id=uuid.uuid4())
        uow.projects.get_by_uuid.return_value = project

        payload = ProjectUpdate(tenant_id=uuid.uuid4())
        with pytest.raises(ForbiddenError):
            _make_service(mock_neo4j_project_repo).update_project(
                project.id, payload, owner_id, [ROLE_ADMIN], uow
            )

        uow.projects.update.assert_not_called()


# ── Delete ─────────────────────────────────────────────────────────────────


class TestDeleteProject:
    def test_delete_success_owner(self, uow, mock_neo4j_project_repo):
        owner_id = uuid.uuid4()
        project = make_project(owner_id=owner_id)
        uow.projects.get_by_uuid.return_value = project
        call_order: list[str] = []
        uow.commit.side_effect = lambda: call_order.append("pg_commit")
        mock_publisher = MagicMock()
        mock_publisher.project_deleted.side_effect = lambda *a, **kw: call_order.append(
            "neo4j_task"
        )

        _make_service(mock_neo4j_project_repo, mock_publisher).delete_project(
            project.id, owner_id, ["pm"], uow
        )

        # Postgres must be committed (soft-delete) before the Neo4j task is enqueued.
        assert "pg_commit" in call_order
        assert call_order.index("pg_commit") < call_order.index("neo4j_task")
        # Soft-delete: project.deleted_at must be set, not hard-deleted.
        assert project.deleted_at is not None
        uow.projects.delete.assert_not_called()
        uow.commit.assert_called_once()
        mock_publisher.project_deleted.assert_called_once_with(str(project.id))

    def test_delete_neo4j_task_called_after_commit(self, uow, mock_neo4j_project_repo):
        """The publisher is always called — broker failures are Celery's concern."""
        owner_id = uuid.uuid4()
        project = make_project(owner_id=owner_id)
        uow.projects.get_by_uuid.return_value = project
        mock_publisher = MagicMock()

        _make_service(mock_neo4j_project_repo, mock_publisher).delete_project(
            project.id, owner_id, ["pm"], uow
        )

        uow.commit.assert_called_once()
        mock_publisher.project_deleted.assert_called_once()

    def test_delete_success_admin(self, uow, mock_neo4j_project_repo):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project
        mock_publisher = MagicMock()

        _make_service(mock_neo4j_project_repo, mock_publisher).delete_project(
            project.id, uuid.uuid4(), ["admin"], uow
        )

        # Soft-delete: deleted_at set, no hard delete called.
        assert project.deleted_at is not None
        uow.projects.delete.assert_not_called()
        mock_publisher.project_deleted.assert_called_once()

    def test_delete_admin_forbidden_other_tenant(self, uow, mock_neo4j_project_repo):
        project = make_project(tenant_id=uuid.uuid4())
        uow.projects.get_by_uuid.return_value = project

        with pytest.raises(ForbiddenError):
            _make_service(mock_neo4j_project_repo).delete_project(
                project.id,
                uuid.uuid4(),
                ["admin"],
                uow,
                requester_tenant_id=uuid.uuid4(),
            )

    def test_delete_not_found(self, uow, mock_neo4j_project_repo):
        uow.projects.get_by_uuid.return_value = None

        with pytest.raises(NotFoundError):
            _make_service(mock_neo4j_project_repo).delete_project(
                uuid.uuid4(), uuid.uuid4(), [], uow
            )

    def test_delete_forbidden(self, uow, mock_neo4j_project_repo):
        project = make_project()
        uow.projects.get_by_uuid.return_value = project

        with pytest.raises(ForbiddenError):
            _make_service(mock_neo4j_project_repo).delete_project(
                project.id, uuid.uuid4(), ["pm"], uow
            )


# ── assert_project_access (content-level RBAC primitive) ───────────────────


class TestAssertProjectAccess:
    """Covers the owner/super_admin/tenant-admin/ProjectMember matrix.

    ``level`` is one of "read"/"write"/"approve" — see
    ProjectService.assert_project_access's docstring for the exact rules.
    """

    def test_owner_passes_any_level(self, uow):
        owner_id = uuid.uuid4()
        project = make_project(owner_id=owner_id)

        for level in ("read", "write", "approve"):
            ProjectService.assert_project_access(project, owner_id, [], None, level, uow)

    def test_super_admin_has_no_bypass(self, uow):
        """super_admin has no special access to project content (sources,
        modules, features, user stories, incremental updates) — only owner,
        tenant-scoped admin, or an assigned ProjectMember do."""
        project = make_project()
        uow.project_members.list_roles_for_user.return_value = []

        for level in ("read", "write", "approve"):
            with pytest.raises(ForbiddenError):
                ProjectService.assert_project_access(
                    project, uuid.uuid4(), [ROLE_SUPER_ADMIN], None, level, uow
                )

    def test_tenant_admin_passes_any_level(self, uow):
        tenant_id = uuid.uuid4()
        project = make_project(tenant_id=tenant_id)

        for level in ("read", "write", "approve"):
            ProjectService.assert_project_access(
                project, uuid.uuid4(), [ROLE_ADMIN], tenant_id, level, uow
            )

    def test_admin_other_tenant_falls_through_to_membership(self, uow):
        project = make_project(tenant_id=uuid.uuid4())
        uow.project_members.list_roles_for_user.return_value = []

        with pytest.raises(ForbiddenError):
            ProjectService.assert_project_access(
                project, uuid.uuid4(), [ROLE_ADMIN], uuid.uuid4(), "read", uow
            )

    def test_no_membership_row_forbidden(self, uow):
        project = make_project()
        uow.project_members.list_roles_for_user.return_value = []

        with pytest.raises(ForbiddenError):
            ProjectService.assert_project_access(project, uuid.uuid4(), [], None, "read", uow)

    def test_member_row_grants_read_write_and_approve(self, uow):
        project = make_project()
        requester_id = uuid.uuid4()
        uow.project_members.list_roles_for_user.return_value = [MagicMock(role=ROLE_MEMBER)]

        # A single member row now grants every content access level, including
        # "approve" (the retired approver role's power folded into member).
        for level in ("read", "write", "approve"):
            ProjectService.assert_project_access(project, requester_id, [], None, level, uow)


class TestAssertLlmApiKeyConfigured:
    """Covers ``ProjectService.assert_llm_api_key_configured`` — requires an
    active AND verified tenant LLM-provider key, stricter than the plain
    ``get_active_api_key`` lookup used elsewhere."""

    @staticmethod
    def _make_row(**overrides):
        row = MagicMock()
        row.provider = "anthropic"
        row.is_active = True
        row.deleted_at = None
        row.api_key_encrypted = "ciphertext123456"
        row.is_verified = True
        for key, value in overrides.items():
            setattr(row, key, value)
        return row

    def test_noop_when_project_has_no_tenant(self, uow):
        project = make_project(tenant_id=None, llm_provider="anthropic")

        ProjectService.assert_llm_api_key_configured(project, uow)

        uow.tenants.get.assert_not_called()

    def test_noop_when_project_has_no_llm_provider(self, uow):
        project = make_project(tenant_id=uuid.uuid4(), llm_provider=None)

        ProjectService.assert_llm_api_key_configured(project, uow)

        uow.tenants.get.assert_not_called()

    def test_raises_when_key_not_verified(self, uow):
        tenant_id = uuid.uuid4()
        project = make_project(tenant_id=tenant_id, llm_provider="anthropic")
        row = self._make_row(is_verified=False)
        uow.tenants.get.return_value = MagicMock(llm_providers=[row])

        with pytest.raises(AppValidationError):
            ProjectService.assert_llm_api_key_configured(project, uow)

    def test_raises_when_no_key_configured(self, uow):
        tenant_id = uuid.uuid4()
        project = make_project(tenant_id=tenant_id, llm_provider="anthropic")
        uow.tenants.get.return_value = MagicMock(llm_providers=[])

        with pytest.raises(AppValidationError):
            ProjectService.assert_llm_api_key_configured(project, uow)

    def test_passes_when_key_active_and_verified(self, uow):
        tenant_id = uuid.uuid4()
        project = make_project(tenant_id=tenant_id, llm_provider="anthropic")
        row = self._make_row(is_verified=True)
        uow.tenants.get.return_value = MagicMock(llm_providers=[row])

        with patch(
            "app.services.tenant_llm_provider_service.decrypt_llm_api_key",
            return_value="sk-live-key",
        ):
            ProjectService.assert_llm_api_key_configured(project, uow)


class TestAssertTenantAdminOrSuperAdmin:
    """Covers the project-membership-management gate — no owner bypass."""

    def test_super_admin_passes(self):
        project = make_project()
        ProjectService.assert_tenant_admin_or_super_admin(project, [ROLE_SUPER_ADMIN], None)

    def test_tenant_admin_passes(self):
        tenant_id = uuid.uuid4()
        project = make_project(tenant_id=tenant_id)
        ProjectService.assert_tenant_admin_or_super_admin(project, [ROLE_ADMIN], tenant_id)

    def test_owner_without_admin_role_forbidden(self):
        owner_id = uuid.uuid4()
        project = make_project(owner_id=owner_id)

        with pytest.raises(ForbiddenError):
            ProjectService.assert_tenant_admin_or_super_admin(project, [], None)

    def test_admin_other_tenant_forbidden(self):
        project = make_project(tenant_id=uuid.uuid4())

        with pytest.raises(ForbiddenError):
            ProjectService.assert_tenant_admin_or_super_admin(project, [ROLE_ADMIN], uuid.uuid4())


class TestResolveDashboardScope:
    """super_admin -> unscoped; admin -> own tenant; member -> owned+assigned."""

    def test_super_admin_returns_none(self, uow):
        result = ProjectService._resolve_dashboard_scope(
            uow, uuid.uuid4(), [ROLE_SUPER_ADMIN], None
        )
        assert result is None

    def test_admin_returns_tenant_project_ids(self, uow):
        tenant_id = uuid.uuid4()
        ids = [uuid.uuid4(), uuid.uuid4()]
        uow.projects.list_ids_by_tenant.return_value = ids

        result = ProjectService._resolve_dashboard_scope(uow, uuid.uuid4(), [ROLE_ADMIN], tenant_id)

        assert result == ids
        uow.projects.list_ids_by_tenant.assert_called_once_with(tenant_id)

    def test_admin_without_tenant_returns_empty(self, uow):
        result = ProjectService._resolve_dashboard_scope(uow, uuid.uuid4(), [ROLE_ADMIN], None)
        assert result == []

    def test_member_returns_owned_or_member_ids(self, uow):
        requester_id = uuid.uuid4()
        ids = [uuid.uuid4()]
        uow.projects.list_ids_owned_or_member.return_value = ids

        result = ProjectService._resolve_dashboard_scope(uow, requester_id, [ROLE_MEMBER], None)

        assert result == ids
        uow.projects.list_ids_owned_or_member.assert_called_once_with(requester_id)

    def test_super_admin_with_tenant_filter_narrows_to_one_tenant(self, uow):
        chosen_tenant_id = uuid.uuid4()
        ids = [uuid.uuid4()]
        uow.projects.list_ids_by_tenant.return_value = ids

        result = ProjectService._resolve_dashboard_scope(
            uow, uuid.uuid4(), [ROLE_SUPER_ADMIN], None, tenant_id_filter=chosen_tenant_id
        )

        assert result == ids
        uow.projects.list_ids_by_tenant.assert_called_once_with(chosen_tenant_id)

    def test_admin_ignores_tenant_filter(self, uow):
        """A plain admin's tenant_id query param is ignored — always scoped
        to their own tenant regardless of what they pass."""
        own_tenant_id = uuid.uuid4()
        other_tenant_id = uuid.uuid4()
        ids = [uuid.uuid4()]
        uow.projects.list_ids_by_tenant.return_value = ids

        result = ProjectService._resolve_dashboard_scope(
            uow, uuid.uuid4(), [ROLE_ADMIN], own_tenant_id, tenant_id_filter=other_tenant_id
        )

        assert result == ids
        uow.projects.list_ids_by_tenant.assert_called_once_with(own_tenant_id)

    def test_member_ignores_tenant_filter(self, uow):
        requester_id = uuid.uuid4()
        ids = [uuid.uuid4()]
        uow.projects.list_ids_owned_or_member.return_value = ids

        result = ProjectService._resolve_dashboard_scope(
            uow, requester_id, [ROLE_MEMBER], None, tenant_id_filter=uuid.uuid4()
        )

        assert result == ids
        uow.projects.list_ids_owned_or_member.assert_called_once_with(requester_id)


class TestGetDashboardStats:
    @staticmethod
    def _completion_stats(**overrides) -> dict:
        defaults = {
            "avg_seconds": None,
            "longest_project_id": None,
            "longest_project_name": None,
            "longest_duration_seconds": None,
        }
        defaults.update(overrides)
        return defaults

    @pytest.mark.asyncio
    async def test_super_admin_gets_unscoped_global_counts(self, uow, mock_neo4j_project_repo):
        uow.project_stats = MagicMock()
        uow.project_stats.count_total_projects.return_value = 15
        uow.project_stats.count_active_projects.return_value = 10
        uow.project_stats.count_active_projects_since.return_value = 3
        uow.project_stats.count_running_pipelines.return_value = 2
        uow.project_stats.get_pipeline_completion_stats.return_value = self._completion_stats(
            avg_seconds=12.5
        )
        mock_neo4j_project_repo.get_global_module_feature_story_counts.return_value = {
            "total_modules": 5,
            "total_features": 8,
            "total_stories": 20,
            "approved_stories": 12,
            "pending_jira_sync_stories": 8,
            "pending_tap_sync_stories": 5,
        }

        service = _make_service(mock_neo4j_project_repo)
        result = await service.get_dashboard_stats(
            uow, requester_id=uuid.uuid4(), requester_roles=[ROLE_SUPER_ADMIN]
        )

        assert result.total_projects == 15
        assert result.active_projects == 10
        assert result.total_modules == 5
        assert result.approved_user_stories == 12
        assert result.pending_tap_sync_count == 5
        assert result.pending_jira_sync_count == 8
        uow.project_stats.count_total_projects.assert_called_once_with(project_ids=None)
        uow.project_stats.count_active_projects.assert_called_once_with(project_ids=None)
        mock_neo4j_project_repo.get_global_module_feature_story_counts.assert_called_once()
        mock_neo4j_project_repo.get_module_feature_story_counts_for_projects.assert_not_called()

    @pytest.mark.asyncio
    async def test_super_admin_with_tenant_id_narrows_scope(self, uow, mock_neo4j_project_repo):
        chosen_tenant_id = uuid.uuid4()
        project_ids = [uuid.uuid4()]
        uow.projects.list_ids_by_tenant.return_value = project_ids
        uow.project_stats = MagicMock()
        uow.project_stats.count_total_projects.return_value = 1
        uow.project_stats.count_active_projects.return_value = 1
        uow.project_stats.count_active_projects_since.return_value = 1
        uow.project_stats.count_running_pipelines.return_value = 0
        uow.project_stats.get_pipeline_completion_stats.return_value = self._completion_stats()
        mock_neo4j_project_repo.get_module_feature_story_counts_for_projects.return_value = {
            "total_modules": 1,
            "total_features": 1,
            "total_stories": 1,
            "approved_stories": 1,
            "pending_jira_sync_stories": 1,
            "pending_tap_sync_stories": 1,
        }

        service = _make_service(mock_neo4j_project_repo)
        result = await service.get_dashboard_stats(
            uow,
            requester_id=uuid.uuid4(),
            requester_roles=[ROLE_SUPER_ADMIN],
            tenant_id=chosen_tenant_id,
        )

        assert result.total_projects == 1
        assert result.active_projects == 1
        assert result.pending_jira_sync_count == 1
        uow.projects.list_ids_by_tenant.assert_called_once_with(chosen_tenant_id)
        uow.project_stats.count_total_projects.assert_called_once_with(project_ids=project_ids)
        uow.project_stats.count_active_projects.assert_called_once_with(project_ids=project_ids)
        mock_neo4j_project_repo.get_module_feature_story_counts_for_projects.assert_called_once_with(
            project_ids
        )
        mock_neo4j_project_repo.get_global_module_feature_story_counts.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_gets_tenant_scoped_counts(self, uow, mock_neo4j_project_repo):
        tenant_id = uuid.uuid4()
        project_ids = [uuid.uuid4(), uuid.uuid4()]
        uow.projects.list_ids_by_tenant.return_value = project_ids
        uow.project_stats = MagicMock()
        uow.project_stats.count_total_projects.return_value = 6
        uow.project_stats.count_active_projects.return_value = 4
        uow.project_stats.count_active_projects_since.return_value = 1
        uow.project_stats.count_running_pipelines.return_value = 0
        uow.project_stats.get_pipeline_completion_stats.return_value = self._completion_stats()
        mock_neo4j_project_repo.get_module_feature_story_counts_for_projects.return_value = {
            "total_modules": 2,
            "total_features": 3,
            "total_stories": 6,
            "approved_stories": 4,
            "pending_jira_sync_stories": 3,
            "pending_tap_sync_stories": 2,
        }

        service = _make_service(mock_neo4j_project_repo)
        result = await service.get_dashboard_stats(
            uow,
            requester_id=uuid.uuid4(),
            requester_roles=[ROLE_ADMIN],
            requester_tenant_id=tenant_id,
        )

        assert result.total_projects == 6
        assert result.active_projects == 4
        assert result.total_modules == 2
        assert result.approved_user_stories == 4
        assert result.pending_tap_sync_count == 2
        assert result.pending_jira_sync_count == 3
        uow.projects.list_ids_by_tenant.assert_called_once_with(tenant_id)
        uow.project_stats.count_total_projects.assert_called_once_with(project_ids=project_ids)
        uow.project_stats.count_active_projects.assert_called_once_with(project_ids=project_ids)
        mock_neo4j_project_repo.get_module_feature_story_counts_for_projects.assert_called_once_with(
            project_ids
        )
        mock_neo4j_project_repo.get_global_module_feature_story_counts.assert_not_called()

    @pytest.mark.asyncio
    async def test_member_gets_owned_and_assigned_scoped_counts(self, uow, mock_neo4j_project_repo):
        requester_id = uuid.uuid4()
        project_ids = [uuid.uuid4()]
        uow.projects.list_ids_owned_or_member.return_value = project_ids
        uow.project_stats = MagicMock()
        uow.project_stats.count_total_projects.return_value = 1
        uow.project_stats.count_active_projects.return_value = 1
        uow.project_stats.count_active_projects_since.return_value = 1
        uow.project_stats.count_running_pipelines.return_value = 0
        uow.project_stats.get_pipeline_completion_stats.return_value = self._completion_stats()
        mock_neo4j_project_repo.get_module_feature_story_counts_for_projects.return_value = {
            "total_modules": 1,
            "total_features": 1,
            "total_stories": 1,
            "approved_stories": 0,
            "pending_jira_sync_stories": 0,
            "pending_tap_sync_stories": 0,
        }

        service = _make_service(mock_neo4j_project_repo)
        result = await service.get_dashboard_stats(
            uow, requester_id=requester_id, requester_roles=[ROLE_MEMBER]
        )

        assert result.total_projects == 1
        assert result.active_projects == 1
        assert result.pending_tap_sync_count == 0
        assert result.pending_jira_sync_count == 0
        uow.projects.list_ids_owned_or_member.assert_called_once_with(requester_id)
        mock_neo4j_project_repo.get_module_feature_story_counts_for_projects.assert_called_once_with(
            project_ids
        )

    @pytest.mark.asyncio
    async def test_neo4j_failure_defaults_graph_counts_to_zero(self, uow, mock_neo4j_project_repo):
        uow.project_stats = MagicMock()
        uow.project_stats.count_total_projects.return_value = 0
        uow.project_stats.count_active_projects.return_value = 0
        uow.project_stats.count_active_projects_since.return_value = 0
        uow.project_stats.count_running_pipelines.return_value = 0
        uow.project_stats.get_pipeline_completion_stats.return_value = self._completion_stats()
        mock_neo4j_project_repo.get_global_module_feature_story_counts.side_effect = Exception(
            "boom"
        )

        service = _make_service(mock_neo4j_project_repo)
        result = await service.get_dashboard_stats(
            uow, requester_id=uuid.uuid4(), requester_roles=[ROLE_SUPER_ADMIN]
        )

        assert result.total_modules == 0
        assert result.total_features == 0
        assert result.total_stories == 0
        assert result.approved_user_stories == 0
        assert result.pending_tap_sync_count == 0
        assert result.pending_jira_sync_count == 0
