"""Tenant repository — typed query helpers on top of BaseRepository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.postgres.tenant_model import Tenant
from app.repositories.postgres.base_repository import BaseRepository


class TenantRepository(BaseRepository[Tenant]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Tenant)

    def get_by_name(self, name: str) -> Tenant | None:
        """Return the tenant with the given name, or None."""
        return self._session.query(Tenant).filter(Tenant.name == name).first()

    def get_by_contact_email(self, contact_email: str) -> Tenant | None:
        """Return the tenant with the given contact email, or None."""
        return self._session.query(Tenant).filter(Tenant.contact_email == contact_email).first()

    def get_by_code(self, code: str) -> Tenant | None:
        """Return the tenant with the given code, or None."""
        return self._session.query(Tenant).filter(Tenant.code == code).first()

    def increment_project_sequence(self, tenant_id: UUID) -> tuple[str, int] | None:
        """Atomically increment and return this tenant's next project
        sequence number alongside its code, in one round trip.

        A single ``UPDATE ... RETURNING`` holds the row lock for the
        duration of the caller's transaction, making this safe under
        concurrent project creation for the same tenant — no read-then-write
        race window. Returns ``None`` if *tenant_id* doesn't reference an
        existing tenant.
        """
        stmt = (
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(project_sequence=Tenant.project_sequence + 1)
            .returning(Tenant.code, Tenant.project_sequence)
        )
        row = self._session.execute(stmt).first()
        return (row[0], row[1]) if row is not None else None
