"""Business logic for the Project domain.

Responsibilities:
- Validate business invariants (name conflicts, ownership, authorization)
- Orchestrate UnitOfWork transactions across PostgreSQL
- Coordinate best-effort Neo4j graph projections
- Aggregate counts from both stores before building responses
- Assemble response DTOs (service concern, not repository concern)

NOT responsible for:
- HTTP / transport concerns (no FastAPI imports)
- Raw SQL or Cypher (delegated to repositories)
- Infrastructure wiring (driver/session creation lives in deps.py)

Mostly sync by design
─────────────────────
Almost every method here is plain sync ``def``: Postgres access is sync
``UnitOfWork``, ``Neo4jProjectRepository.get_user_story_counts`` is a plain
sync ``def``, and ``CeleryProjectEventPublisher`` just calls the sync
``apply_async`` — see ``.github/instructions/services.instructions.md``. The
one exception is ``get_project``, which ``await``s
``ModuleFeatureRepository``/``UserStoryRepository`` (both wrap their Neo4j
calls in ``asyncio.to_thread`` and are genuinely async I/O) to compute the
``all_modules_approved``/``all_features_approved``/``all_user_stories_approved``
response flags — it and its calling route
(``app/routes/v1/projects.py::get_project``) are ``async def`` accordingly;
every other method/route pair in this pairing stays plain ``def`` so FastAPI
runs them in its threadpool.

Dependency injection contract
─────────────────────────────
``ProjectService`` is instantiated by ``deps.get_project_service`` with the
Neo4j project repository injected.  This keeps the service free of
``get_neo4j_driver()`` calls and makes the class trivially unit-testable
with mock repositories.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.constants import ROLE_ADMIN, ROLE_MEMBER, ROLE_SUPER_ADMIN
from app.core.enums.activity_type import ActivityType
from app.core.enums.llm_provider import LLMProvider
from app.core.enums.notification_type import NotificationType
from app.core.enums.project_progress_filter import ProjectProgressFilter
from app.core.enums.project_status import ProjectStatus
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.messages import (
    MSG_ACTIVITY_PROJECT_CREATED,
    MSG_ACTIVITY_PROJECT_UPDATED,
    MSG_PROJECT_CONTENT_ACCESS_FORBIDDEN,
    MSG_PROJECT_DELETE_FORBIDDEN,
    MSG_PROJECT_LLM_API_KEY_NOT_CONFIGURED,
    MSG_PROJECT_LLM_MODEL_NOT_SUPPORTED,
    MSG_PROJECT_LLM_MODEL_REQUIRES_PROVIDER,
    MSG_PROJECT_LLM_PROVIDER_NOT_ENABLED,
    MSG_PROJECT_MEMBERS_MANAGE_FORBIDDEN,
    MSG_PROJECT_NAME_CONFLICT,
    MSG_PROJECT_NOT_FOUND,
    MSG_PROJECT_TENANT_ASSIGN_FORBIDDEN,
    MSG_PROJECT_UPDATE_BLOCKED_PIPELINE_RUNNING,
    MSG_PROJECT_UPDATE_FORBIDDEN,
    MSG_TENANT_NOT_FOUND,
    SUMMARY_ACTIVITY_PROJECT_CREATED,
    SUMMARY_ACTIVITY_PROJECT_UPDATED,
)
from app.core.protocols import IProjectEventPublisher, IProjectGraphRepository
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.project_model import Project
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
from app.repositories.neo4j.project_repository import ProjectProgressFlags, UserStoryCounts
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.schemas.project_schema import (
    DashboardStatsResponse,
    LongestCompletionProject,
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectSummaryResponse,
    ProjectUpdate,
)
from app.services.activity_log_service import record_activity
from app.services.notification_service import publish_notification
from app.services.setting_service import SettingService
from app.services.tenant_llm_provider_service import TenantLLMProviderService
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ProjectService:
    """Orchestrates project domain operations.

    Injection contract
    ──────────────────
    Two dependency patterns are used intentionally:

    1. **Constructor injection** — ``neo4j_project_repo`` is stable for the
       lifetime of a request and has no per-call state.  It is injected once
       via FastAPI ``Depends()`` in ``deps.get_project_service`` and stored as
       ``self._neo4j``.  ``event_publisher``, ``module_feature_repo``, and
       ``user_story_repo`` follow the same pattern, stored as
       ``self._publisher``, ``self._module_feature_repo``, and
       ``self._user_story_repo`` respectively.

    2. **Method injection** — ``uow: UnitOfWork`` carries the SQLAlchemy
       session which owns the transaction boundary.  It is injected per call
       site rather than per class so that the UoW lifetime (begin → commit/
       rollback → close) maps exactly to one service operation, preventing
       accidental cross-operation session sharing.

    To swap the graph store in tests: pass a mock ``Neo4jProjectRepository``
    to ``ProjectService(neo4j_project_repo=mock_repo)``.
    To swap the DB in tests: pass a mock ``UnitOfWork`` to each method call.
    """

    def __init__(
        self,
        neo4j_project_repo: IProjectGraphRepository,
        event_publisher: IProjectEventPublisher,
        module_feature_repo: ModuleFeatureRepository,
        user_story_repo: UserStoryRepository,
        setting_service: SettingService | None = None,
    ) -> None:
        self._neo4j = neo4j_project_repo
        self._settings = setting_service or SettingService()
        self._publisher = event_publisher
        self._module_feature_repo = module_feature_repo
        self._user_story_repo = user_story_repo

    # ── Internal helpers ───────────────────────────────────────────────────

    @staticmethod
    def _assert_owner_or_admin(
        project: Project,
        requester_id: UUID,
        requester_roles: list[str],
        requester_tenant_id: UUID | None,
        error_message: str,
    ) -> None:
        """Raise ForbiddenError unless the requester owns the project, or is
        the tenant-scoped ``admin`` (Client Admin) of the project's own tenant.

        Centralising the guard here means adding a ``co-owner`` role (or any
        future privilege escalation) only requires a change in one place.
        ``@staticmethod`` so non-``ProjectService`` callers (e.g. the project
        WebSocket handshake in ``app/websockets/source_ws.py``) can reuse this
        exact rule without needing to construct a full ``ProjectService``
        (which requires Neo4j/event-publisher dependencies unrelated to this
        check) — see ``.github/instructions/eventing.instructions.md``'s rule
        against re-implementing a REST route's ownership check in a WS handler.

        ``admin`` is scoped to the caller's own tenant (``project.tenant_id ==
        requester_tenant_id``, so ``None == None`` still matches for
        un-tenanted deployments). ``super_admin`` has **no** bypass here —
        deliberately: super_admin's platform-level administration is limited
        to tenant/user/role/invitation/observability management and a
        cross-tenant project list/summary view (see the ``super_admin`` role
        definition in ``app/db/seed_db.py``); creating, updating, deleting,
        or viewing an individual project is a Client Admin/Member
        responsibility within their own tenant, not a super_admin one.
        """
        if project.owner_id == requester_id:
            return
        if ROLE_ADMIN in requester_roles and project.tenant_id == requester_tenant_id:
            return
        raise ForbiddenError(error_message)

    @staticmethod
    def assert_project_access(
        project: Project,
        requester_id: UUID,
        requester_roles: list[str],
        requester_tenant_id: UUID | None,
        level: str,
        uow: UnitOfWork,
    ) -> None:
        """Raise ForbiddenError unless the requester may access this
        project's *content* (modules/features/user stories/sources/
        incremental updates) at the given ``level``.

        Distinct from ``_assert_owner_or_admin``, which governs the project
        *entity itself* (rename/delete/update via ``/projects/{id}``) and has
        no ``ProjectMember`` bypass — assigning members is a Client-Admin-only
        responsibility (see ``assert_tenant_admin_or_super_admin``), even for
        the project's own owner.

        ``level`` is one of ``"read"``, ``"write"``, ``"approve"``:
        - owner / tenant-scoped ``admin`` → always allowed, regardless of
          ``level``. ``super_admin`` has **no** bypass here — see the note
          on ``_assert_owner_or_admin`` above; the same platform-vs-tenant
          boundary applies to a project's content, not just the project
          entity itself.
        - otherwise a ``ProjectMember`` row for this (project, requester) is
          required: it grants ``"read"``, ``"write"``, and ``"approve"``
          alike (the latter used only for the User Story approve/reject
          endpoints — see app/routes/v1/user_stories.py).
        """
        if project.owner_id == requester_id:
            return
        if ROLE_ADMIN in requester_roles and project.tenant_id == requester_tenant_id:
            return

        member_roles = {
            m.role for m in uow.project_members.list_roles_for_user(project.id, requester_id)
        }
        if member_roles:
            if level == "read":
                return
            if level in ("write", "approve") and ROLE_MEMBER in member_roles:
                return
        raise ForbiddenError(MSG_PROJECT_CONTENT_ACCESS_FORBIDDEN.format(level=level))

    @staticmethod
    def _resolve_requested_tenant_id(
        requested_tenant_id: UUID | None,
        requester_roles: list[str],
        uow: UnitOfWork,
    ) -> UUID | None:
        """Validate an explicit ``tenant_id`` sent in a create/update payload.

        Only a ``super_admin`` may direct a project at an arbitrary tenant;
        every other role must omit the field entirely (their own tenant is
        derived automatically by the caller instead — see
        ``create_project``/``update_project``).
        """
        if requested_tenant_id is None:
            return None
        if ROLE_SUPER_ADMIN not in requester_roles:
            raise ForbiddenError(MSG_PROJECT_TENANT_ASSIGN_FORBIDDEN)
        if uow.tenants.get(requested_tenant_id) is None:
            raise NotFoundError(MSG_TENANT_NOT_FOUND.format(tenant_id=requested_tenant_id))
        return requested_tenant_id

    @staticmethod
    def _generate_project_code(tenant_id: UUID | None, uow: UnitOfWork) -> str:
        """Return a new, human-readable project code.

        A tenanted project gets ``{tenant.code}-{seq:04d}`` from that
        tenant's own atomically-incremented sequence
        (``TenantRepository.increment_project_sequence``); an untenanted
        project (MVP single-tenant deployments — see
        ``_resolve_requested_tenant_id``) falls back to a global
        ``PRJ-####`` sequence.
        """
        if tenant_id is None:
            sequence = uow.projects.next_untenanted_code_sequence()
            return f"PRJ-{sequence:04d}"

        result = uow.tenants.increment_project_sequence(tenant_id)
        if result is None:
            raise NotFoundError(MSG_TENANT_NOT_FOUND.format(tenant_id=tenant_id))
        tenant_code, sequence = result
        return f"{tenant_code}-{sequence:04d}"

    @staticmethod
    def _authorize_tenant_reassignment(
        requested_tenant_id: UUID | None,
        requester_roles: list[str],
        uow: UnitOfWork,
    ) -> UUID | None:
        """Validate an update payload that explicitly included ``tenant_id``.

        Unlike ``_resolve_requested_tenant_id``, ``None`` here is a
        meaningful value (an explicit ``{"tenant_id": null}`` clear), so the
        permission check applies regardless of whether a value or ``null``
        was sent — only whether the field was present at all (checked by
        the caller via ``model_fields_set`` before calling this).
        """
        if ROLE_SUPER_ADMIN not in requester_roles:
            raise ForbiddenError(MSG_PROJECT_TENANT_ASSIGN_FORBIDDEN)
        if requested_tenant_id is not None and uow.tenants.get(requested_tenant_id) is None:
            raise NotFoundError(MSG_TENANT_NOT_FOUND.format(tenant_id=requested_tenant_id))
        return requested_tenant_id

    @staticmethod
    def assert_tenant_admin_or_super_admin(
        project: Project,
        requester_roles: list[str],
        requester_tenant_id: UUID | None,
    ) -> None:
        """Raise ForbiddenError unless the requester is a ``super_admin`` or
        the Client Admin of the project's own tenant.

        Deliberately has **no owner bypass** — used only for managing
        project membership itself (``app/routes/v1/project_members.py``),
        since assigning Members to a project is a Client-Admin
        responsibility even when the project's owner is someone else (or is
        merely a Member themselves).
        """
        if ROLE_SUPER_ADMIN in requester_roles:
            return
        if ROLE_ADMIN in requester_roles and project.tenant_id == requester_tenant_id:
            return
        raise ForbiddenError(MSG_PROJECT_MEMBERS_MANAGE_FORBIDDEN)

    @staticmethod
    def _validate_llm_provider_for_tenant(
        tenant_id: UUID | None,
        llm_provider: LLMProvider | None,
        uow: UnitOfWork,
    ) -> None:
        """Reject an ``llm_provider`` choice not enabled for the project's tenant.

        No-ops when the project has no tenant (MVP single-tenant deployments)
        or the tenant hasn't configured an enabled-provider set yet — an
        empty set means "unrestricted", not "nothing allowed", so tenants
        that haven't opted into this feature don't have existing project
        creation/updates suddenly break.
        """
        if llm_provider is None or tenant_id is None:
            return
        tenant = uow.tenants.get(tenant_id)
        if tenant is None:
            return
        enabled = {p.provider for p in tenant.llm_providers if p.is_active}
        if not enabled:
            return
        if llm_provider.value not in enabled:
            raise ValidationError(
                MSG_PROJECT_LLM_PROVIDER_NOT_ENABLED.format(
                    provider=llm_provider.value, tenant_id=tenant_id
                )
            )

    @staticmethod
    def assert_llm_api_key_configured(project: Project, uow: UnitOfWork) -> None:
        """Reject an action that would trigger AI generation when the
        project's tenant has no active, *verified* API key for its chosen
        ``llm_provider``.

        Stricter than the AI pipeline's own run-time lookup
        (``TenantLLMProviderService.get_active_api_key``): this also
        requires the key to have passed connection testing
        (``is_verified``), so an unverified or failed-test key is rejected
        up front instead of letting an upload queue a task that is guaranteed
        to fail deep in the pipeline. No-ops when the project has no tenant
        or hasn't chosen an ``llm_provider`` — same MVP/unrestricted
        convention as ``_validate_llm_provider_for_tenant``.
        """
        if project.tenant_id is None or project.llm_provider is None:
            return
        api_key = TenantLLMProviderService(uow).get_active_api_key(
            project.tenant_id, project.llm_provider, require_verified=True
        )
        if not api_key:
            raise ValidationError(
                MSG_PROJECT_LLM_API_KEY_NOT_CONFIGURED.format(provider=project.llm_provider)
            )

    @staticmethod
    def _assert_project_not_locked_by_running_pipeline(project_id: UUID, uow: UnitOfWork) -> None:
        """Reject any PATCH to this project while its generation pipeline
        is currently running.

        Uses the same ``running``-status lookup as
        ``SourceIngestionService.raise_if_pipeline_running`` (not imported
        directly — that module imports ``ProjectService``, so importing it
        back here would create a circular import).
        """
        running = uow.source_ingestions.list_running_by_project(project_id)
        if running:
            raise ConflictError(
                MSG_PROJECT_UPDATE_BLOCKED_PIPELINE_RUNNING.format(ingestion_id=running[0].id)
            )

    @staticmethod
    def _notify_if_llm_api_key_missing(project: Project, uow: UnitOfWork) -> None:
        """Best-effort: warn the project owner if their tenant has no active,
        usable API key for the project's chosen ``llm_provider``.

        Unlike ``assert_llm_api_key_configured``, this does not require the
        key to be verified (``require_verified`` defaults to False) — an
        admin may legitimately create a project before wiring up and testing
        an LLM key, so this only surfaces the gap via notification instead of
        rejecting the request. Never raises: a notification failure must not
        affect the create-project response.
        """
        if project.tenant_id is None or project.llm_provider is None or project.owner_id is None:
            return
        try:
            api_key = TenantLLMProviderService(uow).get_active_api_key(
                project.tenant_id, project.llm_provider
            )
            if api_key:
                return
            publish_notification(
                user_id=project.owner_id,
                title="LLM API Key Not Configured",
                message=(
                    f'No active API key is configured for "{project.llm_provider}" on '
                    f'your tenant. Uploads to "{project.name}" will fail until one is added.'
                ),
                notification_type=NotificationType.WARNING,
                data={"project_id": str(project.id), "llm_provider": project.llm_provider},
            )
        except Exception:
            logger.warning(
                "_notify_if_llm_api_key_missing: failed for project_id=%s",
                project.id,
                exc_info=True,
            )

    @staticmethod
    def _notify_project_created(project: Project, *, actor_user_id: UUID) -> None:
        """Best-effort: notify the owner that their new project was created.

        Mirrors ``_notify_if_llm_api_key_missing`` — never raises, since a
        notification failure must not affect the create-project response.
        """
        if project.owner_id is None:
            return
        try:
            publish_notification(
                user_id=project.owner_id,
                title=SUMMARY_ACTIVITY_PROJECT_CREATED,
                message=MSG_ACTIVITY_PROJECT_CREATED.format(
                    **ProjectService._project_activity_message_fields(project)
                ),
                notification_type=NotificationType.SUCCESS,
                data={"project_id": str(project.id)},
            )
        except Exception:
            logger.warning(
                "_notify_project_created: failed for project_id=%s actor_user_id=%s",
                project.id,
                actor_user_id,
                exc_info=True,
            )

    _ACTIVITY_FIELD_NOT_SET = "Not set"

    @staticmethod
    def _project_activity_message_fields(project: Project) -> dict[str, str]:
        """Format fields for the create/update activity-log message.

        llm_provider/llm_model/project_type are all optional on a project —
        the human-readable message falls back to a display placeholder
        rather than interpolating a literal "None".
        """
        not_set = ProjectService._ACTIVITY_FIELD_NOT_SET
        return {
            "project_name": project.name,
            "project_type": project.project_type or not_set,
            "llm_provider": project.llm_provider or not_set,
            "llm_model": project.llm_model or not_set,
        }

    @staticmethod
    def _project_activity_data(project: Project) -> dict[str, str | None]:
        """Structured activity-log ``data`` payload — raw values, None kept as-is."""
        return {
            "project_name": project.name,
            "llm_provider": project.llm_provider,
            "llm_model": project.llm_model,
            "project_type": project.project_type,
        }

    @staticmethod
    def _record_project_created_activity(project: Project, *, actor_user_id: UUID) -> None:
        """Best-effort: log a project-creation entry to its activity feed.

        Called after ``uow.commit()`` has already persisted the project row —
        ``record_activity`` opens its own ``UnitOfWork`` and ``activity_logs.project_id``
        is a NOT NULL foreign key, so the project must already exist. Never
        raises: a logging failure must not affect the create-project response.
        """
        record_activity(
            project_id=project.id,
            activity_type=ActivityType.PROJECT_CREATED,
            summary=SUMMARY_ACTIVITY_PROJECT_CREATED,
            message=MSG_ACTIVITY_PROJECT_CREATED.format(
                **ProjectService._project_activity_message_fields(project)
            ),
            actor_user_id=actor_user_id,
            data=ProjectService._project_activity_data(project),
        )

    @staticmethod
    def _record_project_updated_activity(project: Project, *, actor_user_id: UUID) -> None:
        """Best-effort: log a project-update entry to its activity feed.

        Called after ``uow.commit()`` has already persisted the change —
        same reasoning as ``_record_project_created_activity``. Never raises.
        """
        record_activity(
            project_id=project.id,
            activity_type=ActivityType.PROJECT_UPDATED,
            summary=SUMMARY_ACTIVITY_PROJECT_UPDATED,
            message=MSG_ACTIVITY_PROJECT_UPDATED.format(
                **ProjectService._project_activity_message_fields(project)
            ),
            actor_user_id=actor_user_id,
            data=ProjectService._project_activity_data(project),
        )

    def _validate_llm_model_for_provider(
        self,
        llm_provider: LLMProvider | str | None,
        llm_model: str | None,
    ) -> None:
        """Reject an ``llm_model`` that isn't listed under ``llm_provider``.

        The catalog of valid provider→model ids is the shared Neo4j
        ``Setting.llm_providers`` document (see ``SettingService``) rather
        than a hardcoded map, so new models can be added without a code
        change. No-ops when ``llm_model`` is ``None`` — choosing a provider
        without a specific model is allowed.
        """
        if llm_model is None:
            return
        if llm_provider is None:
            raise ValidationError(MSG_PROJECT_LLM_MODEL_REQUIRES_PROVIDER.format(model=llm_model))

        provider_value = (
            llm_provider.value if isinstance(llm_provider, LLMProvider) else llm_provider
        )
        catalog = self._settings.get_or_seed_default_settings().project.llm_providers
        provider_entry = next((p for p in catalog if p.id == provider_value), None)
        valid_model_ids = {m.id for m in provider_entry.models} if provider_entry else set()
        if llm_model not in valid_model_ids:
            raise ValidationError(
                MSG_PROJECT_LLM_MODEL_NOT_SUPPORTED.format(model=llm_model, provider=provider_value)
            )

    def _validate_llm_update(
        self,
        project: Project,
        payload: ProjectUpdate,
        updated_fields: set[str],
        uow: UnitOfWork,
    ) -> None:
        """Run both LLM checks for a PATCH, resolved against the fields the
        caller actually sent (true PATCH semantics — an omitted field falls
        back to the project's current value).

        Split out of ``update_project`` purely to keep that method's
        branching count readable; it owns no state of its own.
        """
        if "llm_provider" in updated_fields and payload.llm_provider is not None:
            self._validate_llm_provider_for_tenant(project.tenant_id, payload.llm_provider, uow)

        if "llm_provider" not in updated_fields and "llm_model" not in updated_fields:
            return
        effective_provider = (
            payload.llm_provider if "llm_provider" in updated_fields else project.llm_provider
        )
        effective_model = payload.llm_model if "llm_model" in updated_fields else project.llm_model
        self._validate_llm_model_for_provider(effective_provider, effective_model)

    def _get_user_story_counts(self, project_ids: list[UUID]) -> dict[UUID, UserStoryCounts]:
        """Fetch total/approved user story counts from Neo4j for all project ids in one query.

        Best-effort: any Neo4j failure is caught and defaulted to 0 so a
        graph outage never aborts a PostgreSQL-backed list/get response.
        """
        if not project_ids:
            return {}
        try:
            return self._neo4j.get_user_story_counts(project_ids)
        except Exception as exc:
            logger.warning("Neo4j user story counts failed: %s", exc)
            return dict.fromkeys(project_ids, UserStoryCounts(total=0, approved=0))

    def _get_progress_flags(self, project_ids: list[UUID]) -> dict[UUID, ProjectProgressFlags]:
        """Fetch has-module-feature/has-user-story flags from Neo4j for all project ids.

        Best-effort, matching :meth:`_get_user_story_counts`: a Neo4j
        failure defaults every project to ``(False, False)`` rather than
        aborting the list response — a project would simply be excluded
        from both stage buckets during a graph outage.
        """
        if not project_ids:
            return {}
        try:
            return self._neo4j.get_progress_flags(project_ids)
        except Exception as exc:
            logger.warning("Neo4j project progress flags failed: %s", exc)
            return dict.fromkeys(
                project_ids, ProjectProgressFlags(has_module_feature=False, has_user_story=False)
            )

    def _sync_neo4j_project(self, project: Project) -> None:
        """Enqueue an async Neo4j upsert task via the event publisher.

        The task runs in the ``neo4j_sync`` Celery queue with automatic
        exponential back-off (up to ``TASK_MAX_RETRIES`` attempts).  The
        HTTP response is not blocked on Neo4j: if the broker is temporarily
        unavailable the task is not delivered, but the Postgres commit is
        already durable.
        """
        self._publisher.project_upserted(
            project_id=str(project.id),
            name=project.name,
            status=ProjectStatus(project.status).value,
        )

    def _build_response(
        self,
        project: Project,
        files_count: int,
        user_story_counts: UserStoryCounts,
        *,
        all_modules_approved: bool = False,
        all_features_approved: bool = False,
        all_user_stories_approved: bool = False,
    ) -> ProjectResponse:
        """Assemble a single :class:`ProjectResponse` from an ORM row and counts.

        Response DTOs are assembled here (service layer) rather than inside
        the PostgreSQL repository, keeping the repo a pure persistence concern.

        ``all_modules_approved``/``all_features_approved``/
        ``all_user_stories_approved`` require a Neo4j round-trip per project,
        so only ``get_project`` (single-project GET) computes real values —
        every other caller (create/update/list) leaves the default ``False``.
        """
        return ProjectResponse(
            id=project.id,
            name=project.name,
            code=project.code,
            description=project.description,
            llm_provider=project.llm_provider,
            llm_model=project.llm_model,
            status=project.status,
            project_type=project.project_type,
            owner_id=project.owner_id,
            tenant_id=project.tenant_id,
            version=project.version,
            created_at=project.created_at,
            updated_at=project.updated_at,
            last_activity_at=project.last_activity_at,
            all_modules_approved=all_modules_approved,
            all_features_approved=all_features_approved,
            all_user_stories_approved=all_user_stories_approved,
            files=files_count,
            user_stories=user_story_counts.total,
            approved_user_stories=user_story_counts.approved,
            jira_synced_count=user_story_counts.jira_synced,
            tap_synced_count=user_story_counts.tap_synced,
            pending_jira_sync=user_story_counts.pending_jira_sync,
            pending_tap_sync=user_story_counts.pending_tap_sync,
            team_members=0,  # placeholder until team-membership table exists
        )

    def _build_list_responses(
        self,
        uow: UnitOfWork,
        projects: list[Project],
        req_counts: dict[UUID, UserStoryCounts],
    ) -> list[ProjectResponse]:
        """Assemble ProjectResponse objects for a list of ORM rows."""
        if not projects:
            return []
        files_counts = uow.sources.get_source_counts_by_project([p.id for p in projects])
        return [
            self._build_response(
                project=p,
                files_count=files_counts.get(p.id, 0),
                user_story_counts=req_counts.get(p.id, UserStoryCounts(total=0, approved=0)),
            )
            for p in projects
        ]

    # ── Create ─────────────────────────────────────────────────────────────

    def create_project(
        self,
        payload: ProjectCreate,
        uow: UnitOfWork,
        owner_id: UUID,
        requester_roles: list[str],
        owner_tenant_id: UUID | None = None,
    ) -> ProjectResponse:
        """Create a new project owned by *owner_id*.

        *owner_tenant_id* (typically the caller's own ``User.tenant_id``) is
        used as ``Project.tenant_id`` by default. A ``super_admin`` may
        instead direct the project at an arbitrary tenant via
        ``payload.tenant_id``; any other role sending that field is rejected
        by ``_resolve_requested_tenant_id``.

        Raises:
            ConflictError: If the owner already has a project with the same name.
            ForbiddenError: If a non-super_admin caller sends ``payload.tenant_id``.
            NotFoundError: If ``payload.tenant_id`` does not reference a real tenant.
        """
        existing = uow.projects.get_by_name_and_owner(payload.name, owner_id)
        if existing is not None:
            raise ConflictError(MSG_PROJECT_NAME_CONFLICT.format(name=payload.name))

        tenant_id = (
            self._resolve_requested_tenant_id(payload.tenant_id, requester_roles, uow)
            or owner_tenant_id
        )
        self._validate_llm_provider_for_tenant(tenant_id, payload.llm_provider, uow)
        self._validate_llm_model_for_provider(payload.llm_provider, payload.llm_model)

        project = Project(
            name=payload.name,
            description=payload.description,
            llm_provider=payload.llm_provider.value if payload.llm_provider else None,
            llm_model=payload.llm_model,
            project_type=payload.project_type.value if payload.project_type else None,
            owner_id=owner_id,
            tenant_id=tenant_id,
            code=self._generate_project_code(tenant_id, uow),
        )
        try:
            project = uow.projects.create(project)
            uow.commit()
        except IntegrityError:
            uow.rollback()
            raise ConflictError(MSG_PROJECT_NAME_CONFLICT.format(name=payload.name))

        self._sync_neo4j_project(project)
        self._notify_if_llm_api_key_missing(project, uow)
        self._record_project_created_activity(project, actor_user_id=owner_id)
        self._notify_project_created(project, actor_user_id=owner_id)
        logger.info("Project created: id=%s owner=%s name=%s", project.id, owner_id, payload.name)

        # A new project always has zero files and zero user stories —
        # skipping the two extra DB/Neo4j round-trips avoids unnecessary latency.
        return self._build_response(
            project, files_count=0, user_story_counts=UserStoryCounts(total=0, approved=0)
        )

    # ── Read ───────────────────────────────────────────────────────────────

    async def get_project(
        self,
        project_id: UUID,
        uow: UnitOfWork,
        requester_id: UUID,
        requester_roles: list[str],
        requester_tenant_id: UUID | None = None,
    ) -> ProjectResponse:
        """Fetch a single project with aggregated counts.

        Raises:
            NotFoundError:  If the project does not exist.
            ForbiddenError: If the requester has no ``"read"`` access — owner,
                super_admin, tenant-scoped admin, or an assigned
                ProjectMember (Member).
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        self.assert_project_access(
            project, requester_id, requester_roles, requester_tenant_id, "read", uow
        )
        files_count = uow.sources.get_source_counts_by_project([project_id]).get(project_id, 0)
        req_counts = self._get_user_story_counts([project_id]).get(
            project_id, UserStoryCounts(total=0, approved=0)
        )
        (
            all_modules_approved,
            all_features_approved,
            all_user_stories_approved,
        ) = await asyncio.gather(
            self._module_feature_repo.are_all_modules_approved(project_id),
            self._module_feature_repo.are_all_features_approved(project_id),
            self._user_story_repo.are_all_user_stories_approved(project_id),
        )
        return self._build_response(
            project,
            files_count,
            req_counts,
            all_modules_approved=all_modules_approved,
            all_features_approved=all_features_approved,
            all_user_stories_approved=all_user_stories_approved,
        )

    def list_projects(
        self,
        owner_id: UUID,
        skip: int,
        limit: int,
        uow: UnitOfWork,
        search: str | None = None,
    ) -> ProjectListResponse:
        """Return a paginated list of projects owned by, or assigned (as a
        Member) to, *owner_id*.
        """
        items, total = uow.projects.get_paginated(
            owner_id=owner_id,
            skip=skip,
            limit=limit,
            search=search,
            member_user_id=owner_id,
        )
        req_counts = self._get_user_story_counts([p.id for p in items])
        return ProjectListResponse(
            items=self._build_list_responses(uow, items, req_counts),
            total=total,
            skip=skip,
            limit=limit,
        )

    def list_all_projects(
        self,
        skip: int,
        limit: int,
        uow: UnitOfWork,
        requester_roles: list[str],
        requester_tenant_id: UUID | None = None,
        search: str | None = None,
        tenant_id: UUID | None = None,
    ) -> ProjectListResponse:
        """Return a paginated list of projects (admin only).

        A plain ``admin`` only ever sees projects within their own tenant —
        any ``tenant_id`` they pass is ignored. A ``super_admin`` sees every
        project on the platform by default, or may narrow to one tenant by
        passing ``tenant_id``.
        """
        if ROLE_SUPER_ADMIN in requester_roles:
            tenant_filter = tenant_id
        else:
            tenant_filter = requester_tenant_id
        items, total = uow.projects.get_paginated(
            skip=skip, limit=limit, search=search, tenant_id=tenant_filter
        )
        req_counts = self._get_user_story_counts([p.id for p in items])
        return ProjectListResponse(
            items=self._build_list_responses(uow, items, req_counts),
            total=total,
            skip=skip,
            limit=limit,
        )

    def list_projects_summary(
        self,
        uow: UnitOfWork,
        requester_id: UUID,
        requester_roles: list[str],
        requester_tenant_id: UUID | None = None,
        search: str | None = None,
        stage: ProjectProgressFilter | None = None,
    ) -> list[ProjectSummaryResponse]:
        """Return every project visible to the caller (id/name/files), role-scoped, unpaginated.

        An ``admin`` sees every project in their own tenant; a ``super_admin``
        sees every project on the platform (same tenant-scoping rule as
        :meth:`list_all_projects`). A Member sees only projects they own or
        are assigned to (``ProjectMember`` — mirrors :meth:`list_projects`).

        ``stage`` optionally narrows the result by backlog-generation
        progress (derived live from Neo4j, best-effort — see
        :meth:`_get_progress_flags`): ``FRESH`` keeps projects with neither
        a Module/Feature nor a User Story yet; ``MODULE_FEATURE_ONLY`` keeps
        projects with a Module/Feature but no User Story yet;
        ``USER_STORY_CREATED`` keeps projects with at least one User Story.
        Omit ``stage`` to skip the Neo4j round-trip entirely.
        """
        if ROLE_ADMIN in requester_roles or ROLE_SUPER_ADMIN in requester_roles:
            tenant_filter = None if ROLE_SUPER_ADMIN in requester_roles else requester_tenant_id
            projects = uow.projects.list_for_scope(tenant_id=tenant_filter, search=search)
        else:
            projects = uow.projects.list_for_scope(
                owner_id=requester_id, member_user_id=requester_id, search=search
            )

        if stage is not None:
            progress = self._get_progress_flags([p.id for p in projects])
            empty_flags = ProjectProgressFlags(has_module_feature=False, has_user_story=False)
            flagged = [(p, progress.get(p.id, empty_flags)) for p in projects]
            if stage is ProjectProgressFilter.FRESH:
                projects = [
                    p for p, f in flagged if not f.has_module_feature and not f.has_user_story
                ]
            elif stage is ProjectProgressFilter.MODULE_FEATURE_ONLY:
                projects = [p for p, f in flagged if f.has_module_feature and not f.has_user_story]
            else:
                projects = [p for p, f in flagged if f.has_user_story]

        files_counts = uow.sources.get_source_counts_by_project([p.id for p in projects])
        return [
            ProjectSummaryResponse(id=p.id, name=p.name, files=files_counts.get(p.id, 0))
            for p in projects
        ]

    # ── Update ─────────────────────────────────────────────────────────────

    def update_project(
        self,
        project_id: UUID,
        payload: ProjectUpdate,
        requester_id: UUID,
        requester_roles: list[str],
        uow: UnitOfWork,
        requester_tenant_id: UUID | None = None,
    ) -> ProjectResponse:
        """Apply a partial update to a project (owner or admin only).

        Raises:
            NotFoundError: If the project does not exist.
            ForbiddenError: If the requester is neither the owner nor an admin.
            ConflictError: If the new name is already taken in the owner scope,
                or if this project's generation pipeline is currently running.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        self._assert_owner_or_admin(
            project,
            requester_id,
            requester_roles,
            requester_tenant_id,
            MSG_PROJECT_UPDATE_FORBIDDEN,
        )
        self._assert_project_not_locked_by_running_pipeline(project_id, uow)

        # Use model_fields_set to detect which fields the caller explicitly
        # included in the request body (true PATCH semantics).  This allows
        # {"description": null} to clear the field, while an omitted
        # "description" key leaves the existing value unchanged.
        updated_fields = payload.model_fields_set

        if "name" in updated_fields and payload.name is not None and payload.name != project.name:
            clash = uow.projects.get_by_name_and_owner(payload.name, project.owner_id)
            if clash is not None and clash.id != project_id:
                raise ConflictError(MSG_PROJECT_NAME_CONFLICT.format(name=payload.name))
            project.name = payload.name

        if "tenant_id" in updated_fields:
            project.tenant_id = self._authorize_tenant_reassignment(
                payload.tenant_id, requester_roles, uow
            )

        self._validate_llm_update(project, payload, updated_fields, uow)

        # allows explicit null, e.g. {"description": null}
        simple_updates = {
            "description": payload.description,
            "llm_provider": payload.llm_provider.value if payload.llm_provider else None,
            "llm_model": payload.llm_model,
            "project_type": payload.project_type.value if payload.project_type else None,
        }
        for field, new_value in simple_updates.items():
            if field in updated_fields:
                setattr(project, field, new_value)

        # uow.projects.save() handles flush + refresh; the service does not
        # touch the SQLAlchemy session directly.
        # The DB UniqueConstraint is the authoritative enforcer here too.
        try:
            project = uow.projects.update(project)
            uow.commit()
        except IntegrityError:
            uow.rollback()
            raise ConflictError(MSG_PROJECT_NAME_CONFLICT.format(name=project.name))

        self._sync_neo4j_project(project)
        self._record_project_updated_activity(project, actor_user_id=requester_id)
        logger.info("Project updated: id=%s name=%s", project_id, project.name)

        # Build response from the already-refreshed in-memory object — avoids a
        # redundant SELECT immediately after the UPDATE.
        files_count = uow.sources.get_source_counts_by_project([project.id]).get(project.id, 0)
        req_counts = self._get_user_story_counts([project.id]).get(
            project.id, UserStoryCounts(total=0, approved=0)
        )
        return self._build_response(project, files_count, req_counts)

    # ── Delete ─────────────────────────────────────────────────────────────

    def delete_project(
        self,
        project_id: UUID,
        requester_id: UUID,
        requester_roles: list[str],
        uow: UnitOfWork,
        requester_tenant_id: UUID | None = None,
    ) -> None:
        """Delete a project and every piece of data it owns (owner or admin only).

        Cascade order
        ─────────────
        1. Cancel every in-flight Celery task for the project (best-effort)
           so nothing keeps writing to Postgres/Neo4j for a project that's
           being deleted.
        2. Delete every source's S3 object (best-effort — a storage failure
           must not block the logical delete).
        3. PostgreSQL: hard-delete every project-scoped table — sources,
           fragment_embeddings, source_ingestions, project_tasks (cascades
           project_task_events), jira_integrations (cascades
           jira_sync_mappings), jira_sync_history, tap_sync_history,
           tap_ack_history, story_feedback_histories, incremental_histories,
           project_members.
        4. PostgreSQL: soft-delete the project row itself (``status="deleted"``,
           ``deleted_at`` set) — kept as the one remaining record that a
           project with this id ever existed.
        5. uow.commit() — Postgres committed atomically before touching Neo4j.
        6. Celery task enqueued to the ``neo4j_sync`` queue — deletes every
           node the project owns (Module/Feature/UserStory + version
           snapshots, Source/Fragment, ConfigSpec, GroupSpec,
           SourceCodeMetadata, SRSEvidence, ProjectMetadata, Project) with
           automatic retry, and best-effort cleans up the resulting orphaned
           ``tap_sync_mappings`` rows.

        Raises:
            NotFoundError:  If the project does not exist.
            ForbiddenError: If the requester is neither the owner nor an admin.
        """
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))

        self._assert_owner_or_admin(
            project,
            requester_id,
            requester_roles,
            requester_tenant_id,
            MSG_PROJECT_DELETE_FORBIDDEN,
        )

        from datetime import datetime

        self._cancel_in_flight_tasks(project_id)
        self._delete_project_sources_from_s3(project_id, uow)

        uow.source_ingestions.delete_by_project_id(project_id)
        uow.project_tasks.delete_by_project_id(project_id)
        uow.jira_integrations.delete_by_project_id(project_id)
        uow.jira_sync_history.delete_by_project_id(project_id)
        uow.tap_sync_history.delete_by_project_id(project_id)
        uow.tap_ack_history.delete_by_project_id(project_id)
        uow.story_feedback_histories.delete_by_project_id(project_id)
        uow.incremental_histories.delete_by_project_id(project_id)
        uow.project_members.delete_by_project_id(project_id)
        uow.fragment_embeddings.delete_by_project_id(project_id)
        uow.sources.delete_by_project_id(project_id)

        # Soft-delete: the project row is the one thing kept around, as a
        # record that a project with this id existed and was deleted.
        project.status = ProjectStatus.DELETED.value
        project.deleted_at = datetime.now(tz=UTC)

        # Commit Postgres atomically before touching any external store.
        uow.commit()

        # Enqueue async Neo4j graph cleanup with retry.
        #    The Postgres commit is already durable; if the broker is down the
        #    task is not delivered, but the graph will remain stale rather than
        #    blocking the HTTP response.  The task retries with exponential
        #    back-off on delivery failure.
        self._publisher.project_deleted(str(project_id))

        logger.info("Project deleted (cascade): id=%s", project_id)

    @staticmethod
    def _cancel_in_flight_tasks(project_id: UUID) -> None:
        """Best-effort: cancel every active Celery task for this project.

        Never raises — an already-running task finishing after the project
        is deleted is undesirable but not fatal; a cancellation failure must
        not block the delete itself.
        """
        from app.services.project_task_service import ProjectTaskService  # noqa: PLC0415

        try:
            result = ProjectTaskService().cancel_project(project_id)
            if result.get("cancelled_count"):
                logger.info(
                    "Cancelled %d in-flight task(s) for deleted project: id=%s",
                    result["cancelled_count"],
                    project_id,
                )
        except Exception:
            logger.warning(
                "Failed to cancel in-flight tasks for project id=%s before delete.",
                project_id,
                exc_info=True,
            )

    @staticmethod
    def _delete_project_sources_from_s3(project_id: UUID, uow: UnitOfWork) -> None:
        """Best-effort: delete every source's S3 object before the rows are hard-deleted.

        Once ``uow.sources.delete_by_project_id`` runs, the storage_key
        pointers are gone — this must run first. A storage failure here must
        not block the logical delete: the DB rows are the authoritative
        state, and the object is simply left for a later retention sweep.
        """
        import asyncio

        from app.clients.s3_client import delete_from_s3
        from app.core.exceptions import StorageError

        storage_keys = uow.sources.get_storage_keys_by_project(project_id)
        if not storage_keys:
            return

        async def _delete_all() -> None:
            for storage_key in storage_keys:
                try:
                    await delete_from_s3(storage_key)
                except StorageError:
                    logger.warning(
                        "S3 cleanup failed for deleted project id=%s key=%s; "
                        "object will persist until next cleanup.",
                        project_id,
                        storage_key,
                    )

        try:
            asyncio.run(_delete_all())
        except Exception:
            logger.warning(
                "S3 cleanup failed for deleted project id=%s; objects may persist.",
                project_id,
                exc_info=True,
            )

    # ── Dashboard stats ──────────────────────────────────────────────────

    @staticmethod
    def _resolve_dashboard_scope(
        uow: UnitOfWork,
        requester_id: UUID,
        requester_roles: list[str],
        requester_tenant_id: UUID | None,
        tenant_id_filter: UUID | None = None,
    ) -> list[UUID] | None:
        """Return the project-id scope for dashboard stats, or ``None`` for
        unscoped/platform-wide (super_admin only, and only when
        ``tenant_id_filter`` is omitted).

        - ``super_admin`` → ``None`` (every project, every tenant) unless
          ``tenant_id_filter`` is given, in which case it narrows to that one
          tenant — this is super_admin's one area of project visibility
          (list + summary); see the module docstring in
          ``app/db/seed_db.py``.
        - ``admin`` (Client Admin) → every project in their own tenant;
          ``tenant_id_filter`` is ignored — they are always scoped to their
          own tenant regardless of what they pass.
        - anyone else (Member) → only projects they own or hold a
          ``ProjectMember`` row for; ``tenant_id_filter`` is ignored.
        """
        if ROLE_SUPER_ADMIN in requester_roles:
            if tenant_id_filter is None:
                return None
            return uow.projects.list_ids_by_tenant(tenant_id_filter)
        if ROLE_ADMIN in requester_roles:
            if requester_tenant_id is None:
                return []
            return uow.projects.list_ids_by_tenant(requester_tenant_id)
        return uow.projects.list_ids_owned_or_member(requester_id)

    async def get_dashboard_stats(
        self,
        uow: UnitOfWork,
        requester_id: UUID,
        requester_roles: list[str],
        requester_tenant_id: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> DashboardStatsResponse:
        """Aggregate dashboard statistics from PostgreSQL and Neo4j, scoped
        to what the requester may see (see :meth:`_resolve_dashboard_scope`).

        ``tenant_id`` lets a super_admin narrow the platform-wide view to
        one tenant; it is ignored for every other role, who are always
        scoped to their own tenant/assigned projects regardless.

        Best-effort Neo4j call: a graph outage returns zeros for module/
        feature/story counts rather than aborting the response.
        """
        now = datetime.now(UTC)
        start_of_month = datetime(now.year, now.month, 1, tzinfo=UTC)

        project_ids = self._resolve_dashboard_scope(
            uow, requester_id, requester_roles, requester_tenant_id, tenant_id_filter=tenant_id
        )

        total_count = uow.project_stats.count_total_projects(project_ids=project_ids)
        active_count = uow.project_stats.count_active_projects(project_ids=project_ids)
        active_count_current_month = uow.project_stats.count_active_projects_since(
            start_of_month, project_ids=project_ids
        )
        running_pipelines = uow.project_stats.count_running_pipelines(project_ids=project_ids)
        completion_stats = uow.project_stats.get_pipeline_completion_stats(project_ids=project_ids)

        try:
            graph_counts = (
                self._neo4j.get_global_module_feature_story_counts()
                if project_ids is None
                else self._neo4j.get_module_feature_story_counts_for_projects(project_ids)
            )
        except Exception as exc:
            logger.warning("Neo4j counts failed: %s", exc)
            graph_counts = {
                "total_modules": 0,
                "total_features": 0,
                "total_stories": 0,
                "approved_stories": 0,
                "pending_jira_sync_stories": 0,
                "pending_tap_sync_stories": 0,
            }

        longest = None
        if completion_stats["longest_project_id"] is not None:
            longest = LongestCompletionProject(
                project_id=completion_stats["longest_project_id"],
                project_name=completion_stats["longest_project_name"],
                duration_seconds=completion_stats["longest_duration_seconds"],
            )

        return DashboardStatsResponse(
            total_projects=total_count,
            active_projects=active_count,
            active_projects_current_month=active_count_current_month,
            total_modules=graph_counts["total_modules"],
            total_features=graph_counts["total_features"],
            total_stories=graph_counts["total_stories"],
            running_pipelines=running_pipelines,
            avg_pipeline_completion_seconds=completion_stats["avg_seconds"],
            longest_completion_project=longest,
            pending_jira_sync_count=graph_counts["pending_jira_sync_stories"],
            pending_tap_sync_count=graph_counts["pending_tap_sync_stories"],
            approved_user_stories=graph_counts["approved_stories"],
        )
