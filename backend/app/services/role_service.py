"""Dynamic role management (super_admin only).

Roles are a named, freely-editable set of permissions drawn from the fixed
code-enforced permission catalogue (seeded in ``app/db/seed_db.py``). The 3
built-in roles (``super_admin``, ``admin``, ``member``) are hardcoded
throughout the authorization layer (``ROLE_ADMIN`` etc. in
``app/core/constants.py``) — renaming or deleting them would silently break
access control, so those two operations are blocked; their description and
permission set may still be updated like any custom role.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.core.constants import ROLE_ADMIN, ROLE_MEMBER, ROLE_SUPER_ADMIN
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.messages import (
    MSG_PERMISSION_NOT_FOUND,
    MSG_ROLE_BUILTIN_IMMUTABLE,
    MSG_ROLE_IN_USE,
    MSG_ROLE_NAME_CONFLICT,
    MSG_ROLE_NOT_FOUND,
)
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.role_model import Role

_BUILTIN_ROLE_NAMES = frozenset({ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_MEMBER})


class RoleService:
    """Orchestrates role CRUD and permission assignment."""

    def _resolve_permissions(self, permission_names: list[str], uow: UnitOfWork) -> list:
        permissions = []
        for name in permission_names:
            permission = uow.permissions.get_by_name(name)
            if permission is None:
                raise ValidationError(MSG_PERMISSION_NOT_FOUND.format(name=name))
            permissions.append(permission)
        return permissions

    def list_roles(self, uow: UnitOfWork) -> list[Role]:
        return uow.roles.get_all()

    def get_role(self, role_id: uuid.UUID, uow: UnitOfWork) -> Role:
        role = uow.roles.get(role_id)
        if role is None:
            raise NotFoundError(MSG_ROLE_NOT_FOUND.format(role_id=role_id))
        return role

    def create_role(
        self,
        name: str,
        display_name: str,
        description: str | None,
        permission_names: list[str],
        uow: UnitOfWork,
    ) -> Role:
        if uow.roles.get_by_name(name) is not None:
            raise ConflictError(MSG_ROLE_NAME_CONFLICT.format(name=name))

        role = Role(name=name, display_name=display_name, description=description)
        role.permissions = self._resolve_permissions(permission_names, uow)
        uow.add(role)
        try:
            uow.flush()
        except IntegrityError as exc:
            # Backstop for two concurrent creates of the same name racing
            # past the pre-check above and both reaching this flush.
            uow.rollback()
            raise ConflictError(MSG_ROLE_NAME_CONFLICT.format(name=name)) from exc
        uow.refresh(role)
        uow.commit()
        return role

    def update_role(
        self,
        role_id: uuid.UUID,
        display_name: str | None,
        description: str | None,
        permission_names: list[str] | None,
        uow: UnitOfWork,
    ) -> Role:
        role = self.get_role(role_id, uow)
        if display_name is not None:
            role.display_name = display_name
        if description is not None:
            role.description = description
        if permission_names is not None:
            role.permissions = self._resolve_permissions(permission_names, uow)
        uow.add(role)
        uow.flush()
        uow.refresh(role)
        uow.commit()
        return role

    def delete_role(self, role_id: uuid.UUID, uow: UnitOfWork) -> None:
        role = self.get_role(role_id, uow)
        if role.name in _BUILTIN_ROLE_NAMES:
            raise ValidationError(MSG_ROLE_BUILTIN_IMMUTABLE.format(name=role.name))
        invitation_count = uow.invitations.count_by_role_id(role_id)
        if invitation_count:
            raise ConflictError(MSG_ROLE_IN_USE.format(name=role.name, count=invitation_count))
        uow.roles.delete(role)
        uow.commit()
