"""Pagination query-parameter dependency.

Inject with ``Depends(PaginationParams)`` to expose ``?skip`` and ``?limit``.
"""

from __future__ import annotations

from fastapi import Query

from app.core.constants import DEFAULT_PAGE_LIMIT, DEFAULT_PAGE_SKIP, MAX_PAGE_LIMIT


class PaginationParams:
    def __init__(
        self,
        skip: int = Query(default=DEFAULT_PAGE_SKIP, ge=0),
        limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    ) -> None:
        self.skip = skip
        self.limit = limit
