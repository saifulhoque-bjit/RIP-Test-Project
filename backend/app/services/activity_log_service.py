"""Service layer for the project activity-log feed.

Responsibilities
────────────────
1. ``ActivityLogService.list_activities`` — paginated feed for a project,
   called from the request path with the caller's ``UnitOfWork``.
2. ``record_activity``          — module-level helper that persists one
   activity-log row from anywhere in the codebase (Celery task completion
   points, request-path services) without requiring the caller to hold an
   open ``UnitOfWork``.
3. ``resolve_actor_from_task``  — module-level helper that looks up the user
   who triggered a completed Celery task via its linked ``ProjectTask`` row,
   for call sites where the actor isn't already available as a plain
   ``user_id``/``current_user.id``.

Sync by design
───────────────
Every method here does plain synchronous Postgres work via ``UnitOfWork`` —
there is no genuine async I/O to benefit from ``async def``.

Never raises
────────────
``record_activity`` fires *after* the real business action (pipeline
completion, accept/reject) has already succeeded or committed — a logging
failure must never fail an already-successful task or already-committed
request, so it catches and logs instead of propagating.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core.constants import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.core.enums.activity_type import ACTIVITY_TYPE_MESSAGE_PREFIX, ActivityType
from app.core.exceptions import NotFoundError
from app.core.messages import MSG_PROJECT_NOT_FOUND
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.project_model import Project
from app.models.postgres.user_model import User
from app.schemas.activity_log_schema import (
    ActivityLogActorOut,
    ActivityLogListResponse,
    ActivityLogResponse,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ActivityLogService:
    """Reusable service for querying a project's activity-log feed."""

    def list_activities(
        self,
        project_id: UUID,
        *,
        skip: int = 0,
        limit: int = 20,
        activity_type: ActivityType | None = None,
        uow: UnitOfWork,
    ) -> ActivityLogListResponse:
        """Return a paginated activity feed for *project_id*.

        Raises:
            NotFoundError: If the project does not exist.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        items, total = uow.activity_logs.list_by_project(
            project_id,
            skip=skip,
            limit=limit,
            activity_type=activity_type.value if activity_type else None,
        )

        actor_ids = {a.actor_user_id for a in items if a.actor_user_id is not None}
        users_by_id = {u.id: u for u in uow.users.get_by_ids(list(actor_ids))}
        member_roles_by_user_id: dict[UUID, list[str]] = {}
        for member in uow.project_members.list_by_project(project_id):
            member_roles_by_user_id.setdefault(member.user_id, []).append(member.role)

        return ActivityLogListResponse(
            items=[
                ActivityLogResponse(
                    id=a.id,
                    project_id=a.project_id,
                    actor=self._build_actor(
                        actor_user_id=a.actor_user_id,
                        project=project,
                        user=users_by_id.get(a.actor_user_id),
                        member_roles_by_user_id=member_roles_by_user_id,
                    ),
                    activity_type=a.activity_type,
                    summary=a.summary,
                    message=a.message,
                    data=a.data,
                    created_at=a.created_at,
                )
                for a in items
            ],
            total=total,
            skip=skip,
            limit=limit,
        )

    @staticmethod
    def _build_actor(
        *,
        actor_user_id: UUID | None,
        project: Project,
        user: User | None,
        member_roles_by_user_id: dict[UUID, list[str]],
    ) -> ActivityLogActorOut | None:
        """Build the nested actor (id/name/role) for an activity-log entry.

        Role resolution mirrors ``UserService.list_users_with_projects``: the
        project owner's tenant-wide admin/super_admin role explains their
        access (project creation is admin/super_admin-only), otherwise the
        actor's ``ProjectMember`` role for this project applies.
        """
        if actor_user_id is None or user is None:
            return None
        role: str | None = None
        if project.owner_id == actor_user_id:
            tenant_roles = set(user.role_names)
            if ROLE_SUPER_ADMIN in tenant_roles:
                role = ROLE_SUPER_ADMIN
            elif ROLE_ADMIN in tenant_roles:
                role = ROLE_ADMIN
        if role is None:
            member_roles = member_roles_by_user_id.get(actor_user_id)
            role = member_roles[0] if member_roles else None
        return ActivityLogActorOut(id=actor_user_id, name=user.name or user.email, role=role)


# ── Module-level convenience helpers ─────────────────────────────────────────


def record_activity(
    *,
    project_id: UUID,
    activity_type: ActivityType,
    summary: str,
    message: str,
    actor_user_id: UUID | None,
    data: dict | None = None,
) -> None:
    """Persist an activity-log row from anywhere in the codebase.

    Opens its own ``UnitOfWork`` — intended for Celery task completion points
    and request-path services alike. Callers resolve ``actor_user_id``
    themselves (e.g. via ``resolve_actor_from_task`` in a worker, or
    ``current_user.id`` in a request path) before calling this.

    Also bumps ``Project.last_activity_at`` to now, in the same transaction
    as the ActivityLog insert — every recorded activity is, by definition,
    the project's most recent activity.

    ``message`` is prefixed with the activity's source-type/trigger label
    (e.g. ``"[RFP] ..."``, ``"[Source Code] ..."``, ``"[Feedback] ..."``,
    ``"[Incremental Update] ..."``) per ``ACTIVITY_TYPE_MESSAGE_PREFIX``, when
    one is defined for this ``activity_type``.

    Never raises — a failure here must not fail an already-successful task
    or already-committed request; it is caught and logged instead.
    """
    prefix = ACTIVITY_TYPE_MESSAGE_PREFIX.get(activity_type.value)
    stored_message = f"[{prefix}] {message}" if prefix else message
    try:
        with UnitOfWork() as uow:
            uow.activity_logs.create(
                project_id=project_id,
                actor_user_id=actor_user_id,
                activity_type=activity_type.value,
                summary=summary,
                message=stored_message,
                data=data,
            )
            uow.projects.update_fields(project_id, last_activity_at=datetime.now(UTC))
            uow.commit()
    except Exception as exc:
        logger.warning(
            "record_activity failed project_id=%s activity_type=%s error=%s",
            project_id,
            activity_type.value,
            exc,
        )


def resolve_actor_from_task(task_db_id: str | None) -> UUID | None:
    """Look up the user who triggered a completed Celery task, or ``None``.

    ``task_db_id`` is the ``ProjectTask.id`` created before the task was
    dispatched — every RFP/source-code pipeline task threads this through
    without carrying the actual ``user_id`` as a task argument.
    """
    if not task_db_id:
        return None
    try:
        with UnitOfWork() as uow:
            task = uow.project_tasks.get_by_id(UUID(task_db_id))
            return task.user_id if task else None
    except Exception as exc:
        logger.warning("resolve_actor_from_task failed task_db_id=%s error=%s", task_db_id, exc)
        return None
