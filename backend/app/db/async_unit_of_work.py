"""Async Unit of Work for incremental SQLAlchemy async adoption."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.repositories.postgres.project_repository_async import ProjectRepositoryAsync


class AsyncUnitOfWork:
    def __init__(self) -> None:
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncUnitOfWork:
        if AsyncSessionLocal is None:
            raise RuntimeError(
                "Async SQLAlchemy session is unavailable. Install async driver dependencies (e.g. asyncpg)."
            )
        self._session = AsyncSessionLocal()
        self.projects = ProjectRepositoryAsync(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()
        await self.close()

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("AsyncUnitOfWork session is not open")
        return self._session

    def add(self, entity: object) -> None:
        self.session.add(entity)

    async def flush(self) -> None:
        await self.session.flush()

    async def refresh(self, entity: object) -> None:
        await self.session.refresh(entity)

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
