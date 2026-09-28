"""Structural sub-type protocols (PEP 544) for cross-layer dependency inversion.

Defining narrow Protocol interfaces here lets service classes depend on
*behaviours* rather than concrete repository or infrastructure classes,
making them easier to test and swap without touching business logic.

Usage
─────
    class MyService:
        def __init__(self, graph_repo: IProjectGraphRepository) -> None:
            self._graph = graph_repo

In tests, any object that satisfies the structural interface works as a
drop-in without inheriting from the concrete class.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from app.repositories.neo4j.project_repository import ProjectProgressFlags, UserStoryCounts


@runtime_checkable
class IProjectGraphRepository(Protocol):
    """Minimum graph-store interface required by :class:`~app.services.project_service.ProjectService`.

    The concrete implementation is
    :class:`~app.repositories.neo4j.project_repository.Neo4jProjectRepository`.
    Any object that provides these three methods satisfies the protocol.
    """

    def upsert_project_node(self, node: object) -> None:
        """Create or update the Project node in the graph store."""
        ...

    def delete_project_graph_sync(self, project_id: str) -> int:
        """Delete all graph nodes / relationships for *project_id*.

        Returns the number of nodes deleted (used for logging / assertions).
        """
        ...

    def get_user_story_counts(self, project_ids: list[UUID]) -> dict[UUID, UserStoryCounts]:
        """Return a mapping of project_id → (total, approved) user story counts."""
        ...

    def get_progress_flags(self, project_ids: list[UUID]) -> dict[UUID, ProjectProgressFlags]:
        """Return a mapping of project_id → (has_module_feature, has_user_story) flags."""
        ...

    def get_global_module_feature_story_counts(self) -> dict:
        """Return global counts of modules, features, and user stories across all projects."""
        ...

    def get_module_feature_story_counts_for_projects(self, project_ids: list[UUID]) -> dict:
        """Return counts of modules, features, and user stories restricted to *project_ids*."""
        ...


@runtime_checkable
class IProjectEventPublisher(Protocol):
    """Minimum async-event interface required by :class:`~app.services.project_service.ProjectService`.

    The concrete implementation is
    :class:`~app.messaging.project_publisher.CeleryProjectEventPublisher`.
    Any object providing these two methods satisfies the protocol.
    """

    def project_upserted(self, project_id: str, name: str, status: str) -> None:
        """Enqueue a task to sync the project node to Neo4j after a create/update."""
        ...

    def project_deleted(self, project_id: str) -> None:
        """Enqueue a task to delete all graph nodes for *project_id*."""
        ...
