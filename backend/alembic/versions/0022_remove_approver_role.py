"""remove the approver role, folding its access into member

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-28

Retires the ``approver`` role entirely. Its capabilities (chiefly the User
Story approve/reject power) are now held by ``member`` — a project Member's
``ProjectMember`` row grants the ``"approve"`` access level directly (see
``ProjectService.assert_project_access``), and ``story:approve``/
``story:reject`` were already in the member permission set.

Data migration (all steps are no-ops on a database where ``approver`` never
existed, e.g. a fresh install seeded after this revision):

1. ``project_members`` — promote every project-scoped ``approver`` row to
   ``member``, deleting it instead when that (project, user) already holds a
   ``member`` row (the composite PK ``(project_id, user_id, role)`` would
   otherwise collide).
2. ``user_roles`` — repoint every tenant-wide ``approver`` assignment to the
   ``member`` role, deleting it instead when the user already holds
   ``member`` (the PK ``(user_id, role_id)`` would otherwise collide).
3. ``invitations`` — repoint every pending invitation issued with the
   ``approver`` role to ``member`` (``role_id`` has no uniqueness
   constraint here, so a plain ``UPDATE`` suffices, no dedup needed).
4. ``role_permissions`` + ``roles`` — drop the ``approver`` role's permission
   links and the role row itself.

Downgrade recreates the ``approver`` role and its read-only permission set so
the role catalogue is restored, but is otherwise lossy: it cannot know which
users/members were originally approvers, so no assignments are restored.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The approver role's original read-only permission set (see the pre-0022
# _APPROVER_PERMISSIONS in app/db/seed_db.py) — used only to rebuild the role
# on downgrade.
_APPROVER_PERMISSIONS = (
    "project:view",
    "task:view",
    "source:view",
    "fragment:view",
    "module:view",
    "feature:view",
    "story:view",
    "story:approve",
    "story:reject",
    "incremental_update:view",
    "incremental_update:accept",
    "incremental_update:reject",
    "notification:view",
    "notification:update",
)


def upgrade() -> None:
    """Fold approver into member and delete the approver role."""
    # 1. Project-scoped memberships: dedup then promote approver -> member.
    op.execute(
        """
        DELETE FROM project_members pm_a
        WHERE pm_a.role = 'approver'
          AND EXISTS (
            SELECT 1 FROM project_members pm_b
            WHERE pm_b.project_id = pm_a.project_id
              AND pm_b.user_id = pm_a.user_id
              AND pm_b.role = 'member'
          )
        """
    )
    op.execute("UPDATE project_members SET role = 'member' WHERE role = 'approver'")

    # 2. Tenant-wide role assignments: dedup then repoint approver -> member.
    op.execute(
        """
        DELETE FROM user_roles ur
        WHERE ur.role_id = (SELECT id FROM roles WHERE name = 'approver')
          AND EXISTS (
            SELECT 1 FROM user_roles ur2
            WHERE ur2.user_id = ur.user_id
              AND ur2.role_id = (SELECT id FROM roles WHERE name = 'member')
          )
        """
    )
    op.execute(
        """
        UPDATE user_roles
        SET role_id = (SELECT id FROM roles WHERE name = 'member')
        WHERE role_id = (SELECT id FROM roles WHERE name = 'approver')
        """
    )

    # 3. Pending invitations: repoint approver -> member.
    op.execute(
        """
        UPDATE invitations
        SET role_id = (SELECT id FROM roles WHERE name = 'member')
        WHERE role_id = (SELECT id FROM roles WHERE name = 'approver')
        """
    )

    # 4. Drop the approver role and its permission links.
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE role_id = (SELECT id FROM roles WHERE name = 'approver')
        """
    )
    op.execute("DELETE FROM roles WHERE name = 'approver'")


def downgrade() -> None:
    """Recreate the approver role and its permission set (assignments not restored)."""
    op.execute(
        """
        INSERT INTO roles (id, name, display_name, description)
        VALUES (
            gen_random_uuid(),
            'approver',
            'Approver',
            'Read-only reviewer — views projects, sources, modules, features, '
            'and user stories; approve/reject power over user stories is '
            'granted per-project via ProjectMember, not this role''s '
            'permission set'
        )
        ON CONFLICT (name) DO NOTHING
        """
    )
    permission_names = ", ".join(f"'{name}'" for name in _APPROVER_PERMISSIONS)
    op.execute(
        f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        JOIN permissions p ON p.name IN ({permission_names})
        WHERE r.name = 'approver'
        ON CONFLICT DO NOTHING
        """
    )
