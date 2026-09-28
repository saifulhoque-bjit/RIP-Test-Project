"""Canonical project type values.

This enum is the single source of truth for ``project.project_type`` persisted
in PostgreSQL.  All layers — ORM model, Pydantic schema, and service — import
from here so the valid set of values is defined exactly once.

Extending ``str`` lets Pydantic v2 coerce plain strings (e.g. from ORM rows)
to the correct enum member without extra validators.

Usage::

    from app.core.enums.project_type import ProjectType

    project.project_type = ProjectType.RFP.value          # model layer
    project_type: ProjectType = ProjectType.SOURCE_CODE    # service / schema layer
"""

from __future__ import annotations

from enum import Enum


class ProjectType(str, Enum):
    """Supported input document categories for a :class:`~app.models.postgres.project_model.Project`."""

    RFP = "rfp"
    IMAGE = "image"
    SOURCE_CODE = "source_code"


# Human-readable labels for API responses.
PROJECT_TYPE_DISPLAY_LABELS: dict[str, str] = {
    ProjectType.RFP.value: "RFP",
    ProjectType.IMAGE.value: "Image",
    ProjectType.SOURCE_CODE.value: "Source code",
}
