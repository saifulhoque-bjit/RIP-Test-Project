from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentChunksResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_id: UUID
    chunks: list[dict]  # or list[ChunkItem] if you want stricter typing later
