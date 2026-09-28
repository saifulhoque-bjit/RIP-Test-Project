"""Async repository for Project model.

Used by AsyncUnitOfWork for incremental async route/service migration.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres.project_model import Project


class ProjectRepositoryAsync:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_uuid(self, project_id: UUID) -> Project | None:
        stmt = (
            select(Project).where(Project.id == project_id, Project.deleted_at.is_(None)).limit(1)
        )
        return await self._session.scalar(stmt)

    async def get_by_name_and_owner(self, name: str, owner_id: UUID) -> Project | None:
        stmt = (
            select(Project)
            .where(
                Project.name == name,
                Project.owner_id == owner_id,
                Project.deleted_at.is_(None),
            )
            .limit(1)
        )
        return await self._session.scalar(stmt)

    async def get_paginated(
        self,
        skip: int = 0,
        limit: int = 20,
        owner_id: UUID | None = None,
        search: str | None = None,
    ) -> tuple[list[Project], int]:
        total_col = func.count().over().label("total")
        stmt = select(Project, total_col).where(Project.deleted_at.is_(None))

        if owner_id is not None:
            stmt = stmt.where(Project.owner_id == owner_id)
        if search:
            stmt = stmt.where(Project.name.ilike(f"%{search}%"))

        stmt = stmt.order_by(Project.created_at.desc()).offset(skip).limit(limit)
        rows = (await self._session.execute(stmt)).all()
        if not rows:
            return [], 0

        return [row[0] for row in rows], int(rows[0][1])

    async def create(self, project: Project) -> Project:
        self._session.add(project)
        await self._session.flush()
        await self._session.refresh(project)
        return project

    async def update(self, project: Project) -> Project:
        await self._session.flush()
        await self._session.refresh(project)
        return project
