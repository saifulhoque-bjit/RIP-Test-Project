"""Canonical project lifecycle statuses.

This enum is the single source of truth for ``project.status`` persisted in
PostgreSQL.  All layers — ORM model, Pydantic schema, and service — import
from here so the valid set of values is defined exactly once.

Extending ``str`` lets Pydantic v2 coerce plain strings (e.g. from ORM rows)
to the correct enum member without extra validators.

Usage::

    from app.core.enums.project_status import ProjectStatus

    project.status = ProjectStatus.ACTIVE.value          # model layer
    status: ProjectStatus = ProjectStatus.ARCHIVED        # service / schema layer
"""

from __future__ import annotations

from enum import Enum


class ProjectStatus(str, Enum):
    """Lifecycle states for a :class:`~app.models.postgres.project_model.Project`."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"
    DELETED = "deleted"


# Human-readable labels for API responses.
PROJECT_STATUS_DISPLAY_LABELS: dict[str, str] = {
    ProjectStatus.ACTIVE.value: "Active",
    ProjectStatus.INACTIVE.value: "Inactive",
    ProjectStatus.ARCHIVED.value: "Archived",
    ProjectStatus.DELETED.value: "Deleted",
}
