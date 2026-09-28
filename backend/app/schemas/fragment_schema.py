"""API schemas for fragment creation and retrieval."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FragmentBBoxCoordinates(BaseModel):
    """Coordinate payload nested under each bbox item."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(..., description="X coordinate")
    y: float = Field(..., description="Y coordinate")
    w: float = Field(..., description="Width")
    h: float = Field(..., description="Height")


class FragmentBBoxItem(BaseModel):
    """Bounding-box metadata for a fragment on a page."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(..., ge=1)
    bbox: FragmentBBoxCoordinates
    confidence: float | None = Field(None, ge=0, le=1, description="Confidence score")


class FragmentResponse(BaseModel):
    """Normalized fragment returned by the fragment API."""

    id: str
    source_id: UUID
    frag_type: str
    source_type: str | None = None
    content: str
    content_hash: str
    bbox: list[FragmentBBoxItem]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CreateSingleFragmentResponse(BaseModel):
    """Response returned after creating a single fragment."""

    source_id: UUID
    fragment: FragmentResponse


class ListFragmentsResponse(BaseModel):
    """Response returned when listing fragments for a source."""

    source_id: UUID
    total_count: int
    fragments: list[FragmentResponse]


class ListFragmentsByProjectResponse(BaseModel):
    """Response returned when listing all fragments for a project."""

    project_id: UUID
    total: int
    skip: int
    limit: int
    items: list[FragmentResponse]


class UpdateFragmentBBoxRequest(BaseModel):
    """Request body for updating only the bounding-box of a fragment."""

    model_config = ConfigDict(extra="forbid")

    bbox: list[FragmentBBoxItem] = Field(..., min_length=1)


class UpdateFragmentBBoxResponse(BaseModel):
    """Response returned after a bbox-only update."""

    fragment_id: str
    source_id: UUID
    bbox: list[FragmentBBoxItem]
    created_at: datetime | None = None
    updated_at: datetime | None = None
