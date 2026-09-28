"""Permission repository — typed query helpers on top of BaseRepository."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.postgres.permission_model import Permission
from app.repositories.postgres.base_repository import BaseRepository


class PermissionRepository(BaseRepository[Permission]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Permission)

    def get_by_name(self, name: str) -> Permission | None:
        """Return the permission with the given name, or None."""
        return self._session.query(Permission).filter(Permission.name == name).first()
