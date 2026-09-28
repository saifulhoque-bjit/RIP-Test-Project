"""User repository — typed query helpers on top of BaseRepository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.postgres.user_model import User
from app.repositories.postgres.base_repository import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, User)

    def get_by_cognito_sub(self, sub: str) -> User | None:
        """Return the live (non-removed) user whose Cognito ``sub`` matches, or None.

        Excludes soft-deleted rows (``deleted_at IS NOT NULL``) — a removed
        user's identity is free to be reclaimed by a brand-new registration
        (see the partial unique index added in migration 0021).
        """
        return (
            self._session.query(User)
            .filter(User.cognito_sub == sub, User.deleted_at.is_(None))
            .first()
        )

    def get_by_email(self, email: str) -> User | None:
        """Return the live (non-removed) user with the given email, or None.

        Excludes soft-deleted rows — see :meth:`get_by_cognito_sub`. This is
        what lets ``InvitationService.validate_invite_target`` re-invite an
        email that belonged to a removed user.
        """
        return (
            self._session.query(User).filter(User.email == email, User.deleted_at.is_(None)).first()
        )

    def get_by_ids(self, user_ids: list[UUID]) -> list[User]:
        """Return every user (including soft-deleted ones) matching *user_ids*.

        Unlike :meth:`get_by_cognito_sub`/:meth:`get_by_email`, this does not
        exclude soft-deleted rows — callers use this for historical display
        (e.g. attributing a past activity-log entry to its actor), where a
        since-removed user's name should still resolve rather than vanish.
        """
        if not user_ids:
            return []
        return self._session.query(User).filter(User.id.in_(user_ids)).all()

    def get_paginated(
        self,
        skip: int = 0,
        limit: int = 20,
        tenant_id: UUID | None = None,
    ) -> tuple[list[User], int]:
        """Return a page of live (non-removed) users plus the total matching count.

        Overrides :meth:`BaseRepository.get_paginated` to add tenant
        filtering. ``tenant_id=None`` applies no filter (``super_admin``
        cross-tenant listing, or un-tenanted deployments) — same convention
        as ``ProjectRepository.get_paginated``.
        """
        query = self._session.query(User).filter(User.deleted_at.is_(None))
        if tenant_id is not None:
            query = query.filter(User.tenant_id == tenant_id)
        total = query.count()
        items = query.offset(skip).limit(limit).all()
        return items, total
