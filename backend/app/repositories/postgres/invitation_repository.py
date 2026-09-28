"""Invitation repository — typed query helpers on top of BaseRepository."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.postgres.invitation_model import Invitation
from app.repositories.postgres.base_repository import BaseRepository


class InvitationRepository(BaseRepository[Invitation]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Invitation)

    def get_by_token_hash(self, token_hash: str) -> Invitation | None:
        """Return the invitation with the given token hash, or None."""
        return self._session.query(Invitation).filter(Invitation.token_hash == token_hash).first()

    def get_pending_by_tenant_and_email(
        self, tenant_id: uuid.UUID, email: str
    ) -> Invitation | None:
        """Return a still-pending invitation for *tenant_id*/*email*, or None."""
        return (
            self._session.query(Invitation)
            .filter(
                Invitation.tenant_id == tenant_id,
                Invitation.email == email,
                Invitation.status == "pending",
            )
            .first()
        )

    def get_by_id_and_tenant(
        self, invitation_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Invitation | None:
        """Return the invitation with *invitation_id* scoped to *tenant_id*, or None."""
        return (
            self._session.query(Invitation)
            .filter(
                Invitation.id == invitation_id,
                Invitation.tenant_id == tenant_id,
            )
            .first()
        )

    def count_by_role_id(self, role_id: uuid.UUID) -> int:
        """Return how many invitations (any status) reference *role_id*."""
        return self._session.query(Invitation).filter(Invitation.role_id == role_id).count()

    def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[Invitation], int]:
        """Return a page of *tenant_id*'s invitations (newest first), plus the total count.

        Filters on *status* when given (e.g. "pending") — otherwise returns
        invitations in every status.
        """
        query = self._session.query(Invitation).filter(Invitation.tenant_id == tenant_id)
        if status is not None:
            query = query.filter(Invitation.status == status)
        total = query.count()
        items = query.order_by(Invitation.created_at.desc()).offset(skip).limit(limit).all()
        return items, total
