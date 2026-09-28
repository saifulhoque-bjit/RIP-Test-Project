"""Shared pytest fixtures for the entire test-suite."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, MagicMock
import uuid

import pytest

# ── Celery task isolation ──────────────────────────────────────────────────


def _make_task_uow_mock() -> MagicMock:
    """Build a UnitOfWork context-manager mock for internal task DB writes in services."""
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    mock.project_tasks = MagicMock()
    mock.project_tasks.get_by_id = MagicMock(return_value=None)
    mock.add = MagicMock()
    mock.flush = MagicMock()
    mock.refresh = MagicMock()
    mock.commit = MagicMock()
    return mock


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the shared SlowAPI limiter's in-memory storage before each test.

    ``app.core.rate_limiter.limiter`` is a module-level singleton shared by
    the whole test process. Route-level unit tests call `@limiter.limit(...)`
    decorated handlers directly with a bare ``Request(scope={"path": "/", ...})``
    (see each test file's local ``_make_request()`` helper) — since every
    such request shares the same literal path and no distinguishing client
    IP, they all land in the same rate-limit bucket. Without a reset, hit
    counts accumulate across unrelated test files for the entire session and
    an otherwise-unrelated test can fail with a 429 once enough other tests
    have called a rate-limited route before it.
    """
    from app.core.rate_limiter import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(autouse=True)
def _no_celery_dispatch(monkeypatch):
    """Prevent Celery tasks from being dispatched to the Redis broker during tests.

    ``source_service._enqueue_processing`` is the only confirmed unguarded
    dispatch site: it is called by ``upload_bulk`` after a successful upload
    and fires ``process_source_task.apply_async()`` without any broker check.

    Tests that need to assert on task dispatch patch the specific task's
    ``apply_async`` themselves (e.g. test_module_feature_service.py).
    """
    monkeypatch.setattr(
        "app.services.source_service._enqueue_processing",
        lambda *args, **kwargs: None,
    )
    # Prevent module/user story services from hitting the real DB for task creation
    _task_uow_mock = _make_task_uow_mock()
    monkeypatch.setattr(
        "app.services.module_feature_service.UnitOfWork",
        lambda: _task_uow_mock,
    )
    monkeypatch.setattr(
        "app.services.user_story_service.UnitOfWork",
        lambda: _task_uow_mock,
    )
    monkeypatch.setattr(
        "app.services.project_task_service.UnitOfWork",
        lambda: _task_uow_mock,
    )
    # Prevent Redis publish calls during tests
    monkeypatch.setattr(
        "app.websockets.manager.publish_task_event_sync",
        lambda **kwargs: None,
    )
    # EmailService (app/services/email_service.py) unconditionally dispatches
    # via Celery — used by TenantService.create and the invitation routes.
    # Block the real broker call; tests asserting on the email content
    # override this locally (see tests/test_email_service.py).
    monkeypatch.setattr(
        "app.services.email_service.send_notification_email.delay",
        lambda **kwargs: None,
    )

    # ── Task-cancellation control-plane isolation ──────────────────────────
    # app/core/task_control.py opens a real Redis connection on every call.
    # Every Celery task entry point now calls it cooperatively, so without
    # this, any test exercising a task body makes a real (slow, environment-
    # dependent) network call. Default to "never cancelled"; tests that
    # exercise cancellation itself patch `is_request_cancelled` explicitly.
    monkeypatch.setattr("app.core.task_control.is_request_cancelled", lambda request_id: False)
    monkeypatch.setattr("app.core.task_control.mark_request_cancelled", lambda request_id: None)
    monkeypatch.setattr("app.core.task_control.clear_request_cancelled", lambda request_id: None)
    # Default to "not a duplicate delivery" so tests that pass a task_db_id
    # through _parse_code_task's redelivery-dedup checkpoint don't make a
    # real Redis call; tests exercising the dedup behavior itself override
    # this locally.
    monkeypatch.setattr(
        "app.core.task_control.claim_task_execution", lambda task_id, *args, **kwargs: True
    )
    # Same isolation for the poison-loop delivery-count guard used by
    # process_single_module/persist_single_module (CELERY_RETRY_POLICY.docx
    # Section A): default to "within limit" so tests don't make a real Redis
    # call, and — since the guard counts cumulatively — don't leak an
    # ever-incrementing counter into other tests via a shared dev Redis.
    # Tests exercising the guard itself override this locally.
    monkeypatch.setattr(
        "app.core.task_control.register_delivery_within_limit",
        lambda task_id, *args, **kwargs: True,
    )

    # ── Neo4j isolation ───────────────────────────────────────────────────
    # Block every call path that would open a real Neo4j connection.
    # Each entry covers a different import site so no test can accidentally
    # write Project / Source / Module / Feature / UserStory / Fragment nodes
    # to a live database.
    _mock_driver = MagicMock()
    _no_op_driver = lambda: _mock_driver  # noqa: E731
    # Source: covers deferred imports inside worker functions
    monkeypatch.setattr("app.db.neo4j.get_neo4j_driver", _no_op_driver)
    # Module-level imported references in each consumer module
    monkeypatch.setattr("app.services.source_service.get_neo4j_driver", _no_op_driver)
    monkeypatch.setattr(
        "app.repositories.neo4j.module_feature_repository.get_neo4j_driver", _no_op_driver
    )
    monkeypatch.setattr(
        "app.repositories.neo4j.fragment_repository.get_neo4j_driver", _no_op_driver
    )
    monkeypatch.setattr(
        "app.repositories.neo4j.user_story_repository.get_neo4j_driver", _no_op_driver
    )


from app.db.unit_of_work import UnitOfWork
from app.models.postgres.project_model import Project
from app.models.postgres.source_model import Source
from app.models.postgres.user_model import User

# ── Source context helper ─────────────────────────────────────────────────


class SourceFileParams:
    """Optional file-metadata overrides for make_source."""

    __slots__ = (
        "relative_path",
        "storage_url",
        "file_size_bytes",
        "mime_type",
        "file_type",
        "upload_type",
        "batch_id",
        "source_type",
        "status",
        "checksum_sha256",
        "link_url",
    )

    def __init__(
        self,
        relative_path: str | None = None,
        storage_url: str | None = "https://s3.example.com/presigned",
        file_size_bytes: int = 1024,
        mime_type: str = "application/pdf",
        file_type: str = "PDF",
        upload_type: str = "single",
        batch_id: uuid.UUID | None = None,
        source_type: str | None = "rfp",
        status: str = "uploaded",
        checksum_sha256: str | None = "abc123",
        link_url: str | None = None,
    ) -> None:
        self.relative_path = relative_path
        self.storage_url = storage_url
        self.file_size_bytes = file_size_bytes
        self.mime_type = mime_type
        self.file_type = file_type
        self.upload_type = upload_type
        self.batch_id = batch_id
        self.source_type = source_type
        self.status = status
        self.checksum_sha256 = checksum_sha256
        self.link_url = link_url


# ── UnitOfWork mock ────────────────────────────────────────────────────────


def _make_uow() -> MagicMock:
    uow = MagicMock(spec=UnitOfWork)
    uow.users = MagicMock()
    uow.roles = MagicMock()
    uow.permissions = MagicMock()
    uow.projects = MagicMock()
    # Default project passes ProjectService.assert_project_access's owner
    # check for any requester_id/tenant_id (unittest.mock.ANY == anything) —
    # most tests here exercise business logic, not authorization, and
    # predate project-level access control. llm_provider=None keeps
    # ProjectService.assert_llm_api_key_configured a no-op by default (same
    # reasoning — most tests here predate that check too). Tests that
    # specifically cover authorization or LLM-key validation configure their
    # own Project/ProjectMember mocks.
    uow.projects.get_by_uuid.return_value = MagicMock(
        owner_id=ANY, tenant_id=ANY, llm_provider=None
    )
    uow.project_members = MagicMock()
    uow.project_members.list_roles_for_user.return_value = []
    uow.project_members.list_by_project.return_value = []
    uow.sources = MagicMock()
    # Default to "no duplicate" for both dedup lookups — tests covering the
    # duplicate path override these explicitly with a Source/None as needed.
    uow.sources.get_by_checksum.return_value = None
    uow.sources.get_by_filename.return_value = None
    uow.fragment_embeddings = MagicMock()
    uow.fragment_embeddings.delete_by_source_id = MagicMock(return_value=0)
    uow.source_ingestions = MagicMock()
    uow.source_ingestions.create_ingestion = MagicMock(return_value=MagicMock(id=uuid.uuid4()))
    uow.source_ingestions.list_open_feedback_or_incremental_by_project.return_value = []
    uow.source_ingestions.get_ready_for_review_generation_ingestion.return_value = None
    uow.source_ingestions.has_unresolved_feedback_or_incremental.return_value = False
    uow.source_ingestions.list_running_by_project.return_value = []
    uow.project_tasks = MagicMock()
    uow.incremental_histories = MagicMock()
    uow.incremental_histories.list_by_project.return_value = []
    uow.task_events = MagicMock()
    uow.project_stats = MagicMock()
    uow.story_feedback_histories = MagicMock()
    uow.notifications = MagicMock()
    uow.activity_logs = MagicMock()
    uow.tenants = MagicMock()
    uow.tenants.increment_project_sequence.return_value = ("TEN", 1)
    uow.projects.next_untenanted_code_sequence.return_value = 1
    uow.tenant_stats = MagicMock()
    uow.invitations = MagicMock()
    uow.invitations.count_by_role_id.return_value = 0
    uow.jira_integrations = MagicMock()
    uow.jira_sync_mappings = MagicMock()
    uow.jira_sync_history = MagicMock()
    uow.tap_sync_mappings = MagicMock()
    uow.tap_sync_history = MagicMock()
    uow.tap_ack_history = MagicMock()
    uow.add = MagicMock()
    uow.flush = MagicMock()
    uow.refresh = MagicMock()
    uow.commit = MagicMock()
    uow.rollback = MagicMock()
    return uow


@pytest.fixture
def uow() -> MagicMock:
    return _make_uow()


@pytest.fixture
def mock_neo4j_project_repo() -> MagicMock:
    """Return a MagicMock that stands in for Neo4jProjectRepository.

    Used by tests that instantiate ProjectService directly; avoids any
    real Neo4j driver call.
    """
    repo = MagicMock()
    repo.upsert_project_node = MagicMock(return_value=None)
    repo.delete_project_graph_sync = MagicMock(return_value=1)
    # Default: no user stories for any project (override per-test as needed)
    repo.get_user_story_counts = MagicMock(return_value={})
    repo.get_progress_flags = MagicMock(return_value={})
    _empty_graph_counts = {"total_modules": 0, "total_features": 0, "total_stories": 0}
    repo.get_global_module_feature_story_counts = MagicMock(return_value=_empty_graph_counts)
    repo.get_module_feature_story_counts_for_projects = MagicMock(return_value=_empty_graph_counts)
    return repo


@pytest.fixture
def mock_module_feature_repo() -> MagicMock:
    """Return a MagicMock standing in for ModuleFeatureRepository.

    Used by tests that instantiate ProjectService directly; avoids any real
    Neo4j driver call. Defaults to "fully approved" so tests that don't care
    about the approval-gate fields aren't forced to configure it.
    """
    repo = MagicMock()
    repo.are_all_modules_approved = AsyncMock(return_value=True)
    repo.are_all_features_approved = AsyncMock(return_value=True)
    return repo


@pytest.fixture
def mock_user_story_repo() -> MagicMock:
    """Return a MagicMock standing in for UserStoryRepository (project-service tests).

    See :func:`mock_module_feature_repo` — same rationale, defaults to
    "fully approved".
    """
    repo = MagicMock()
    repo.are_all_user_stories_approved = AsyncMock(return_value=True)
    return repo


def _make_mock_setting_service() -> MagicMock:
    """Build a MagicMock standing in for SettingService.

    Backs the Neo4j-derived llm_providers→models catalog used by
    ProjectService's llm_model/llm_provider cross-validation, with entries
    covering the provider/model combinations exercised by
    test_project_service.py — avoids any real Neo4j driver call.
    """
    from app.schemas.setting_schema import (
        LlmModel,
        LlmProvider,
        ProjectSettings,
        SettingResponse,
        SourceCodePipelineSettings,
    )

    service = MagicMock()
    service.get_or_seed_default_settings.return_value = SettingResponse(
        project=ProjectSettings(
            llm_providers=[
                LlmProvider(
                    id="anthropic",
                    name="Anthropic",
                    models=[LlmModel(id="claude_sonnet_4_6", name="Claude Sonnet 4.6")],
                ),
                LlmProvider(
                    id="openai",
                    name="OpenAI",
                    models=[LlmModel(id="gpt-5", name="GPT-5")],
                ),
                LlmProvider(id="google", name="Google", models=[]),
                LlmProvider(id="deepseek", name="DeepSeek", models=[]),
            ]
        ),
        source_code_pipeline=SourceCodePipelineSettings(
            source_languages=[],
            frontend_stacks=[],
            backend_stacks=[],
            database_stacks=[],
            infrastructure_stacks=[],
            architecture_stacks=[],
        ),
    )
    return service


@pytest.fixture
def mock_setting_service() -> MagicMock:
    return _make_mock_setting_service()


# ── Model factories ────────────────────────────────────────────────────────


def make_user(
    *,
    name: str | None = "Test User",
    email: str = "test@example.com",
    cognito_sub: str | None = None,
    is_active: bool = True,
    is_verified: bool = True,
    roles: list | None = None,
    tenant_id: uuid.UUID | None = None,
) -> User:
    u = User()
    u.id = uuid.uuid4()
    u.cognito_sub = cognito_sub or str(uuid.uuid4())
    u.email = email
    u.name = name
    u.is_active = is_active
    u.is_verified = is_verified
    u.roles = roles if roles is not None else []
    u.tenant_id = tenant_id
    u.created_at = datetime.now(tz=UTC)
    u.updated_at = datetime.now(tz=UTC)
    return u


def make_project(
    *,
    name: str = "Test Project",
    code: str = "TEST-0001",
    description: str | None = "A test project",
    llm_provider: str | None = None,
    llm_model: str | None = None,
    status: str = "active",
    owner_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    version: int = 1,
) -> Project:
    p = Project()
    p.id = uuid.uuid4()
    p.name = name
    p.code = code
    p.description = description
    p.llm_provider = llm_provider
    p.llm_model = llm_model
    p.status = status
    p.owner_id = owner_id or uuid.uuid4()
    p.tenant_id = tenant_id
    p.version = version
    p.created_at = datetime.now(tz=UTC)
    p.updated_at = datetime.now(tz=UTC)
    return p


def make_source(
    *,
    project_id: uuid.UUID | None = None,
    original_name: str = "document.pdf",
    storage_key: str | None = "sources/proj/src_document.pdf",
    file_params: SourceFileParams | None = None,
    is_deleted: bool = False,
    created_by: uuid.UUID | None = None,
) -> Source:
    fp = file_params or SourceFileParams()
    s = Source()
    s.id = uuid.uuid4()
    s.project_id = project_id or uuid.uuid4()
    s.original_name = original_name
    s.relative_path = fp.relative_path
    s.storage_key = storage_key
    s.storage_url = fp.storage_url
    s.file_size_bytes = fp.file_size_bytes
    s.mime_type = fp.mime_type
    s.file_type = fp.file_type
    s.upload_type = fp.upload_type
    s.batch_id = fp.batch_id
    s.source_type = fp.source_type
    s.status = fp.status
    s.checksum_sha256 = fp.checksum_sha256
    s.link_url = fp.link_url
    s.is_deleted = is_deleted
    s.created_by = created_by or uuid.uuid4()
    s.created_at = datetime.now(tz=UTC)
    s.updated_at = datetime.now(tz=UTC)
    # Provide a mock uploader for SourceResponse.resolve_created_by validator
    uploader = MagicMock()
    uploader.id = s.created_by
    uploader.name = "Test User"
    s.uploader = uploader
    return s
