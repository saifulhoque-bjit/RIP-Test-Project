"""Layout shape of an analyzed source-code upload.

This enum is the single source of truth for
``source_ingestions.source_layout_type`` persisted in PostgreSQL. Its values
are exactly what
``PipelineOrchestrator.configure_project(source_layout_type=...)`` expects —
they drive whether the source-code pipeline flattens the codebase and how
Stage 2.5 clusters it. Only meaningful when the owning
``SourceIngestion.source_type`` is ``source_code`` — see
``SourceIngestionRepository.create_ingestion``, which drops this field for
any other source type.
"""

from __future__ import annotations

from enum import Enum


class SourceLayoutType(str, Enum):
    MODULAR = "modular"
    FLAT = "flat"
    AUTO = "auto"


# Human-readable label for each SourceLayoutType value — e.g. for API docs /
# frontend display. Keyed by the raw value, not the enum member, so callers
# holding just the persisted string can look up a label without importing
# the enum.
SOURCE_LAYOUT_TYPE_LABELS: dict[str, str] = {
    SourceLayoutType.MODULAR.value: "Modular",
    SourceLayoutType.FLAT.value: "Non Modular",
    SourceLayoutType.AUTO.value: "Unknown",
}
