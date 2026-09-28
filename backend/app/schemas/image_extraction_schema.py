import json

from pydantic import BaseModel, Field, field_validator


class OcrGroup(BaseModel):
    region: str
    text: list[str]

    @field_validator("text", mode="before")
    @classmethod
    def _coerce(cls, v):
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return v


class ImageExtractionOutput(BaseModel):
    image_type: str
    description: str
    ocr_groups: list[OcrGroup]
    confidence: str  # "high" | "medium" | "low"
    legibility_notes: str = Field(default="")

    @field_validator("ocr_groups", mode="before")
    @classmethod
    def _coerce_groups(cls, v):
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return v
