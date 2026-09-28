"""Read-only repository for dashboard / stats aggregations.

Responsibilities:
- Cross-table read queries required by the dashboard stats endpoint
- Returns plain primitives and dicts (no ORM object assembly)

NOT responsible for:
- Any write operations
- Business logic or date calculations (those belong to the service layer)
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.constants import SOURCE_STATUS_COMPLETED
from app.core.enums.project_status import ProjectStatus
from app.core.enums.source_ingestion_status import SourceIngestionStatus
from app.models.postgres.project_model import Project
from app.models.postgres.project_task_model import ProjectTask
from app.models.postgres.source_ingestion_model import SourceIngestion


class ProjectStatsRepository:
    """Aggregation queries for the dashboard stats endpoint.

    All methods are read-only and may join across tables.  Keeping them here
    ensures entity repositories (ProjectRepository, ProjectTaskRepository)
    stay focused on single-model CRUD / lookups.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def count_active_projects(self, *, project_ids: list[UUID] | None = None) -> int:
        """Count active, non-deleted projects.

        ``project_ids=None`` means unscoped/platform-wide (super_admin only
        — see ``ProjectService._resolve_dashboard_scope``); otherwise scopes
        to that explicit set (a Client Admin's tenant, or a Member's owned +
        assigned projects).
        """
        query = self._session.query(func.count(Project.id)).filter(
            Project.deleted_at.is_(None),
            Project.status == ProjectStatus.ACTIVE.value,
        )
        if project_ids is not None:
            query = query.filter(Project.id.in_(project_ids))
        return query.scalar() or 0

    def count_total_projects(self, *, project_ids: list[UUID] | None = None) -> int:
        """Count non-deleted projects regardless of status.

        See :meth:`count_active_projects` for the ``project_ids`` scoping
        contract — same rule, just without the ``status == ACTIVE`` filter
        (includes active/inactive/archived).
        """
        query = self._session.query(func.count(Project.id)).filter(
            Project.deleted_at.is_(None),
        )
        if project_ids is not None:
            query = query.filter(Project.id.in_(project_ids))
        return query.scalar() or 0

    def count_projects_by_tenant_ids(self, tenant_ids: list[UUID]) -> dict[UUID, int]:
        """Return a per-tenant non-deleted project count for *tenant_ids*.

        A tenant with no projects is simply absent from the returned dict
        (callers should default to 0). A single grouped query is used so a
        tenant list page doesn't pay one round-trip per row.
        """
        if not tenant_ids:
            return {}
        rows = (
            self._session.query(Project.tenant_id, func.count(Project.id))
            .filter(Project.tenant_id.in_(tenant_ids), Project.deleted_at.is_(None))
            .group_by(Project.tenant_id)
            .all()
        )
        return dict(rows)

    def count_active_projects_since(
        self, since: datetime, *, project_ids: list[UUID] | None = None
    ) -> int:
        """Count active, non-deleted projects created on or after *since*.

        See :meth:`count_active_projects` for the ``project_ids`` scoping contract.
        """
        query = self._session.query(func.count(Project.id)).filter(
            Project.deleted_at.is_(None),
            Project.status == ProjectStatus.ACTIVE.value,
            Project.created_at >= since,
        )
        if project_ids is not None:
            query = query.filter(Project.id.in_(project_ids))
        return query.scalar() or 0

    def count_running_pipelines(self, *, project_ids: list[UUID] | None = None) -> int:
        """Count ingestion pipelines that are actively running (not yet ready for review or completed).

        See :meth:`count_active_projects` for the ``project_ids`` scoping contract.
        """
        query = self._session.query(func.count(SourceIngestion.id)).filter(
            SourceIngestion.status == SourceIngestionStatus.RUNNING.value,
            SourceIngestion.deleted_at.is_(None),
        )
        if project_ids is not None:
            query = query.filter(SourceIngestion.project_id.in_(project_ids))
        return query.scalar() or 0

    def get_pipeline_completion_stats(self, *, project_ids: list[UUID] | None = None) -> dict:
        """Return avg completion time and the project with the longest completed task.

        See :meth:`count_active_projects` for the ``project_ids`` scoping contract.

        Returns a dict with keys:
            avg_seconds: float | None
            longest_project_id: UUID | None
            longest_project_name: str | None
            longest_duration_seconds: float | None
        """
        duration_expr = func.extract("epoch", ProjectTask.updated_at - ProjectTask.created_at)

        avg_query = self._session.query(func.avg(duration_expr)).filter(
            ProjectTask.status == SOURCE_STATUS_COMPLETED
        )
        longest_query = (
            self._session.query(
                ProjectTask.project_id,
                Project.name.label("project_name"),
                duration_expr.label("duration_seconds"),
            )
            .join(Project, ProjectTask.project_id == Project.id)
            .filter(ProjectTask.status == SOURCE_STATUS_COMPLETED)
        )
        if project_ids is not None:
            avg_query = avg_query.filter(ProjectTask.project_id.in_(project_ids))
            longest_query = longest_query.filter(ProjectTask.project_id.in_(project_ids))

        avg_seconds = avg_query.scalar()
        longest = longest_query.order_by(duration_expr.desc()).first()

        return {
            "avg_seconds": float(avg_seconds) if avg_seconds is not None else None,
            "longest_project_id": longest.project_id if longest else None,
            "longest_project_name": longest.project_name if longest else None,
            "longest_duration_seconds": float(longest.duration_seconds) if longest else None,
        }
