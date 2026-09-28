"""Filter values for ``GET /projects/list``'s ``stage`` query parameter.

Distinguishes projects by how far backlog generation has progressed —
derived live from Neo4j Module/Feature/UserStory node existence (see
``Neo4jProjectRepository.get_progress_flags``), not persisted anywhere.

Extending ``str`` lets Pydantic v2 and FastAPI coerce/validate plain query
strings to the correct enum member without extra validators.
"""

from __future__ import annotations

from enum import Enum


class ProjectProgressFilter(str, Enum):
    """Backlog-generation progress buckets for the project list filter."""

    FRESH = "fresh"
    MODULE_FEATURE_ONLY = "module_feature_only"
    USER_STORY_CREATED = "user_story_created"


# Human-readable labels for API responses.
PROJECT_PROGRESS_FILTER_DISPLAY_LABELS: dict[str, str] = {
    ProjectProgressFilter.FRESH.value: "Fresh",
    ProjectProgressFilter.MODULE_FEATURE_ONLY.value: "Module & Feature Only",
    ProjectProgressFilter.USER_STORY_CREATED.value: "User Story Created",
}
