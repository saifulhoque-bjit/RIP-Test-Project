"""Tests for async SQLAlchemy scaffolding.

These tests validate graceful behavior when async DB extras are not installed
and basic AsyncUnitOfWork contract behavior.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_async_uow_raises_when_async_session_unavailable(monkeypatch) -> None:
    from app.db.async_unit_of_work import AsyncUnitOfWork

    monkeypatch.setattr("app.db.async_unit_of_work.AsyncSessionLocal", None)

    with pytest.raises(RuntimeError, match="Async SQLAlchemy session is unavailable"):
        async with AsyncUnitOfWork() as uow:
            _ = uow
