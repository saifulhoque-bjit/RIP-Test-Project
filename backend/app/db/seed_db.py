"""Seed default roles and permissions.

Called once at application startup — fully idempotent (skips any record that
already exists).

Roles seeded
────────────
member   → full source/story/module/feature/task/fragment/incremental-update/
           notification/export access, including approve/reject power over
           User Stories (enforced per-project via ProjectMember, see
           app/models/postgres/project_member_model.py); project entity CRUD
           (create/rename/delete) is Client-Admin/super_admin-only — a member
           can only view projects they're assigned to via ProjectMember; no
           tenant, user, integration, or observability administration
admin    → everything member has, plus project create/update/delete, user
           view/invite/remove/role-management, integrations, exports, and
           observability; NOT tenant management (tenant:create/view/update/
           delete are super_admin-only — see require_roles(ROLE_SUPER_ADMIN)
           on app/routes/v1/tenants.py's core CRUD routes)
super_admin → platform-level administration: full tenant/user/role/
           invitation/observability management across every tenant, plus a
           cross-tenant project list/summary view (project:view only —
           NOT project content management, which stays Client Admin/Member
           territory). Reserved for the seeded bootstrap user, see
           app/db/seed_super_admin.py. NOTE: this permission set is
           informational only for this role — require_roles() (app/deps.py)
           unconditionally lets super_admin bypass every role check
           regardless of the permission catalogue, so shrinking this list
           does NOT actually restrict what a super_admin can call via the
           API; see app/deps.py::require_roles

Every newly registered user is automatically assigned the ``member`` role by
:meth:`~app.services.user_service.UserService.get_or_create_by_cognito_sub`.

``member`` replaces the former ``pm``/``viewer`` split (2026-07-22) — any
user still holding one of those is migrated onto ``member`` by
:func:`_consolidate_pm_viewer_to_member` before the old roles are dropped.
"""

from __future__ import annotations

from app.db.session import SessionLocal
from app.models.postgres.permission_model import Permission
from app.models.postgres.role_model import Role
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Permissions catalogue ──────────────────────────────────────────────────
# ``name`` follows a singular ``resource:action`` convention, e.g.
# ``project:create``. One entry per real capability exposed under app/routes.

_PERMISSIONS: list[dict[str, str]] = [
    # Projects
    {
        "name": "project:create",
        "resource": "project",
        "action": "create",
        "description": "Create new projects",
    },
    {
        "name": "project:view",
        "resource": "project",
        "action": "view",
        "description": "View and list projects",
    },
    {
        "name": "project:update",
        "resource": "project",
        "action": "update",
        "description": "Update project details",
    },
    {
        "name": "project:delete",
        "resource": "project",
        "action": "delete",
        "description": "Delete projects",
    },
    # Project tasks (pipeline runs)
    {
        "name": "task:view",
        "resource": "task",
        "action": "view",
        "description": "View project background tasks",
    },
    {
        "name": "task:cancel",
        "resource": "task",
        "action": "cancel",
        "description": "Cancel running project background tasks",
    },
    # Sources
    {
        "name": "source:create",
        "resource": "source",
        "action": "create",
        "description": "Upload source files",
    },
    {
        "name": "source:view",
        "resource": "source",
        "action": "view",
        "description": "View, list, and download source files",
    },
    {
        "name": "source:delete",
        "resource": "source",
        "action": "delete",
        "description": "Delete source files",
    },
    # Fragments
    {
        "name": "fragment:view",
        "resource": "fragment",
        "action": "view",
        "description": "View source fragments",
    },
    {
        "name": "fragment:update",
        "resource": "fragment",
        "action": "update",
        "description": "Update fragment bounding boxes",
    },
    # Modules
    {
        "name": "module:view",
        "resource": "module",
        "action": "view",
        "description": "View and list modules",
    },
    {
        "name": "module:update",
        "resource": "module",
        "action": "update",
        "description": "Update module status and sync flags",
    },
    {
        "name": "module:regenerate",
        "resource": "module",
        "action": "regenerate",
        "description": "Regenerate modules",
    },
    # Features
    {
        "name": "feature:view",
        "resource": "feature",
        "action": "view",
        "description": "View and list features",
    },
    {
        "name": "feature:update",
        "resource": "feature",
        "action": "update",
        "description": "Update feature status and sync flags",
    },
    {
        "name": "feature:regenerate",
        "resource": "feature",
        "action": "regenerate",
        "description": "Regenerate features",
    },
    # User stories
    {
        "name": "story:view",
        "resource": "story",
        "action": "view",
        "description": "View and list user stories",
    },
    {
        "name": "story:update",
        "resource": "story",
        "action": "update",
        "description": "Update user story bounding boxes and sync flags",
    },
    {
        "name": "story:delete",
        "resource": "story",
        "action": "delete",
        "description": "Delete user stories",
    },
    {
        "name": "story:regenerate",
        "resource": "story",
        "action": "regenerate",
        "description": "Regenerate user stories",
    },
    {
        "name": "story:approve",
        "resource": "story",
        "action": "approve",
        "description": "Approve user stories",
    },
    {
        "name": "story:reject",
        "resource": "story",
        "action": "reject",
        "description": "Reject user stories (send back for edit)",
    },
    # Incremental updates (pending change review)
    {
        "name": "incremental_update:view",
        "resource": "incremental_update",
        "action": "view",
        "description": "View pending incremental changes",
    },
    {
        "name": "incremental_update:accept",
        "resource": "incremental_update",
        "action": "accept",
        "description": "Accept a pending incremental change",
    },
    {
        "name": "incremental_update:reject",
        "resource": "incremental_update",
        "action": "reject",
        "description": "Reject a pending incremental change",
    },
    # Users / tenant membership
    {
        "name": "user:view",
        "resource": "user",
        "action": "view",
        "description": "View and list users",
    },
    {
        "name": "user:invite",
        "resource": "user",
        "action": "invite",
        "description": "Invite a user into a tenant",
    },
    {
        "name": "user:remove",
        "resource": "user",
        "action": "remove",
        "description": "Remove or deactivate a user",
    },
    {
        "name": "user:manage_roles",
        "resource": "user",
        "action": "manage_roles",
        "description": "Assign or revoke user roles",
    },
    # Tenants
    {
        "name": "tenant:create",
        "resource": "tenant",
        "action": "create",
        "description": "Create tenants",
    },
    {
        "name": "tenant:view",
        "resource": "tenant",
        "action": "view",
        "description": "View and list tenants",
    },
    {
        "name": "tenant:update",
        "resource": "tenant",
        "action": "update",
        "description": "Update tenant details",
    },
    {
        "name": "tenant:delete",
        "resource": "tenant",
        "action": "delete",
        "description": "Deactivate tenants",
    },
    # Notifications
    {
        "name": "notification:view",
        "resource": "notification",
        "action": "view",
        "description": "View notifications and unread count",
    },
    {
        "name": "notification:update",
        "resource": "notification",
        "action": "update",
        "description": "Mark notifications as read",
    },
    # Third-party integrations (Jira / TAP)
    {
        "name": "integration:create",
        "resource": "integration",
        "action": "create",
        "description": "Connect a third-party integration",
    },
    {
        "name": "integration:view",
        "resource": "integration",
        "action": "view",
        "description": "View integration settings and sync history",
    },
    {
        "name": "integration:update",
        "resource": "integration",
        "action": "update",
        "description": "Update integration settings",
    },
    {
        "name": "integration:delete",
        "resource": "integration",
        "action": "delete",
        "description": "Disconnect a third-party integration",
    },
    {
        "name": "integration:sync",
        "resource": "integration",
        "action": "sync",
        "description": "Trigger or acknowledge an integration sync",
    },
    # Export
    {
        "name": "export:create",
        "resource": "export",
        "action": "create",
        "description": "Export project data",
    },
    # Observability (admin dashboards)
    {
        "name": "observability:view",
        "resource": "observability",
        "action": "view",
        "description": "View system status and dead-letter queue",
    },
    {
        "name": "observability:manage",
        "resource": "observability",
        "action": "manage",
        "description": "Replay dead-lettered messages",
    },
]

# ── Role definitions ───────────────────────────────────────────────────────

_MEMBER_PERMISSIONS = [
    # Project entity CRUD is Client-Admin/super_admin-only — Members can
    # only view projects they're assigned to (see
    # ProjectService.assert_project_access / app/routes/v1/projects.py).
    "project:view",
    "task:view",
    "task:cancel",
    "source:create",
    "source:view",
    "source:delete",
    "fragment:view",
    "fragment:update",
    "module:view",
    "module:update",
    "module:regenerate",
    "feature:view",
    "feature:update",
    "feature:regenerate",
    "story:view",
    "story:update",
    "story:delete",
    "story:regenerate",
    "story:approve",
    "story:reject",
    "incremental_update:view",
    "incremental_update:accept",
    "incremental_update:reject",
    "notification:view",
    "notification:update",
    "export:create",
]

_ADMIN_PERMISSIONS = [
    # Project entity CRUD (create/rename/delete) is Client-Admin/super_admin-only.
    "project:create",
    "project:view",
    "project:update",
    "project:delete",
    "task:view",
    "task:cancel",
    "source:create",
    "source:view",
    "source:delete",
    "fragment:view",
    "fragment:update",
    "module:view",
    "module:update",
    "module:regenerate",
    "feature:view",
    "feature:update",
    "feature:regenerate",
    "story:view",
    "story:update",
    "story:delete",
    "story:regenerate",
    "story:approve",
    "story:reject",
    "incremental_update:view",
    "incremental_update:accept",
    "incremental_update:reject",
    "user:view",
    "user:invite",
    "user:remove",
    "user:manage_roles",
    # Tenant — read-only visibility of the Client Admin's OWN tenant
    # (GET /tenants/ and GET /tenants/{id}, tenant-scoped in TenantService;
    # a Client Admin can never see another tenant). Tenant *management*
    # (create/update/delete) is deliberately NOT granted — POST/PATCH/DELETE
    # /tenants stay gated require_roles(ROLE_SUPER_ADMIN) only (see
    # app/routes/v1/tenants.py). LLM-provider config remains the other
    # tenant-scoped Client Admin surface (require_roles(SUPER_ADMIN, ADMIN)).
    "tenant:view",
    "notification:view",
    "notification:update",
    "integration:create",
    "integration:view",
    "integration:update",
    "integration:delete",
    "integration:sync",
    "export:create",
    "observability:view",
    "observability:manage",
]

_SUPER_ADMIN_PERMISSIONS = [
    # Projects — cross-tenant list/summary visibility only. Day-to-day
    # project content work (create/update/delete, sources, modules,
    # features, stories, tasks, fragments, incremental updates,
    # notifications, integrations, exports) is Client Admin/Member
    # territory within their own tenant, not a platform-admin concern —
    # see _ADMIN_PERMISSIONS/_MEMBER_PERMISSIONS for that surface.
    "project:view",
    # Tenants — full platform-level tenant lifecycle management; the one
    # resource no other role can touch (see the comment on tenant:* in
    # _ADMIN_PERMISSIONS above).
    "tenant:create",
    "tenant:view",
    "tenant:update",
    "tenant:delete",
    # Users — full cross-tenant user administration, including role
    # assignment. There is no separate "role catalogue" permission: custom
    # role CRUD (POST/PATCH/DELETE /roles) is gated by
    # require_roles(ROLE_SUPER_ADMIN) directly, independent of this
    # permission catalogue (see app/routes/v1/roles.py).
    "user:view",
    "user:invite",
    "user:remove",
    "user:manage_roles",
    # Observability
    "observability:view",
    "observability:manage",
]

# Permission names retired by the 2026-07-22 singular resource:action rename
# (superseded by the entries in _PERMISSIONS above). Detached from any role
# and deleted outright by _remove_deprecated_permissions.
_DEPRECATED_PERMISSIONS = [
    "tests:read",
    "tests:write",
    "tests:delete",
    "tests:execute",
    "sources:read",
    "sources:write",
    "sources:delete",
    "projects:read",
    "projects:write",
    "projects:delete",
    "users:read",
    "users:write",
]

_ROLES: dict[str, dict] = {
    "member": {
        "display_name": "Member",
        "description": (
            "Standard tenant member — full project, source, story, module, "
            "feature, and task management; no tenant, user, integration, or "
            "observability administration"
        ),
        "permissions": _MEMBER_PERMISSIONS,
    },
    "admin": {
        "display_name": "Client Admin",
        "description": (
            "Full access to their own tenant's projects, users, and "
            "integrations, including user and role management; tenant "
            "management itself (create/view/update/delete) is super_admin-only"
        ),
        "permissions": _ADMIN_PERMISSIONS,
    },
    "super_admin": {
        "display_name": "Super Admin",
        # Kept under the roles.description varchar(255) limit (see
        # app/models/postgres/role_model.py) — the previous wording overflowed
        # it and broke seeding on a fresh database.
        "description": (
            "Platform-level administrator — full tenant, user, role, "
            "invitation, and observability management across every tenant, "
            "plus a cross-tenant project list/summary view; day-to-day "
            "project content management stays Client Admin/Member territory"
        ),
        "permissions": _SUPER_ADMIN_PERMISSIONS,
    },
}


# ── Public entry point ─────────────────────────────────────────────────────


def seed_db() -> None:
    """Idempotently insert default roles and permissions.

    Safe to call on every application startup.
    """
    session = SessionLocal()
    try:
        _remove_deprecated_roles(session)
        _remove_deprecated_permissions(session)
        _seed_permissions(session)
        session.flush()
        _seed_roles(session)
        session.flush()
        _consolidate_pm_viewer_to_member(session)
        session.commit()
        logger.info("seed_db complete")
    except Exception:
        session.rollback()
        logger.exception("seed_db failed — transaction rolled back")
        raise
    finally:
        session.close()


# ── Helpers ────────────────────────────────────────────────────────────────


def _remove_deprecated_roles(session) -> None:  # type: ignore[no-untyped-def]
    """Remove roles that are no longer part of the application."""
    _DEPRECATED_ROLES = ["editor"]
    for role_name in _DEPRECATED_ROLES:
        role = session.query(Role).filter_by(name=role_name).first()
        if role is not None:
            session.delete(role)
            logger.info("Removed deprecated role: %s", role_name)


def _remove_deprecated_permissions(session) -> None:  # type: ignore[no-untyped-def]
    """Detach and delete permissions retired by the singular-naming rename.

    Must run before ``_seed_roles`` re-syncs role permissions, otherwise
    roles would end up holding both the old and new names for the same
    capability.
    """
    for pname in _DEPRECATED_PERMISSIONS:
        perm = session.query(Permission).filter_by(name=pname).first()
        if perm is None:
            continue
        for role in list(perm.roles):
            role.permissions.remove(perm)
        session.delete(perm)
        logger.info("Removed deprecated permission: %s", pname)


def _consolidate_pm_viewer_to_member(session) -> None:  # type: ignore[no-untyped-def]
    """One-time data migration: merge the deprecated ``pm``/``viewer`` roles into ``member``.

    Runs after ``_seed_roles`` so ``member`` already exists. Reassigns any
    user still on ``pm``/``viewer`` to ``member`` *before* deleting the old
    role — ``user_roles.role_id`` cascades on delete (see
    ``app/models/postgres/user_model.py``), so skipping this step would
    silently strip the role from those users instead of migrating them.
    """
    member_role = session.query(Role).filter_by(name="member").first()
    if member_role is None:
        return
    for deprecated_name in ("pm", "viewer"):
        role = session.query(Role).filter_by(name=deprecated_name).first()
        if role is None:
            continue
        for user in role.users:
            if member_role not in user.roles:
                user.roles.append(member_role)
        session.delete(role)
        logger.info("Consolidated deprecated role '%s' into 'member'", deprecated_name)


def _seed_permissions(session) -> None:  # type: ignore[no-untyped-def]
    for pdef in _PERMISSIONS:
        exists = session.query(Permission).filter_by(name=pdef["name"]).first()
        if exists is None:
            session.add(Permission(**pdef))
            logger.debug("Seeded permission: %s", pdef["name"])


def _seed_roles(session) -> None:  # type: ignore[no-untyped-def]
    for role_name, rdef in _ROLES.items():
        role = session.query(Role).filter_by(name=role_name).first()
        if role is None:
            role = Role(
                name=role_name,
                display_name=rdef["display_name"],
                description=rdef["description"],
            )
            session.add(role)
            session.flush()
            logger.debug("Seeded role: %s", role_name)
        elif role.display_name != rdef["display_name"]:
            # Keep the built-in roles' display_name in sync on every startup
            # (e.g. after the 0018 migration backfilled it from `name`).
            role.display_name = rdef["display_name"]

        # Sync permissions for existing roles too (idempotent) — grants any
        # newly-listed permission and revokes any no longer listed, so a
        # role definition change (e.g. shrinking admin's permission set)
        # takes effect on every startup, not just for freshly-created roles.
        desired_perm_names = set(rdef["permissions"])
        existing_perm_names = {p.name for p in role.permissions}

        for pname in desired_perm_names - existing_perm_names:
            perm = session.query(Permission).filter_by(name=pname).first()
            if perm is not None:
                role.permissions.append(perm)

        # list() copy is required — .remove() below mutates role.permissions
        # itself, which would corrupt in-progress iteration otherwise.
        for perm in list(role.permissions):
            if perm.name not in desired_perm_names:
                role.permissions.remove(perm)
                logger.info("Revoked permission '%s' from role '%s'", perm.name, role_name)
