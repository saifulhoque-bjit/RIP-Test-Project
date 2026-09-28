"""Internal domain models for fragment workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class FragmentBBoxCoordinatesModel:
    x: float
    y: float
    w: float
    h: float


@dataclass(slots=True)
class FragmentBBoxModel:
    page: int
    bbox: FragmentBBoxCoordinatesModel
    confidence: float | None = None


@dataclass(slots=True)
class FragmentModel:
    id: str
    source_id: UUID
    frag_type: str
    content: str
    bbox: list[FragmentBBoxModel]
    content_hash: str | None = None
    source_type: str | None = None
    # Zero-based index of this fragment in the parsed document. Persisted so
    # reads can return fragments in document order — fragment ids are uuid5
    # hashes, so ordering by id shuffles the document.
    position_index: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
