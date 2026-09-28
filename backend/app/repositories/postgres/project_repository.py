"""Domain-specific repository for the Project model.

Responsibilities:
- Create, Update, Delete operations
- Get by ID / name / owner lookups
- List, Search, paginated queries
- Transaction participation (flush within the open session; never commit)

NOT responsible for:
- Neo4j / graph queries (no cross-store imports)
- Business invariants (name-conflict checks belong to the service)
- Response DTO assembly (belongs to the service)
- Cross-table aggregations (e.g. source counts — use SourceRepository)
- Committing or rolling back transactions (owned by UnitOfWork)
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.models.postgres.project_member_model import ProjectMember
from app.models.postgres.project_model import Project
from app.repositories.postgres.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ProjectRepository(BaseRepository[Project]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Project)

    # ── Lookups ────────────────────────────────────────────────────────────

    def get_by_uuid(self, project_id: UUID) -> Project | None:
        """Return an active (non-deleted) Project by its UUID, or None."""
        return (
            self._session.query(Project)
            .filter(Project.id == project_id, Project.deleted_at.is_(None))
            .first()
        )

    def list_ids_by_tenant(self, tenant_id: UUID) -> list[UUID]:
        """Return the IDs of every active project belonging to *tenant_id*.

        Used to scope dashboard-stats aggregation to a Client Admin's own
        tenant — Neo4j has no tenant concept on its Module/Feature/UserStory
        nodes, so a concrete project-id list (not just a ``tenant_id``
        filter) is required to scope the graph-side counts too.
        """
        stmt = select(Project.id).where(
            Project.deleted_at.is_(None), Project.tenant_id == tenant_id
        )
        return list(self._session.execute(stmt).scalars().all())

    def list_ids_owned_or_member(self, user_id: UUID) -> list[UUID]:
        """Return the IDs of every active project *user_id* owns or holds a
        ``ProjectMember`` row for (Member assigned by a Client Admin) — the
        same "owned OR assigned" scope as :meth:`get_paginated`'s
        ``owner_id``/``member_user_id`` filter, but as a plain ID list for
        aggregation queries (e.g. dashboard stats) rather than a paginated
        entity list.
        """
        stmt = select(Project.id).where(
            Project.deleted_at.is_(None),
            or_(
                Project.owner_id == user_id,
                select(ProjectMember.project_id)
                .where(
                    and_(
                        ProjectMember.project_id == Project.id,
                        ProjectMember.user_id == user_id,
                    )
                )
                .exists(),
            ),
        )
        return list(self._session.execute(stmt).scalars().all())

    def get_by_name_and_owner(self, name: str, owner_id: UUID) -> Project | None:
        """Return the first active Project whose name matches exactly for a specific owner."""
        return (
            self._session.query(Project)
            .filter(
                Project.name == name,
                Project.owner_id == owner_id,
                Project.deleted_at.is_(None),
            )
            .first()
        )

    def _apply_scope_filters(
        self,
        stmt: Select,
        owner_id: UUID | None,
        tenant_id: UUID | None,
        search: str | None,
        member_user_id: UUID | None,
    ) -> Select:
        """Apply the owner/member/tenant/search filters shared by :meth:`get_paginated`
        and :meth:`list_for_scope` — kept in one place so the two query shapes
        (paginated vs. unpaginated) can never drift apart on scoping rules.

        See :meth:`get_paginated`'s docstring for the exact semantics of each
        parameter.
        """
        if owner_id is not None:
            if member_user_id is not None:
                stmt = stmt.where(
                    or_(
                        Project.owner_id == owner_id,
                        select(ProjectMember.project_id)
                        .where(
                            and_(
                                ProjectMember.project_id == Project.id,
                                ProjectMember.user_id == member_user_id,
                            )
                        )
                        .exists(),
                    )
                )
            else:
                stmt = stmt.where(Project.owner_id == owner_id)
        if tenant_id is not None:
            stmt = stmt.where(Project.tenant_id == tenant_id)
        if search:
            stmt = stmt.where(Project.name.ilike(f"%{search}%"))
        return stmt

    def list_for_scope(
        self,
        owner_id: UUID | None = None,
        tenant_id: UUID | None = None,
        search: str | None = None,
        member_user_id: UUID | None = None,
    ) -> list[Project]:
        """Return every active project matching the given scope, unpaginated.

        Same owner_id/member_user_id/tenant_id/search semantics as
        :meth:`get_paginated` (see its docstring) but without a LIMIT/OFFSET
        — for list endpoints that intentionally return every matching row
        in one response (e.g. ``GET /projects/list``).
        """
        stmt = select(Project).where(Project.deleted_at.is_(None))
        stmt = self._apply_scope_filters(stmt, owner_id, tenant_id, search, member_user_id)
        stmt = stmt.order_by(Project.created_at.desc())
        return list(self._session.execute(stmt).scalars().all())

    def get_paginated(
        self,
        skip: int = 0,
        limit: int = 20,
        owner_id: UUID | None = None,
        tenant_id: UUID | None = None,
        search: str | None = None,
        member_user_id: UUID | None = None,
    ) -> tuple[list[Project], int]:
        """Return a page of projects plus the total matching count.

        Uses a single SQL query with a ``count(*) OVER ()`` window function
        instead of the previous two-query COUNT + SELECT pattern, eliminating
        the extra round-trip for every list request.

        Args:
            skip:      Number of rows to skip (offset).
            limit:     Maximum rows to return.
            owner_id:  When provided, restricts results to projects owned by
                       this user.  When ``None``, returns projects for all users
                       (admin / ``list_all`` use-case).
            tenant_id: When provided, restricts results to this tenant. When
                       ``None``, no tenant filter is applied (``super_admin``
                       cross-tenant listing, or un-tenanted deployments).
            search:    Case-insensitive substring match on ``Project.name``.
            member_user_id: When provided alongside ``owner_id``, also
                       includes projects where this user holds a
                       ``ProjectMember`` row (Member assigned by a Client
                       Admin) — OR'd with the ``owner_id`` filter so a
                       user's project list shows both owned and assigned
                       projects. Ignored if ``owner_id`` is ``None``.
        """
        total_col = func.count().over().label("total")
        stmt = select(Project, total_col).where(Project.deleted_at.is_(None))
        stmt = self._apply_scope_filters(stmt, owner_id, tenant_id, search, member_user_id)
        stmt = stmt.order_by(Project.created_at.desc()).offset(skip).limit(limit)

        rows = self._session.execute(stmt).all()
        if not rows:
            return [], 0

        items = [row[0] for row in rows]
        total: int = rows[0][1]
        return items, total

    def get_projects_by_user_ids(
        self, user_ids: list[UUID]
    ) -> dict[UUID, list[tuple[Project, bool, list[str]]]]:
        """Return every active project each of *user_ids* owns and/or is assigned to.

        Two queries regardless of how many user ids are passed (no N+1):
        one for owned projects, one for ``ProjectMember`` assignments. Each
        project is tagged with ``(is_owner, roles)`` reported independently
        — ownership (``Project.owner_id``) is not a role, and an owner who
        is *also* explicitly assigned as member keeps both facts (previously
        the membership role was silently dropped for owners).
        """
        # project_id -> [Project, is_owner, roles] per user, built incrementally.
        by_project: dict[UUID, dict[UUID, list]] = {uid: {} for uid in user_ids}
        if not user_ids:
            return {uid: [] for uid in user_ids}

        owned = (
            self._session.query(Project)
            .filter(Project.owner_id.in_(user_ids), Project.deleted_at.is_(None))
            .all()
        )
        for project in owned:
            by_project[project.owner_id][project.id] = [project, True, []]

        member_rows = (
            self._session.query(ProjectMember.user_id, ProjectMember.role, Project)
            .join(Project, Project.id == ProjectMember.project_id)
            .filter(ProjectMember.user_id.in_(user_ids), Project.deleted_at.is_(None))
            .all()
        )
        for user_id, role, project in member_rows:
            entry = by_project[user_id].get(project.id)
            if entry is None:
                entry = [project, False, []]
                by_project[user_id][project.id] = entry
            entry[2].append(role)

        return {
            user_id: [(project, is_owner, roles) for project, is_owner, roles in projects.values()]
            for user_id, projects in by_project.items()
        }

    def next_untenanted_code_sequence(self) -> int:
        """Return the next value of the global untenanted-project code sequence.

        Backs the ``PRJ-####`` code fallback for projects with no
        ``tenant_id`` (MVP single-tenant deployments) — mirrors the existing
        ``project_run_code_seq`` pattern used for ``source_ingestions.run_code``.
        """
        return self._session.execute(
            text("SELECT nextval('project_untenanted_code_seq')")
        ).scalar_one()

    # ── Write ──────────────────────────────────────────────────────────────

    def create(self, project: Project) -> Project:
        """Persist a new Project and return it with all DB-generated fields populated.

        Stages the entity, flushes to obtain DB-assigned values (``id``,
        ``created_at``, ``updated_at``) within the current transaction, then
        refreshes the in-memory object so those values are immediately readable.

        Does **not** commit — the caller (UnitOfWork / service) owns the
        transaction boundary.
        """
        self._session.add(project)
        self._session.flush()
        self._session.refresh(project)
        return project

    def update(self, project: Project) -> Project:
        """Persist mutations to an existing Project and return the refreshed instance.

        The entity must already be tracked by the current SQLAlchemy session
        (i.e. loaded via ``get_by_uuid`` in the same request).  Calling
        ``session.add()`` on a tracked object is a no-op; the flush propagates
        dirty fields, and refresh re-loads DB-updated values (e.g. ``updated_at``).

        Does **not** commit — the caller owns the transaction boundary.
        """
        self._session.flush()
        self._session.refresh(project)
        return project

    def update_fields(self, project_id: UUID, **fields: object) -> Project | None:
        """Patch arbitrary column values on a non-deleted project row.

        The caller is responsible for committing the session.
        """
        project = self.get_by_uuid(project_id)
        if project is None:
            return None
        for key, value in fields.items():
            setattr(project, key, value)
        return project
