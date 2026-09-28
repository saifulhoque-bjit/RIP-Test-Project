"""Client-supplied source categorization.

This enum is the single source of truth for source.source_type persisted in
PostgreSQL and mirrored onto the Neo4j :Source node.
"""

from __future__ import annotations

from enum import Enum


class SourceType(str, Enum):
    RFP = "rfp"
    ADDITIONAL_RFP = "additional_rfp"
    SOURCE_CODE = "source_code"
    MEETING_NOTES = "meeting_notes"
    REQUIREMENT_UPDATE = "requirement_update"


SOURCE_TYPE_VALUES: tuple[str, ...] = tuple(source_type.value for source_type in SourceType)

# Human-readable labels for API responses.
SOURCE_TYPE_DISPLAY_LABELS: dict[str, str] = {
    SourceType.RFP.value: "RFP",
    SourceType.ADDITIONAL_RFP.value: "Additional RFP",
    SourceType.SOURCE_CODE.value: "Source code",
    SourceType.MEETING_NOTES.value: "Meeting notes",
    SourceType.REQUIREMENT_UPDATE.value: "Requirement update",
}
