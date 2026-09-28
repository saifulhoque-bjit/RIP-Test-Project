"""Unit tests for module feature routes."""

from __future__ import annotations

from unittest.mock import ANY, AsyncMock, MagicMock, patch
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps import get_current_db_user, get_uow
from app.routes.v1.module_features import router as module_features_router
from app.schemas.module_feature_schema import (
    FeatureResponse,
    ModuleFeatureFeatureSingleResponse,
    ModuleFeatureListResponse,
    ModuleFeatureRegenerationQueuedResponse,
    ModuleFeatureRegenerationRequest,
    ModuleFeatureResponse,
    ModuleFeatureSingleResponse,
    ModuleFeatureStatusChangeResponse,
    ModuleTreeListResponse,
)
from tests.conftest import make_user


def _override_uow() -> MagicMock:
    """Bare UoW stand-in for tests that don't exercise persistence directly.

    ``require_project_access`` (see app/deps.py) now calls
    ``uow.projects.get_by_uuid`` for real on every request — the returned
    project's owner_id/tenant_id use ``ANY`` so the owner bypass in
    ``ProjectService.assert_project_access`` always succeeds regardless of
    the mocked user's id, since these tests aren't exercising authorization.
    """
    uow = MagicMock()
    uow.projects.get_by_uuid.return_value = MagicMock(owner_id=ANY, tenant_id=ANY)
    return uow


def _build_module_response(source_id):
    return ModuleFeatureResponse(
        source_id=source_id,
        id="module_1",
        name="Module 1",
        description="Module description",
        features=[
            {
                "id": "feature_11",
                "name": "Feature 11",
                "description": "Feature description",
            }
        ],
    )


def _build_module_response_v2(project_id):
    return ModuleFeatureResponse(
        project_id=project_id,
        id="module_1",
        name="Module 1",
        description="Module description",
        features=[
            {
                "id": "feature_11",
                "name": "Feature 11",
                "description": "Feature description",
            }
        ],
    )


def test_list_modules_success() -> None:
    app = FastAPI()
    app.include_router(module_features_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    app.dependency_overrides[get_uow] = _override_uow
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.module_features.ModuleFeatureService.list_modules_for_project",
        new=AsyncMock(
            return_value=ModuleFeatureListResponse(
                total=1,
                skip=0,
                limit=20,
                items=[
                    {
                        "id": "module_1",
                        "mod_code": None,
                        "name": "Module 1",
                        "description": None,
                        "status": "ready",
                        "features": [
                            {
                                "id": "feature_11",
                                "fea_code": None,
                                "name": "Feature 11",
                                "description": None,
                                "status": "ready",
                            }
                        ],
                    }
                ],
            )
        ),
    ) as mock_list:
        client = TestClient(app)
        response = client.get(f"/projects/{project_id}/modules")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["skip"] == 0
    assert data["limit"] == 20
    assert data["items"][0]["id"] == "module_1"
    assert "source_id" not in data
    mock_list.assert_awaited_once()


def test_list_modules_tree_forwards_source_ingestion_id_filter() -> None:
    app = FastAPI()
    app.include_router(module_features_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    app.dependency_overrides[get_uow] = _override_uow
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.module_features.ModuleFeatureService.list_modules_tree_for_project",
        new=AsyncMock(return_value=ModuleTreeListResponse(total=0, items=[])),
    ) as mock_list_tree:
        client = TestClient(app)
        response = client.get(
            f"/projects/{project_id}/modules/list",
            params={"source_ingestion_id": "ingestion-1"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["items"] == []
    mock_list_tree.assert_awaited_once_with(
        project_id=project_id, uow=ANY, source_ingestion_id="ingestion-1"
    )


def test_list_modules_tree_without_filter_defaults_to_none() -> None:
    app = FastAPI()
    app.include_router(module_features_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    app.dependency_overrides[get_uow] = _override_uow
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.module_features.ModuleFeatureService.list_modules_tree_for_project",
        new=AsyncMock(return_value=ModuleTreeListResponse(total=0, items=[])),
    ) as mock_list_tree:
        client = TestClient(app)
        response = client.get(f"/projects/{project_id}/modules/list")

    assert response.status_code == 200
    mock_list_tree.assert_awaited_once_with(project_id=project_id, uow=ANY, source_ingestion_id=None)


def test_get_module_success() -> None:
    app = FastAPI()
    app.include_router(module_features_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    app.dependency_overrides[get_uow] = _override_uow
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.module_features.ModuleFeatureService.get_module_by_project",
        new=AsyncMock(
            return_value=ModuleFeatureSingleResponse(
                project_id=project_id,
                module=_build_module_response_v2(project_id),
            )
        ),
    ) as mock_get:
        client = TestClient(app)
        response = client.get(f"/projects/{project_id}/modules/module_1")

    assert response.status_code == 200
    assert response.json()["data"]["module"]["id"] == "module_1"
    mock_get.assert_awaited_once()


def test_get_feature_success() -> None:
    app = FastAPI()
    app.include_router(module_features_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    app.dependency_overrides[get_uow] = _override_uow
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.module_features.ModuleFeatureService.get_feature_by_module",
        new=AsyncMock(
            return_value=ModuleFeatureFeatureSingleResponse(
                project_id=project_id,
                module_id="module_1",
                feature=FeatureResponse(
                    id="feature_1",
                    name="Feature 1",
                    description="Feature description",
                ),
            )
        ),
    ) as mock_get:
        client = TestClient(app)
        response = client.get(f"/projects/{project_id}/modules/module_1/features/feature_1")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["module_id"] == "module_1"
    assert data["feature"]["id"] == "feature_1"
    mock_get.assert_awaited_once()


def test_regenerate_modules_success() -> None:
    app = FastAPI()
    app.include_router(module_features_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    uow = _override_uow()
    app.dependency_overrides[get_uow] = lambda: uow
    project_id = uuid.uuid4()
    source_id = uuid.uuid4()

    with patch(
        "app.routes.v1.module_features.ModuleFeatureService.enqueue_module_feature_regeneration",
        new=AsyncMock(
            return_value=ModuleFeatureRegenerationQueuedResponse(
                task_id="task-123",
                project_id=project_id,
                source_ids=[source_id],
                status="queued",
            )
        ),
    ) as mock_enqueue:
        client = TestClient(app)
        response = client.post(
            f"/projects/{project_id}/modules/regenerate",
            json=ModuleFeatureRegenerationRequest(
                feedback="Refine auth module boundaries"
            ).model_dump(),
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["task_id"] == "task-123"
    assert data["status"] == "queued"
    mock_enqueue.assert_awaited_once_with(
        project_id=project_id,
        feedback="Refine auth module boundaries",
        module_ids=None,
        feature_ids=None,
        uow=uow,
        user_id=mock_enqueue.call_args.kwargs["user_id"],
    )


def test_change_module_feature_status_success() -> None:
    app = FastAPI()
    app.include_router(module_features_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    uow = _override_uow()
    app.dependency_overrides[get_uow] = lambda: uow
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.module_features.ModuleFeatureService.change_module_feature_status_for_project",
        new=AsyncMock(
            return_value=ModuleFeatureStatusChangeResponse(
                status="approved",
            )
        ),
    ) as mock_change_status:
        client = TestClient(app)
        response = client.patch(
            f"/projects/{project_id}/modules/status",
            json={"status": "approved"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "approved"
    # Verify the service method was called with correct project_id and uow
    mock_change_status.assert_awaited_once()
    call_args = mock_change_status.call_args
    assert call_args.kwargs["project_id"] == project_id
    assert call_args.kwargs["uow"] == uow
    # Payload is created by FastAPI from the request, verify it has the right data
    assert call_args.kwargs["payload"].status.value == "approved"


def test_change_module_feature_status_forwards_skip_processing_true() -> None:
    app = FastAPI()
    app.include_router(module_features_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    uow = _override_uow()
    app.dependency_overrides[get_uow] = lambda: uow
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.module_features.ModuleFeatureService.change_module_feature_status_for_project",
        new=AsyncMock(
            return_value=ModuleFeatureStatusChangeResponse(
                status="approved",
            )
        ),
    ) as mock_change_status:
        client = TestClient(app)
        response = client.patch(
            f"/projects/{project_id}/modules/status",
            json={"status": "approved", "skip_processing": True},
        )

    assert response.status_code == 200
    call_args = mock_change_status.call_args
    assert call_args.kwargs["payload"].status.value == "approved"
    assert call_args.kwargs["payload"].skip_processing is True


def test_delete_module_forwards_reason() -> None:
    app = FastAPI()
    app.include_router(module_features_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    uow = _override_uow()
    app.dependency_overrides[get_uow] = lambda: uow
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.module_features.ModuleFeatureService.delete_module",
        new=AsyncMock(
            return_value={
                "is_deleted": True,
                "deletion_reason": "No longer needed",
                "deleted_at": None,
            }
        ),
    ) as mock_delete:
        client = TestClient(app)
        response = client.request(
            "DELETE",
            f"/projects/{project_id}/modules/module_1",
            json={"reason": "No longer needed"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["is_deleted"] is True
    assert data["deletion_reason"] == "No longer needed"
    mock_delete.assert_awaited_once_with(
        project_id=project_id, module_id="module_1", uow=ANY, reason="No longer needed"
    )


def test_delete_module_without_body_forwards_none_reason() -> None:
    app = FastAPI()
    app.include_router(module_features_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    uow = _override_uow()
    app.dependency_overrides[get_uow] = lambda: uow
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.module_features.ModuleFeatureService.delete_module",
        new=AsyncMock(
            return_value={"is_deleted": True, "deletion_reason": None, "deleted_at": None}
        ),
    ) as mock_delete:
        client = TestClient(app)
        response = client.request("DELETE", f"/projects/{project_id}/modules/module_1")

    assert response.status_code == 200
    mock_delete.assert_awaited_once_with(
        project_id=project_id, module_id="module_1", uow=ANY, reason=None
    )


def test_delete_feature_forwards_reason() -> None:
    app = FastAPI()
    app.include_router(module_features_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    uow = _override_uow()
    app.dependency_overrides[get_uow] = lambda: uow
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.module_features.ModuleFeatureService.delete_feature",
        new=AsyncMock(
            return_value={
                "is_deleted": True,
                "deletion_reason": "No longer needed",
                "deleted_at": None,
            }
        ),
    ) as mock_delete:
        client = TestClient(app)
        response = client.request(
            "DELETE",
            f"/projects/{project_id}/modules/module_1/features/feature_1",
            json={"reason": "No longer needed"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["is_deleted"] is True
    assert data["deletion_reason"] == "No longer needed"
    mock_delete.assert_awaited_once_with(
        project_id=project_id,
        module_id="module_1",
        feature_id="feature_1",
        uow=ANY,
        reason="No longer needed",
    )


def test_delete_feature_without_body_forwards_none_reason() -> None:
    app = FastAPI()
    app.include_router(module_features_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    uow = _override_uow()
    app.dependency_overrides[get_uow] = lambda: uow
    project_id = uuid.uuid4()

    with patch(
        "app.routes.v1.module_features.ModuleFeatureService.delete_feature",
        new=AsyncMock(
            return_value={"is_deleted": True, "deletion_reason": None, "deleted_at": None}
        ),
    ) as mock_delete:
        client = TestClient(app)
        response = client.request(
            "DELETE", f"/projects/{project_id}/modules/module_1/features/feature_1"
        )

    assert response.status_code == 200
    mock_delete.assert_awaited_once_with(
        project_id=project_id,
        module_id="module_1",
        feature_id="feature_1",
        uow=ANY,
        reason=None,
    )
