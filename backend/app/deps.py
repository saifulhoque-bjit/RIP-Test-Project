"""Shared FastAPI dependencies.

- ``get_uow``                  → yields an open UnitOfWork per request.
- ``get_current_user``         → validates a Cognito JWT; returns raw claims dict.
- ``get_current_db_user``      → validates JWT *and* upserts the User record in
                                 PostgreSQL; returns the ORM ``User`` instance
                                 with roles eagerly loaded.
- ``require_roles``            → dependency factory; enforces role membership.
- ``require_permissions``      → dependency factory; enforces fine-grained perms.
- ``get_neo4j_project_repo``   → returns a Neo4jProjectRepository bound to the
                                 application-level driver singleton; allows
                                 service classes to receive the repo via
                                 Depends() rather than constructing it inline.
- ``get_project_service``      → dependency factory; constructs ProjectService
                                 with the injected Neo4j repository.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Generator
from typing import Annotated, Any
from uuid import UUID

from fastapi import Cookie, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError

from app.core.constants import ROLE_SUPER_ADMIN
from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.messages import (
    MSG_AUTH_CREDENTIALS_REQUIRED,
    MSG_AUTH_INVALID_TOKEN,
    MSG_AUTH_TOKEN_EXPIRED,
    MSG_PROJECT_NOT_FOUND,
    MSG_RBAC_PERMISSIONS_MISSING,
    MSG_RBAC_ROLES_REQUIRED,
)
from app.core.security import decode_cognito_token
from app.db.async_unit_of_work import AsyncUnitOfWork
from app.db.neo4j import get_neo4j_driver
from app.db.unit_of_work import UnitOfWork
from app.messaging.project_publisher import CeleryProjectEventPublisher
from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
from app.repositories.neo4j.project_repository import (
    ProjectRepository as Neo4jProjectRepository,
)
from app.repositories.neo4j.user_story_repository import UserStoryRepository
from app.services.project_service import ProjectService
from app.services.project_task_service import ProjectTaskService
from app.services.setting_service import SettingService
from app.services.user_service import UserService

# auto_error=False so that cookie-only requests are not immediately rejected
_bearer = HTTPBearer(auto_error=False)


# ── Unit of Work ───────────────────────────────────────────────────────────


def get_uow() -> Generator[UnitOfWork]:
    """Yield a ``UnitOfWork`` instance for the duration of the request."""
    with UnitOfWork() as uow:
        yield uow


async def get_async_uow() -> AsyncGenerator[AsyncUnitOfWork]:
    """Yield an ``AsyncUnitOfWork`` instance for async routes/services."""
    async with AsyncUnitOfWork() as uow:
        yield uow


# ── JWT claims ─────────────────────────────────────────────────────────────


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    cookie_access_token: str | None = Cookie(default=None, alias="access_token"),
) -> dict[str, Any]:
    """Validate a Cognito JWT and return its decoded claims.

    Token resolution order (first non-empty value wins):
        1. ``access_token`` HttpOnly cookie  — browser / SPA clients.
        2. ``Authorization: Bearer <token>`` header — API / mobile clients.

    Raises HTTP 401 for any missing or invalid token so that no internal
    implementation detail leaks out.
    """
    token = cookie_access_token or (credentials.credentials if credentials else None)
    if not token:
        raise UnauthorizedError(MSG_AUTH_CREDENTIALS_REQUIRED)
    try:
        claims = decode_cognito_token(token)
    except ExpiredSignatureError:
        raise UnauthorizedError(MSG_AUTH_TOKEN_EXPIRED)
    except JWTError:
        raise UnauthorizedError(MSG_AUTH_INVALID_TOKEN)
    return claims


# ── DB-backed user ─────────────────────────────────────────────────────────


def get_current_db_user(
    claims: dict[str, Any] = Depends(get_current_user),
    uow: UnitOfWork = Depends(get_uow),
) -> Any:
    """Validate the JWT *and* return an ORM ``User`` with roles eagerly loaded.

    On every authenticated request this dependency:
        1. Resolves the Cognito JWT via :func:`get_current_user`.
        2. Looks up the local ``User`` record matching the JWT claims and
           backfills any missing mutable fields — it does **not** create one.
           A valid Cognito session alone isn't enough: the caller also needs
           an accepted invitation (or, for the Super Admin, startup seeding)
           to have a local row at all. Raises HTTP 403 if none exists.
        3. Raises HTTP 403 if the account has been deactivated.

    The request-scoped ``UnitOfWork`` from :func:`get_uow` is shared here.
    FastAPI deduplicates generator dependencies within a single request, so
    both this dependency and the route handler receive the *same* session —
    eliminating the second DB connection that a private ``with UnitOfWork()``
    block would open.
    """
    sub: str = claims.get("sub", "")
    email: str = claims.get("email", claims.get("username", ""))
    username: str | None = claims.get("cognito:username") or claims.get("username")
    name: str | None = claims.get("name")
    is_verified: bool = bool(claims.get("email_verified", False))

    user = UserService(uow).get_authenticated_user(
        sub=sub,
        email=email,
        name=name,
        is_verified=is_verified,
        username=username,
    )
    return user


# ── RBAC dependency factories ──────────────────────────────────────────────


def require_roles(*roles: str) -> Callable:
    """Return a FastAPI dependency that enforces role membership.

    The caller must hold **at least one** of the listed roles.

    Usage::

        _admin: User = Depends(require_roles("admin"))
        _staff: User = Depends(require_roles("admin", "editor"))
    """

    def _dependency(user: Any = Depends(get_current_db_user)) -> Any:
        user_role_names = {r.name for r in user.roles}
        is_authorized = ROLE_SUPER_ADMIN in user_role_names or bool(
            user_role_names.intersection(roles)
        )
        if not is_authorized:
            raise ForbiddenError(MSG_RBAC_ROLES_REQUIRED.format(roles=", ".join(roles)))
        return user

    return _dependency


def require_exact_roles(*roles: str) -> Callable:
    """Return a FastAPI dependency that enforces role membership with NO
    ``super_admin`` bypass.

    Identical to :func:`require_roles` except the caller must hold at least
    one of the *listed* roles literally — ``super_admin`` does not
    automatically pass. Use this only where ``super_admin`` is deliberately
    excluded from a capability (e.g. ``POST /projects`` — project creation
    is a Client Admin/Member responsibility within their own tenant;
    super_admin's platform-level role is limited to tenant/user/role/
    invitation/observability management plus a cross-tenant project list/
    summary view, not project content — see the ``super_admin`` role
    definition in ``app/db/seed_db.py``). Everywhere else, prefer
    :func:`require_roles`, which is the correct default.

    Usage::

        _: User = Depends(require_exact_roles("admin"))
    """

    def _dependency(user: Any = Depends(get_current_db_user)) -> Any:
        user_role_names = {r.name for r in user.roles}
        if not user_role_names.intersection(roles):
            raise ForbiddenError(MSG_RBAC_ROLES_REQUIRED.format(roles=", ".join(roles)))
        return user

    return _dependency


def require_permissions(*permissions: str) -> Callable:
    """Return a FastAPI dependency that enforces fine-grained permissions.

    The caller must hold **all** of the listed permissions across their roles.

    Usage::

        _: User = Depends(require_permissions("project:view", "project:update"))
    """
    required = frozenset(permissions)

    def _dependency(user: Any = Depends(get_current_db_user)) -> Any:
        user_perms = {perm.name for role in user.roles for perm in role.permissions}
        missing = required - user_perms
        if missing:
            raise ForbiddenError(
                MSG_RBAC_PERMISSIONS_MISSING.format(permissions=", ".join(sorted(missing)))
            )
        return user

    return _dependency


def require_project_access(level: str) -> Callable:
    """Return a FastAPI dependency that loads the ``project_id`` path param's
    ``Project`` and enforces content-level access via
    ``ProjectService.assert_project_access``.

    ``level`` is one of ``"read"``, ``"write"``, ``"approve"`` — see that
    method's docstring for exactly which callers pass at each level. Only
    fits routes with ``project_id`` as a path parameter; routes where the
    project is identified another way (form field, or reachable only via a
    child entity ID) call ``ProjectService.assert_project_access`` directly
    instead (see app/routes/v1/sources.py, app/routes/v1/user_stories.py).

    Usage::

        _project: Project = Depends(require_project_access("read"))
    """

    def _dependency(
        project_id: UUID,
        user: Any = Depends(get_current_db_user),
        uow: UnitOfWork = Depends(get_uow),
    ) -> Any:
        project = uow.projects.get_by_uuid(project_id)
        if project is None:
            raise NotFoundError(MSG_PROJECT_NOT_FOUND.format(project_id=project_id))
        ProjectService.assert_project_access(
            project=project,
            requester_id=user.id,
            requester_roles=user.role_names,
            requester_tenant_id=user.tenant_id,
            level=level,
            uow=uow,
        )
        return project

    return _dependency


# ── Infrastructure dependencies ────────────────────────────────────────────


def get_neo4j_project_repo() -> Neo4jProjectRepository:
    """Return a :class:`Neo4jProjectRepository` bound to the driver singleton.

    Injecting this via ``Depends()`` keeps service classes free of direct
    infrastructure imports (``get_neo4j_driver()`` stays in this module only).
    """
    return Neo4jProjectRepository(get_neo4j_driver())


def get_module_feature_repo() -> ModuleFeatureRepository:
    """Return a :class:`ModuleFeatureRepository` bound to the driver singleton."""
    return ModuleFeatureRepository(get_neo4j_driver())


def get_user_story_repo() -> UserStoryRepository:
    """Return a :class:`UserStoryRepository` bound to the driver singleton."""
    return UserStoryRepository(get_neo4j_driver())


# ── Service factories ──────────────────────────────────────────────────────

_Neo4jProjectRepoDep = Annotated[Neo4jProjectRepository, Depends(get_neo4j_project_repo)]
_ModuleFeatureRepoDep = Annotated[ModuleFeatureRepository, Depends(get_module_feature_repo)]
_UserStoryRepoDep = Annotated[UserStoryRepository, Depends(get_user_story_repo)]


def get_project_service(
    neo4j_repo: _Neo4jProjectRepoDep,
    module_feature_repo: _ModuleFeatureRepoDep,
    user_story_repo: _UserStoryRepoDep,
) -> ProjectService:
    """Construct :class:`ProjectService` with injected repositories.

    Centralised here so that any future router (v2, etc.) can reuse the
    same wiring without duplication.
    """
    return ProjectService(
        neo4j_project_repo=neo4j_repo,
        event_publisher=CeleryProjectEventPublisher(),
        setting_service=SettingService(),
        module_feature_repo=module_feature_repo,
        user_story_repo=user_story_repo,
    )


def get_project_task_service() -> ProjectTaskService:
    """Return a :class:`ProjectTaskService` instance.

    ProjectTaskService is stateless between requests, so a new instance
    per request costs nothing and keeps the DI graph consistent.
    """
    return ProjectTaskService()


# ── Jira integration dependencies ─────────────────────────────────────────


def get_jira_integration_service():
    """Return a :class:`JiraIntegrationService` instance."""
    from app.services.jira_integration_service import JiraIntegrationService

    return JiraIntegrationService()


def get_jira_sync_service():
    """Return a :class:`JiraSyncService` with injected Neo4j repositories."""
    from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
    from app.repositories.neo4j.user_story_repository import UserStoryRepository
    from app.services.jira_sync_service import JiraSyncService

    driver = get_neo4j_driver()
    return JiraSyncService(
        module_feature_repo=ModuleFeatureRepository(driver),
        user_story_repo=UserStoryRepository(driver),
    )


# ── Export dependencies ────────────────────────────────────────────────────


def get_export_service():
    """Return an :class:`ExportService` with injected Neo4j repositories."""
    from app.repositories.neo4j.group_spec_repository import GroupSpecRepository
    from app.repositories.neo4j.project_metadata_repository import ProjectMetadataRepository
    from app.repositories.neo4j.user_story_repository import UserStoryRepository
    from app.services.export_service import ExportService

    driver = get_neo4j_driver()
    return ExportService(
        user_story_repo=UserStoryRepository(driver),
        group_spec_repo=GroupSpecRepository(driver),
        project_metadata_repo=ProjectMetadataRepository(driver),
    )


# ── TAP integration dependencies ───────────────────────────────────────────


def get_tap_sync_service():
    """Return a :class:`TapSyncService` with injected Neo4j repositories."""
    from app.repositories.neo4j.module_feature_repository import ModuleFeatureRepository
    from app.repositories.neo4j.user_story_repository import UserStoryRepository
    from app.services.tap_sync_service import TapSyncService

    driver = get_neo4j_driver()
    return TapSyncService(
        module_feature_repo=ModuleFeatureRepository(driver),
        user_story_repo=UserStoryRepository(driver),
    )


def get_tap_integration_service():
    """Return a :class:`TapIntegrationService` instance."""
    from app.services.tap_integration_service import TapIntegrationService

    return TapIntegrationService()
