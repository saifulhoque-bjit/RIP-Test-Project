"""Unit tests for app.workers.project_tasks (Neo4j sync/delete Celery tasks)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import uuid

import pytest

from app.workers.project_tasks import (
    _cleanup_tap_sync_mappings,
    delete_project_graph,
    sync_project_to_neo4j,
)


class TestSyncProjectToNeo4j:
    def test_success_upserts_node_and_metadata(self):
        repo = MagicMock()
        metadata_repo = MagicMock()

        with (
            patch("app.db.neo4j.get_neo4j_driver", return_value=MagicMock()),
            patch("app.repositories.neo4j.project_repository.ProjectRepository", return_value=repo),
            patch(
                "app.repositories.neo4j.project_metadata_repository.ProjectMetadataRepository",
                return_value=metadata_repo,
            ),
        ):
            project_id = str(uuid.uuid4())
            result = sync_project_to_neo4j.run(project_id, "Alpha", "active")

        assert result == {"status": "ok", "project_id": project_id}
        repo.upsert_project_node.assert_called_once()
        metadata_repo.upsert.assert_called_once()

    def test_failure_retries(self):
        repo = MagicMock()
        repo.upsert_project_node.side_effect = RuntimeError("neo4j down")

        with (
            patch("app.db.neo4j.get_neo4j_driver", return_value=MagicMock()),
            patch("app.repositories.neo4j.project_repository.ProjectRepository", return_value=repo),
            patch.object(
                sync_project_to_neo4j, "retry", side_effect=RuntimeError("retry-signal")
            ) as mock_retry,
        ):
            with pytest.raises(RuntimeError, match="retry-signal"):
                sync_project_to_neo4j.run(str(uuid.uuid4()), "Alpha", "active")

        mock_retry.assert_called_once()


class TestDeleteProjectGraph:
    def test_success_deletes_graph_and_cleans_up_tap_mappings(self):
        repo = MagicMock()
        repo.get_all_entity_ids_sync.return_value = [str(uuid.uuid4())]

        with (
            patch("app.db.neo4j.get_neo4j_driver", return_value=MagicMock()),
            patch("app.repositories.neo4j.project_repository.ProjectRepository", return_value=repo),
            patch("app.workers.project_tasks._cleanup_tap_sync_mappings") as mock_cleanup,
        ):
            project_id = str(uuid.uuid4())
            result = delete_project_graph.run(project_id)

        assert result == {"status": "ok", "project_id": project_id}
        repo.delete_project_graph_sync.assert_called_once_with(project_id)
        mock_cleanup.assert_called_once()

    def test_failure_retries(self):
        repo = MagicMock()
        repo.get_all_entity_ids_sync.side_effect = RuntimeError("neo4j down")

        with (
            patch("app.db.neo4j.get_neo4j_driver", return_value=MagicMock()),
            patch("app.repositories.neo4j.project_repository.ProjectRepository", return_value=repo),
            patch.object(
                delete_project_graph, "retry", side_effect=RuntimeError("retry-signal")
            ) as mock_retry,
        ):
            with pytest.raises(RuntimeError, match="retry-signal"):
                delete_project_graph.run(str(uuid.uuid4()))

        mock_retry.assert_called_once()


class TestCleanupTapSyncMappings:
    def test_empty_entity_ids_short_circuits(self):
        with patch("app.db.unit_of_work.UnitOfWork") as mock_uow_cls:
            _cleanup_tap_sync_mappings([], "proj-1")

        mock_uow_cls.assert_not_called()

    def test_deletes_mappings_for_given_entity_ids(self):
        uow = MagicMock()
        uow.tap_sync_mappings.delete_by_rip_entity_ids.return_value = 2
        cm = MagicMock()
        cm.__enter__.return_value = uow
        cm.__exit__.return_value = None

        with patch("app.db.unit_of_work.UnitOfWork", return_value=cm):
            _cleanup_tap_sync_mappings([str(uuid.uuid4())], "proj-1")

        uow.tap_sync_mappings.delete_by_rip_entity_ids.assert_called_once()

    def test_swallows_exception(self):
        with patch("app.db.unit_of_work.UnitOfWork", side_effect=RuntimeError("db down")):
            _cleanup_tap_sync_mappings([str(uuid.uuid4())], "proj-1")  # must not raise
