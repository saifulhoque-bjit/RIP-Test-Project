"""Generic BaseRepository providing CRUD operations over a SQLAlchemy model.

Rules:
- Never call ``session.commit()`` here — that belongs to the Unit of Work.
- All public methods accept/return typed model instances.
"""

from __future__ import annotations

from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import Session

from app.db.base import Base

T = TypeVar("T", bound=Base)


class BaseRepository(Generic[T]):
    def __init__(self, session: Session, model: type[T]) -> None:
        self._session = session
        self._model = model

    # ── Read ───────────────────────────────────────────────────────────────

    def get(self, record_id: object) -> T | None:
        """Return a single record by primary key, or None if not found."""
        return self._session.get(self._model, record_id)

    def get_all(self) -> list[T]:
        """Return all records for this model."""
        return self._session.query(self._model).all()

    def get_paginated(self, skip: int = 0, limit: int = 20) -> tuple[list[T], int]:
        """Return a page of records plus the total count."""
        query = self._session.query(self._model)
        total = query.count()
        items = query.offset(skip).limit(limit).all()
        return items, total

    # ── Write ──────────────────────────────────────────────────────────────

    def add(self, entity: T) -> T:
        """Stage *entity* for insertion (does not flush or commit)."""
        self._session.add(entity)
        return entity

    def delete(self, entity: T) -> None:
        """Mark *entity* for deletion (does not flush or commit)."""
        self._session.delete(entity)

    def delete_by_project_id(self, project_id: UUID) -> int:
        """Hard-delete every row of this model for *project_id*.

        Only usable on models that carry a ``project_id`` column — raises
        ``AttributeError`` at call time otherwise. Used by project deletion
        to purge every project-scoped table that doesn't need bespoke
        deletion logic (e.g. cascading a related table first).

        Returns:
            The number of rows deleted.
        """
        stmt = sa_delete(self._model).where(self._model.project_id == project_id)
        result = self._session.execute(stmt)
        return result.rowcount
