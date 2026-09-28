"""Unit of Work — owns the SQLAlchemy Session and all repositories.

Transaction flow:
    uow.add(entity)   → stages object
    uow.flush()       → writes to DB within the open transaction (no commit)
    uow.refresh(obj)  → reloads DB-generated fields (e.g. id, created_at)

Commit / rollback are handled automatically by the context manager:
    __exit__ with no exception → commit, then close
    __exit__ with exception    → rollback, then close

Explicit commit rule
────────────────────
Call ``uow.commit()`` explicitly whenever the service must perform operations
on external systems that depend on the Postgres data being durably written
first — e.g. enqueueing a Celery task, calling S3, or sending a webhook.
The context-manager auto-commit on ``__exit__`` becomes a safe no-op in
that case (nothing left to commit).

For read-only flows or simple write flows with no external side-effects,
letting the context-manager auto-commit is fine.

Use ``uow.flush()`` when you need DB-generated values (e.g. auto PKs) to be
visible within the same transaction before the context exits.

``expire_on_commit=False`` is set on the session factory so all ORM objects
remain fully usable after the session closes.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.repositories.postgres.activity_log_repository import ActivityLogRepository
from app.repositories.postgres.fragment_embedding_repository import FragmentEmbeddingRepository
from app.repositories.postgres.incremental_history_repository import IncrementalHistoryRepository
from app.repositories.postgres.invitation_repository import InvitationRepository
from app.repositories.postgres.jira_integration_repository import JiraIntegrationRepository
from app.repositories.postgres.jira_sync_history_repository import JiraSyncHistoryRepository
from app.repositories.postgres.jira_sync_mapping_repository import JiraSyncMappingRepository
from app.repositories.postgres.notification_repository import NotificationRepository
from app.repositories.postgres.permission_repository import PermissionRepository
from app.repositories.postgres.project_member_repository import ProjectMemberRepository
from app.repositories.postgres.project_repository import ProjectRepository
from app.repositories.postgres.project_stats_repository import ProjectStatsRepository
from app.repositories.postgres.project_task_event_repository import ProjectTaskEventRepository
from app.repositories.postgres.project_task_repository import ProjectTaskRepository
from app.repositories.postgres.role_repository import RoleRepository
from app.repositories.postgres.source_ingestion_repository import SourceIngestionRepository
from app.repositories.postgres.source_repository import SourceRepository
from app.repositories.postgres.story_feedback_history_repository import (
    StoryFeedbackHistoryRepository,
)
from app.repositories.postgres.tap_ack_history_repository import TapAckHistoryRepository
from app.repositories.postgres.tap_integration_repository import TapIntegrationRepository
from app.repositories.postgres.tap_sync_history_repository import TapSyncHistoryRepository
from app.repositories.postgres.tap_sync_mapping_repository import TapSyncMappingRepository
from app.repositories.postgres.tenant_repository import TenantRepository
from app.repositories.postgres.tenant_stats_repository import TenantStatsRepository
from app.repositories.postgres.user_repository import UserRepository


class UnitOfWork:
    def __init__(self) -> None:
        self._session: Session | None = None

    # ── Context manager ────────────────────────────────────────────────────

    def __enter__(self) -> UnitOfWork:
        self._session = SessionLocal()
        self.users = UserRepository(self._session)
        self.roles = RoleRepository(self._session)
        self.permissions = PermissionRepository(self._session)
        self.projects = ProjectRepository(self._session)
        self.project_members = ProjectMemberRepository(self._session)
        self.sources = SourceRepository(self._session)
        self.source_ingestions = SourceIngestionRepository(self._session)
        self.fragment_embeddings = FragmentEmbeddingRepository(self._session)
        self.incremental_histories = IncrementalHistoryRepository(self._session)
        self.project_tasks = ProjectTaskRepository(self._session)
        self.task_events = ProjectTaskEventRepository(self._session)
        self.project_stats = ProjectStatsRepository(self._session)
        self.story_feedback_histories = StoryFeedbackHistoryRepository(self._session)
        self.notifications = NotificationRepository(self._session)
        self.activity_logs = ActivityLogRepository(self._session)
        self.tenants = TenantRepository(self._session)
        self.tenant_stats = TenantStatsRepository(self._session)
        self.invitations = InvitationRepository(self._session)
        self.jira_integrations = JiraIntegrationRepository(self._session)
        self.jira_sync_mappings = JiraSyncMappingRepository(self._session)
        self.jira_sync_history = JiraSyncHistoryRepository(self._session)
        self.tap_sync_mappings = TapSyncMappingRepository(self._session)
        self.tap_sync_history = TapSyncHistoryRepository(self._session)
        self.tap_ack_history = TapAckHistoryRepository(self._session)
        self.tap_integrations = TapIntegrationRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()

    # ── Transaction helpers ────────────────────────────────────────────────

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("UnitOfWork session is not open")
        return self._session

    def add(self, entity: object) -> None:
        self.session.add(entity)

    def flush(self) -> None:
        self.session.flush()

    def refresh(self, entity: object) -> None:
        self.session.refresh(entity)

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
