"""Unit tests for fragment routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps import get_current_db_user, get_uow
from app.routes.v1.fragments import router as fragment_router
from app.schemas.fragment_schema import (
    CreateSingleFragmentResponse,
    FragmentResponse,
)
from tests.conftest import make_user


def test_get_fragment_success() -> None:
    app = FastAPI()
    app.include_router(fragment_router)
    app.dependency_overrides[get_current_db_user] = lambda: make_user()
    app.dependency_overrides[get_uow] = lambda: object()
    project_id = uuid.uuid4()
    source_id = uuid.uuid4()
    response_model = CreateSingleFragmentResponse(
        source_id=source_id,
        fragment=FragmentResponse(
            id="fragment-1",
            source_id=source_id,
            frag_type="text",
            content="View fragment",
            bbox=[],
            content_hash="hash-view",
        ),
    )

    with patch(
        "app.routes.v1.fragments.FragmentService.get_fragment_by_project",
        new=AsyncMock(return_value=response_model),
    ) as mock_get:
        client = TestClient(app)
        response = client.get(f"/projects/{project_id}/fragments/fragment-1")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["fragment"]["id"] == "fragment-1"
    mock_get.assert_awaited_once()
