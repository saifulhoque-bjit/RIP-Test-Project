"""Role repository — typed query helpers on top of BaseRepository."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.postgres.role_model import Role
from app.repositories.postgres.base_repository import BaseRepository


class RoleRepository(BaseRepository[Role]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Role)

    def get_by_name(self, name: str) -> Role | None:
        """Return the role with the given name, or None."""
        return self._session.query(Role).filter(Role.name == name).first()
