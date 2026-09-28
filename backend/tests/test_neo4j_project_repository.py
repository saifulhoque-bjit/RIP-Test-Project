"""Unit tests for the Neo4j ProjectRepository."""

from __future__ import annotations

import uuid

from app.models.neo4j.project_model import ProjectNode
from app.repositories.neo4j.project_repository import (
    ProjectProgressFlags,
    ProjectRepository,
    UserStoryCounts,
)


class _FakeResult:
    def __init__(self, single_result):
        self._single_result = single_result

    def single(self):
        return self._single_result

    def __iter__(self):
        if isinstance(self._single_result, list):
            return iter(self._single_result)
        return iter([])


class _FakeTx:
    def __init__(self, single_results, captured_runs):
        self._single_results = single_results
        self._captured_runs = captured_runs

    def run(self, cypher, **params):
        self._captured_runs.append((cypher, params))
        return _FakeResult(self._single_results.pop(0))


class _FakeSession:
    def __init__(self, single_results, captured_runs):
        self._single_results = single_results
        self._captured_runs = captured_runs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return None

    def execute_read(self, callback):
        return callback(_FakeTx(self._single_results, self._captured_runs))

    def execute_write(self, callback):
        return callback(_FakeTx(self._single_results, self._captured_runs))


class _FakeDriver:
    def __init__(self, single_results, captured_runs=None):
        self._single_results = single_results
        self._captured_runs = captured_runs if captured_runs is not None else []

    def session(self):
        return _FakeSession(self._single_results, self._captured_runs)


class TestUpsertProjectNode:
    def test_upserts_with_correct_params(self):
        captured_runs = []
        driver = _FakeDriver([{"project_id": "p1"}], captured_runs)
        repo = ProjectRepository(driver)
        node = ProjectNode(id=uuid.uuid4(), name="Alpha", status="active")

        repo.upsert_project_node(node)

        cypher, params = captured_runs[0]
        assert "MERGE (p:Project" in cypher
        assert params["project_id"] == str(node.id)
        assert params["project_name"] == "Alpha"


class TestDeleteProjectGraphSync:
    def test_runs_all_delete_statements_and_returns_one(self):
        captured_runs = []
        driver = _FakeDriver(
            [None] * len(ProjectRepository._DELETE_PROJECT_GRAPH_STATEMENTS), captured_runs
        )
        repo = ProjectRepository(driver)

        result = repo.delete_project_graph_sync("proj-1")

        assert result == 1
        assert len(captured_runs) == len(ProjectRepository._DELETE_PROJECT_GRAPH_STATEMENTS)
        for _, params in captured_runs:
            assert params["project_id"] == "proj-1"


class TestGetAllEntityIdsSync:
    def test_returns_non_null_ids(self):
        driver = _FakeDriver([{"ids": ["m1", None, "ft1", "us1"]}])
        repo = ProjectRepository(driver)

        result = repo.get_all_entity_ids_sync("proj-1")

        assert result == ["m1", "ft1", "us1"]

    def test_returns_empty_list_when_no_record(self):
        driver = _FakeDriver([None])
        repo = ProjectRepository(driver)

        assert repo.get_all_entity_ids_sync("proj-1") == []

    def test_returns_empty_list_when_ids_is_none(self):
        driver = _FakeDriver([{"ids": None}])
        repo = ProjectRepository(driver)

        assert repo.get_all_entity_ids_sync("proj-1") == []


class TestDeleteBacklogForProject:
    def test_returns_deletion_counts(self):
        captured_runs = []
        # One record per statement, in _DELETE_BACKLOG_STATEMENTS order:
        # user stories, then features, then modules.
        driver = _FakeDriver([{"deleted": 12}, {"deleted": 5}, {"deleted": 2}], captured_runs)
        repo = ProjectRepository(driver)

        result = repo.delete_backlog_for_project("proj-1")

        assert result == {
            "deleted_modules": 2,
            "deleted_features": 5,
            "deleted_user_stories": 12,
        }
        assert len(captured_runs) == len(ProjectRepository._DELETE_BACKLOG_STATEMENTS)
        for _, params in captured_runs:
            assert params["project_id"] == "proj-1"

    def test_returns_zero_counts_when_no_record(self):
        driver = _FakeDriver([None, None, None])
        repo = ProjectRepository(driver)

        result = repo.delete_backlog_for_project("proj-1")

        assert result == {
            "deleted_modules": 0,
            "deleted_features": 0,
            "deleted_user_stories": 0,
        }

    def test_deletes_children_before_parents(self):
        """UserStory, then Feature, then Module.

        DETACH DELETE-ing a Module first would sever the HAS_FEATURE /
        HAS_USER_STORY edges the later statements traverse to find its
        children, leaving them orphaned in the graph.
        """
        captured_runs = []
        driver = _FakeDriver([{"deleted": 0}] * 3, captured_runs)

        ProjectRepository(driver).delete_backlog_for_project("proj-1")

        keys = [key for key, _ in ProjectRepository._DELETE_BACKLOG_STATEMENTS]
        assert keys == ["deleted_user_stories", "deleted_features", "deleted_modules"]
        # ...and the statements actually ran in that order.
        assert [cypher for cypher, _ in captured_runs] == [
            statement for _, statement in ProjectRepository._DELETE_BACKLOG_STATEMENTS
        ]
        # Each statement collects and deletes the level it is named for.
        for key, cypher in ProjectRepository._DELETE_BACKLOG_STATEMENTS:
            collection = {
                "deleted_user_stories": "user_stories",
                "deleted_features": "features",
                "deleted_modules": "modules",
            }[key]
            assert f"FOREACH (n IN {collection} | DETACH DELETE n)" in cypher
            assert f"RETURN size({collection}) AS deleted" in cypher

    def test_no_statement_combines_independent_optional_branches(self):
        """Regression guard for cancel latency.

        Collapsing the version and SRS-evidence branches of several levels
        into one query makes Neo4j materialize their cartesian product before
        collect(DISTINCT ...) dedups it. Because Module/Feature/UserStory
        *Version* nodes are CREATE-per-change, that product grows with a
        project's edit history and made cancellation take minutes. Each
        statement here must stay scoped to a single backlog level.
        """
        for key, cypher in ProjectRepository._DELETE_BACKLOG_STATEMENTS:
            version_labels = [
                label
                for label in ("ModuleVersion", "FeatureVersion", "UserStoryVersion")
                if label in cypher
            ]
            assert len(version_labels) <= 1, (
                f"{key} spans version snapshots of more than one level "
                f"({version_labels}) — see delete_backlog_for_project's docstring"
            )

    def test_matches_user_stories_by_traversal_not_by_project_id_property(self):
        """:UserStory is MERGEd by ``id`` alone in user_story_repository.

        Its ``project_id`` property is therefore not guaranteed on every
        write path; matching stories by it would silently leave some behind
        on a cancel. They must be reached from :Module by relationship.
        """
        cypher = dict(
            (key, statement)
            for key, statement in ProjectRepository._DELETE_BACKLOG_STATEMENTS
        )["deleted_user_stories"]

        assert "UserStory {project_id" not in cypher
        assert "(:Module {project_id: $project_id})" in cypher
        assert "-[:HAS_USER_STORY]->" in cypher


class TestGetUserStoryCounts:
    def test_empty_project_ids_returns_empty_dict(self):
        driver = _FakeDriver([])
        repo = ProjectRepository(driver)

        assert repo.get_user_story_counts([]) == {}

    def test_returns_counts_defaulting_zero_for_projects_with_no_data(self):
        p1, p2 = uuid.uuid4(), uuid.uuid4()
        driver = _FakeDriver(
            [
                [
                    {
                        "pid": str(p1),
                        "req_count": 5,
                        "approved_count": 2,
                        "jira_synced_count": 1,
                        "tap_synced_count": 0,
                    }
                ]
            ]
        )
        repo = ProjectRepository(driver)

        result = repo.get_user_story_counts([p1, p2])

        assert result[p1] == UserStoryCounts(total=5, approved=2, jira_synced=1, tap_synced=0)
        assert result[p2] == UserStoryCounts(total=0, approved=0)

    def test_swallows_malformed_record(self):
        p1 = uuid.uuid4()
        driver = _FakeDriver([[{"pid": "not-a-uuid", "req_count": 1}]])
        repo = ProjectRepository(driver)

        result = repo.get_user_story_counts([p1])

        assert result[p1] == UserStoryCounts(total=0, approved=0)

    def test_excludes_soft_deleted_user_stories(self):
        p1 = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver([[]], captured_runs)
        repo = ProjectRepository(driver)

        repo.get_user_story_counts([p1])

        cypher, _ = captured_runs[0]
        assert "count(CASE WHEN r IS NULL OR r.deleted_at IS NULL THEN r END) AS req_count" in cypher
        assert "r.status IN ['approved', 'deleted']" in cypher


class TestGetProgressFlags:
    def test_empty_project_ids_returns_empty_dict(self):
        driver = _FakeDriver([])
        repo = ProjectRepository(driver)

        assert repo.get_progress_flags([]) == {}

    def test_defaults_false_for_projects_with_no_data(self):
        p1, p2 = uuid.uuid4(), uuid.uuid4()
        driver = _FakeDriver(
            [[{"pid": str(p1), "module_count": 3, "story_count": 0}]]
        )
        repo = ProjectRepository(driver)

        result = repo.get_progress_flags([p1, p2])

        assert result[p1] == ProjectProgressFlags(has_module_feature=True, has_user_story=False)
        assert result[p2] == ProjectProgressFlags(has_module_feature=False, has_user_story=False)

    def test_has_user_story_true_when_story_count_positive(self):
        p1 = uuid.uuid4()
        driver = _FakeDriver([[{"pid": str(p1), "module_count": 2, "story_count": 4}]])
        repo = ProjectRepository(driver)

        result = repo.get_progress_flags([p1])

        assert result[p1] == ProjectProgressFlags(has_module_feature=True, has_user_story=True)

    def test_swallows_malformed_record(self):
        p1 = uuid.uuid4()
        driver = _FakeDriver([[{"pid": "not-a-uuid", "module_count": 1}]])
        repo = ProjectRepository(driver)

        result = repo.get_progress_flags([p1])

        assert result[p1] == ProjectProgressFlags(has_module_feature=False, has_user_story=False)

    def test_module_check_filters_by_project_id_and_excludes_soft_deleted(self):
        p1 = uuid.uuid4()
        captured_runs = []
        driver = _FakeDriver([[]], captured_runs)
        repo = ProjectRepository(driver)

        repo.get_progress_flags([p1])

        cypher, params = captured_runs[0]
        assert "m.project_id = pid AND m.deleted_at IS NULL" in cypher
        assert "m2.project_id = pid AND s.deleted_at IS NULL" in cypher
        assert params["project_ids"] == [str(p1)]


class TestGetGlobalModuleFeatureStoryCounts:
    def test_returns_counts(self):
        driver = _FakeDriver(
            [
                {
                    "total_modules": 3,
                    "total_features": 9,
                    "total_stories": 20,
                    "approved_stories": 10,
                    "pending_jira_sync_stories": 4,
                    "pending_tap_sync_stories": 6,
                }
            ]
        )
        repo = ProjectRepository(driver)

        result = repo.get_global_module_feature_story_counts()

        assert result["total_modules"] == 3
        assert result["approved_stories"] == 10

    def test_returns_zero_counts_when_no_record(self):
        driver = _FakeDriver([None])
        repo = ProjectRepository(driver)

        result = repo.get_global_module_feature_story_counts()

        assert result["total_modules"] == 0
        assert result["pending_tap_sync_stories"] == 0

    def test_excludes_soft_deleted_modules_features_and_stories(self):
        captured_runs = []
        driver = _FakeDriver([None], captured_runs)
        repo = ProjectRepository(driver)

        repo.get_global_module_feature_story_counts()

        cypher, _ = captured_runs[0]
        assert "OPTIONAL MATCH (m:Module) WHERE m.deleted_at IS NULL " in cypher
        assert "OPTIONAL MATCH (f:Feature) WHERE f.deleted_at IS NULL " in cypher
        assert "OPTIONAL MATCH (s:UserStory) WHERE s.deleted_at IS NULL " in cypher


class TestGetModuleFeatureStoryCountsForProjects:
    def test_empty_project_ids_returns_zero_counts_without_querying(self):
        driver = _FakeDriver([])
        repo = ProjectRepository(driver)

        result = repo.get_module_feature_story_counts_for_projects([])

        assert result["total_modules"] == 0

    def test_returns_counts(self):
        driver = _FakeDriver(
            [
                {
                    "total_modules": 1,
                    "total_features": 2,
                    "total_stories": 3,
                    "approved_stories": 1,
                    "pending_jira_sync_stories": 0,
                    "pending_tap_sync_stories": 1,
                }
            ]
        )
        repo = ProjectRepository(driver)

        result = repo.get_module_feature_story_counts_for_projects([uuid.uuid4()])

        assert result["total_stories"] == 3

    def test_returns_zero_counts_when_no_record(self):
        driver = _FakeDriver([None])
        repo = ProjectRepository(driver)

        result = repo.get_module_feature_story_counts_for_projects([uuid.uuid4()])

        assert result["total_modules"] == 0

    def test_excludes_soft_deleted_modules_features_and_stories(self):
        captured_runs = []
        driver = _FakeDriver([None], captured_runs)
        repo = ProjectRepository(driver)

        repo.get_module_feature_story_counts_for_projects([uuid.uuid4()])

        cypher, _ = captured_runs[0]
        assert "m.project_id IN $project_ids AND m.deleted_at IS NULL " in cypher
        assert "f.project_id IN $project_ids AND f.deleted_at IS NULL " in cypher
        assert "s.project_id IN $project_ids AND s.deleted_at IS NULL " in cypher
