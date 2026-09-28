"""Unit tests for user story routes."""

from __future__ import annotations

from unittest.mock import ANY, AsyncMock, MagicMock, patch
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps import get_current_db_user, get_uow
from app.routes.v1.user_stories import router as user_stories_router
from app.schemas.user_story_schema import (
    ProjectUserStorySummaryResponse,
    SourceFileInfo,
    SyncCandidateTreeResponse,
    UserStoryDetailResponse,
    UserStoryListItemResponse,
    UserStoryListResponse,
    UserStoryRegenerationQueuedResponse,
    UserStoryStatusChangedResponse,
    UserStoryTreeResponse,
)
from tests.conftest import make_user


def _build_user_story_list_item(user_story_id: str = "req_1") -> UserStoryListItemResponse:
    return UserStoryListItemResponse(
        id=user_story_id,
        user_story_code="ARCH-001",
        title="Core Architecture",
        description="Some description",
        consensus=9.8,
        status="approved",
        version=1,
        feature_id=None,
        source_file_count=1,
    )


def _make_uow() -> MagicMock:
    """UoW stand-in whose mocked project always passes the owner bypass in
    ``ProjectService.assert_project_access`` (see app/deps.py's
    ``require_project_access``, now a real dependency on every project-scoped
    route) regardless of which user id is mocked as the caller."""
    uow = MagicMock()
    uow.projects.get_by_uuid.return_value = MagicMock(owner_id=ANY, tenant_id=ANY)
    return uow


def _make_app() -> tuple[FastAPI, uuid.UUID]:
    app = FastAPI()
    app.include_router(user_stories_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    app.dependency_overrides[get_uow] = _make_uow
    source_id = uuid.uuid4()
    return app, source_id


_VALID_BODY = {
    "user_story_code": "ARCH-001",
    "title": "Core Architecture",
    "description": "Some description",
    "consensus": 9.8,
    "status": "approved",
    "version": "1.0",
}


def test_list_user_stories_by_project_success() -> None:
    app, _ = _make_app()
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.user_stories.UserStoryService.list_user_stories_by_project",
        new=AsyncMock(
            return_value=UserStoryListResponse(
                total=1,
                are_all_approved=False,
                skip=0,
                limit=20,
                items=[_build_user_story_list_item()],
            )
        ),
    ) as mock_list:
        client = TestClient(app)
        response = client.get(f"/projects/{project_id}/user-stories")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["skip"] == 0
    assert data["limit"] == 20
    assert data["items"][0]["user_story_code"] == "ARCH-001"
    mock_list.assert_awaited_once()


def test_get_user_stories_tree_forwards_source_ingestion_id_filter() -> None:
    app, _ = _make_app()
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.user_stories.UserStoryService.get_user_stories_tree",
        new=AsyncMock(
            return_value=UserStoryTreeResponse(
                are_all_approved_for_us=False,
                are_all_approved_for_mod=False,
                are_all_approved_for_fea=False,
                items=[],
            )
        ),
    ) as mock_get_tree:
        client = TestClient(app)
        response = client.get(
            f"/projects/{project_id}/user-stories/list/tree",
            params={"source_ingestion_id": "ingestion-1"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["items"] == []
    mock_get_tree.assert_awaited_once_with(
        project_id=project_id, uow=ANY, source_ingestion_id="ingestion-1"
    )


def test_get_user_story_sync_candidates_success() -> None:
    app, _ = _make_app()
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.user_stories.UserStoryService.get_sync_candidate_tree",
        new=AsyncMock(
            return_value=SyncCandidateTreeResponse(
                sync_target="jira",
                total_count=0,
                items=[],
            )
        ),
    ) as mock_get_candidates:
        client = TestClient(app)
        response = client.get(
            f"/projects/{project_id}/user-stories/sync-candidates",
            params={"sync_target": "jira"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["sync_target"] == "jira"
    assert data["total_count"] == 0
    mock_get_candidates.assert_awaited_once_with(
        project_id=project_id, sync_target="jira", uow=ANY
    )


def test_get_user_story_sync_candidates_rejects_invalid_target() -> None:
    app, _ = _make_app()
    project_id = uuid.uuid4()

    client = TestClient(app)
    response = client.get(
        f"/projects/{project_id}/user-stories/sync-candidates",
        params={"sync_target": "not-a-target"},
    )

    assert response.status_code == 422


def test_get_user_story_sync_candidates_requires_sync_target() -> None:
    app, _ = _make_app()
    project_id = uuid.uuid4()

    client = TestClient(app)
    response = client.get(f"/projects/{project_id}/user-stories/sync-candidates")

    assert response.status_code == 422


def test_get_user_stories_tree_without_filter_defaults_to_none() -> None:
    app, _ = _make_app()
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.user_stories.UserStoryService.get_user_stories_tree",
        new=AsyncMock(
            return_value=UserStoryTreeResponse(
                are_all_approved_for_us=False,
                are_all_approved_for_mod=False,
                are_all_approved_for_fea=False,
                items=[],
            )
        ),
    ) as mock_get_tree:
        client = TestClient(app)
        response = client.get(f"/projects/{project_id}/user-stories/list/tree")

    assert response.status_code == 200
    mock_get_tree.assert_awaited_once_with(project_id=project_id, uow=ANY, source_ingestion_id=None)


def test_get_user_stories_tree_includes_source_ingestion_id_in_response() -> None:
    app, _ = _make_app()
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.user_stories.UserStoryService.get_user_stories_tree",
        new=AsyncMock(
            return_value=UserStoryTreeResponse(
                are_all_approved_for_us=False,
                are_all_approved_for_mod=False,
                are_all_approved_for_fea=False,
                items=[
                    {
                        "id": "module_1",
                        "mod_code": "MOD-001",
                        "name": "Module 1",
                        "source_ingestion_id": "ingestion-module",
                        "children": [
                            {
                                "id": "feature_1",
                                "fea_code": "FEA-001",
                                "name": "Feature 1",
                                "source_ingestion_id": "ingestion-feature",
                                "children": [
                                    {
                                        "id": "req_1",
                                        "user_story_code": "REQ-001",
                                        "name": "Story 1",
                                        "status": "ready",
                                        "source_ingestion_id": "ingestion-story",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            )
        ),
    ):
        client = TestClient(app)
        response = client.get(f"/projects/{project_id}/user-stories/list/tree")

    assert response.status_code == 200
    module = response.json()["data"]["items"][0]
    feature = module["children"][0]
    story = feature["children"][0]
    assert module["source_ingestion_id"] == "ingestion-module"
    assert feature["source_ingestion_id"] == "ingestion-feature"
    assert story["source_ingestion_id"] == "ingestion-story"


def test_get_project_user_story_summary_success() -> None:
    app, _ = _make_app()
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.user_stories.UserStoryService.get_project_user_story_summary",
        new=AsyncMock(
            return_value=ProjectUserStorySummaryResponse(
                project_id=project_id,
                total_user_stories=10,
                ready_count=3,
                needs_edit_count=1,
                failed_count=2,
                approved_count=4,
                total_modules=4,
                total_features=12,
            )
        ),
    ) as mock_summary:
        client = TestClient(app)
        response = client.get(f"/projects/{project_id}/user-stories/summary")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total_user_stories"] == 10
    assert data["ready_count"] == 3
    assert data["needs_edit_count"] == 1
    assert data["failed_count"] == 2
    assert data["approved_count"] == 4
    assert data["total_modules"] == 4
    assert data["total_features"] == 12
    assert data["jira_sync_count"] == 0
    assert data["tap_sync_count"] == 0
    mock_summary.assert_awaited_once()


def test_change_status_success() -> None:
    app, _ = _make_app()

    with (
        patch(
            "app.routes.v1.user_stories.UserStoryRepository.get_project_id_by_user_story_id",
            new=AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "app.routes.v1.user_stories.UserStoryService.change_status",
            new=AsyncMock(
                return_value=UserStoryStatusChangedResponse(
                    id="req_1",
                    status="approved",
                )
            ),
        ) as mock_change,
    ):
        client = TestClient(app)
        response = client.patch(
            "/user-stories/req_1/status",
            json={"status": "approved"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["id"] == "req_1"
    assert response.json()["data"]["status"] == "approved"
    mock_change.assert_awaited_once()


def test_change_status_invalid_value() -> None:
    app, _ = _make_app()
    client = TestClient(app)

    response = client.patch(
        "/user-stories/req_1/status",
        json={"status": "not-a-valid-status"},
    )

    assert response.status_code == 422


def test_get_user_story_by_project_success() -> None:
    app, source_id = _make_app()
    project_id = uuid.uuid4()

    detail_response = UserStoryDetailResponse(
        id="req_1",
        user_story_code="ARCH-001",
        title="Core Architecture",
        description="Some description",
        consensus=9.8,
        status="approved",
        version=1,
        feature_id=None,
        source_file_count=1,
        source_files=[
            SourceFileInfo(
                id=str(source_id),
                name="SourceFile1.pdf",
                type="pdf",
                storage_key="sources/file.pdf",
                storage_url="https://s3.example.com/presigned",
                created_at=None,
                updated_at=None,
            )
        ],
    )

    with patch(
        "app.routes.v1.user_stories.UserStoryService.get_user_story_detail_by_project",
        new=AsyncMock(return_value=detail_response),
    ) as mock_get:
        client = TestClient(app)
        response = client.get(f"/projects/{project_id}/user-stories/req_1")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["id"] == "req_1"
    assert data["user_story_code"] == "ARCH-001"
    assert len(data["source_files"]) == 1
    assert data["source_files"][0]["name"] == "SourceFile1.pdf"
    assert data["source_files"][0]["type"] == "pdf"
    mock_get.assert_awaited_once()


def test_regenerate_user_stories_success() -> None:
    app, _ = _make_app()
    project_id = uuid.uuid4()
    source_id = uuid.uuid4()

    with patch(
        "app.routes.v1.user_stories.UserStoryService.enqueue_user_story_regeneration",
        new=AsyncMock(
            return_value=UserStoryRegenerationQueuedResponse(
                task_id="task-123",
                project_id=project_id,
                source_ids=[source_id],
                status="queued",
            )
        ),
    ) as mock_enqueue:
        client = TestClient(app)
        response = client.post(
            f"/projects/{project_id}/user-stories/regenerate",
            json={"feedback": "Please refine acceptance criteria"},
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["task_id"] == "task-123"
    assert data["project_id"] == str(project_id)
    assert data["status"] == "queued"
    mock_enqueue.assert_awaited_once()


def test_regenerate_user_stories_success_without_body() -> None:
    app, _ = _make_app()
    project_id = uuid.uuid4()
    source_id = uuid.uuid4()

    with patch(
        "app.routes.v1.user_stories.UserStoryService.enqueue_user_story_regeneration",
        new=AsyncMock(
            return_value=UserStoryRegenerationQueuedResponse(
                task_id="task-124",
                project_id=project_id,
                source_ids=[source_id],
                status="queued",
            )
        ),
    ) as mock_enqueue:
        client = TestClient(app)
        response = client.post(f"/projects/{project_id}/user-stories/regenerate")

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["task_id"] == "task-124"
    assert data["project_id"] == str(project_id)
    assert data["status"] == "queued"
    mock_enqueue.assert_awaited_once()
    assert mock_enqueue.await_args.kwargs["project_id"] == project_id
    assert mock_enqueue.await_args.kwargs["feedback"] is None


def test_delete_user_stories_by_project_success() -> None:
    app, _ = _make_app()
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.user_stories.UserStoryService.delete_user_stories_for_project",
        new=AsyncMock(return_value=7),
    ) as mock_delete:
        client = TestClient(app)
        response = client.delete(f"/projects/{project_id}/user-stories")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["project_id"] == str(project_id)
    assert data["deleted_count"] == 7
    mock_delete.assert_awaited_once()


def test_delete_user_story_by_id_forwards_reason() -> None:
    app, _ = _make_app()
    project_id = uuid.uuid4()
    outcome = {"is_current": False, "del_reason": "No longer needed", "deleted_at": None}

    with patch(
        "app.routes.v1.user_stories.UserStoryService.delete_user_story",
        new=AsyncMock(return_value=outcome),
    ) as mock_delete:
        client = TestClient(app)
        response = client.request(
            "DELETE",
            f"/projects/{project_id}/user-stories/req_1",
            json={"reason": "No longer needed"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["user_story_id"] == "req_1"
    assert data["is_current"] is False
    assert data["del_reason"] == "No longer needed"
    mock_delete.assert_awaited_once_with(
        project_id=project_id,
        user_story_id="req_1",
        uow=ANY,
        reason="No longer needed",
    )


def test_delete_user_story_by_id_without_body_forwards_none_reason() -> None:
    """No body at all is accepted — ``reason`` is only required by the service
    when the target story is 'approved'; the route itself never enforces it."""
    app, _ = _make_app()
    project_id = uuid.uuid4()
    outcome = {"is_current": True, "del_reason": None, "deleted_at": None}

    with patch(
        "app.routes.v1.user_stories.UserStoryService.delete_user_story",
        new=AsyncMock(return_value=outcome),
    ) as mock_delete:
        client = TestClient(app)
        response = client.request("DELETE", f"/projects/{project_id}/user-stories/req_1")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["is_current"] is True
    assert data["del_reason"] is None
    mock_delete.assert_awaited_once_with(
        project_id=project_id,
        user_story_id="req_1",
        uow=ANY,
        reason=None,
    )
