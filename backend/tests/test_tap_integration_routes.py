"""Unit tests for TAP sync routes.

Handlers are called directly with ``MagicMock`` services standing in for the
injected services; ``uow`` is an inert placeholder the route only forwards.
``sync_execute`` receives a real minimal ``starlette.Request`` because the
SlowAPI wrapper inspects it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest
from starlette.requests import Request

from app.core.exceptions import ForbiddenError, ServiceUnavailableError
from app.routes.v1.tap_integrations import (
    get_sync_data,
    receive_ack,
    sync_execute,
    verify_tap_ack_signature,
)
from app.schemas.tap_integration_schema import (
    TapAckRequest,
    TapAckResponse,
    TapSyncDataResponse,
    TapSyncExecuteRequest,
    TapSyncExecuteResponse,
)
from tests.conftest import make_user


def _make_request() -> Request:
    return Request(
        scope={
            "type": "http",
            "method": "POST",
            "path": "/",
            "query_string": b"",
            "headers": [],
        }
    )


class TestSyncRoute:
    async def test_sync_execute_stages_and_notifies(self) -> None:
        project_id = uuid.uuid4()
        sync_service = MagicMock()

        module_id = uuid.uuid4()
        payload = TapSyncExecuteRequest(
            modules=[
                {
                    "module_id": module_id,
                    "module_code": "1",
                    "module_name": "M",
                    "module_description": "d",
                    "features": [],
                }
            ]
        )
        expected = TapSyncExecuteResponse(
            sync_id=uuid.uuid4(),
            created=1,
            updated=0,
            total_synced=1,
            message="ok",
        )
        sync_service.execute_sync_with_hierarchy = AsyncMock(return_value=expected)
        user = make_user()
        uow = object()

        result = await sync_execute(
            _make_request(),
            project_id=project_id,
            payload=payload,
            current_user=user,
            uow=uow,
            sync_service=sync_service,
        )

        assert result.success is True
        assert result.data.total_synced == 1
        sync_service.execute_sync_with_hierarchy.assert_awaited_once_with(
            project_id=project_id,
            modules=payload.modules,
            triggered_by_id=user.id,
            uow=uow,
        )


class TestPullRoute:
    def test_get_sync_data_wraps_response(self) -> None:
        project_id = uuid.uuid4()
        sync_id = uuid.uuid4()
        sync_service = MagicMock()
        expected = TapSyncDataResponse(sync_id=sync_id, modules=[])
        sync_service.get_sync_payload.return_value = expected
        uow = object()

        result = get_sync_data(
            project_id=project_id, sync_id=sync_id, uow=uow, sync_service=sync_service
        )

        assert result.data is expected
        sync_service.get_sync_payload.assert_called_once_with(
            project_id=project_id, sync_id=sync_id, uow=uow
        )


class TestAckRoute:
    async def test_receive_ack_wraps_response(self) -> None:
        project_id = uuid.uuid4()
        sync_service = MagicMock()
        expected = TapAckResponse(
            ack_history_id=uuid.uuid4(),
            job_id="abc-123",
            status="COMPLETED",
            entities_synced=2,
            message="ok",
        )
        sync_service.handle_ack = AsyncMock(return_value=expected)
        sync_id = uuid.uuid4()
        payload = TapAckRequest(
            job_id="abc-123",
            order_index=1,
            status="COMPLETED",
            total_requirements=2,
            processed_requirements=2,
            failed_requirements=0,
        )
        uow = object()

        result = await receive_ack(
            project_id=project_id,
            sync_id=sync_id,
            payload=payload,
            uow=uow,
            sync_service=sync_service,
        )

        assert result.success is True
        assert result.data.entities_synced == 2
        sync_service.handle_ack.assert_awaited_once_with(
            project_id=project_id, sync_id=sync_id, request=payload, uow=uow
        )


class TestInboundCallbackGuard:
    """``verify_tap_ack_signature`` protects the two endpoints TAP calls.

    Neither is user-authenticated: one serves a project's whole staged
    requirements hierarchy, the other flips entities to synced. The guard is
    all that stands in front of them, so its failure modes are pinned here.
    """

    def test_rejects_a_wrong_signature(self) -> None:
        with (
            patch("app.routes.v1.tap_integrations.settings.TAP_ACK_SECRET", "s3cret"),
            pytest.raises(ForbiddenError),
        ):
            verify_tap_ack_signature(x_tap_signature="not-the-secret")

    def test_rejects_a_missing_signature(self) -> None:
        with (
            patch("app.routes.v1.tap_integrations.settings.TAP_ACK_SECRET", "s3cret"),
            pytest.raises(ForbiddenError),
        ):
            verify_tap_ack_signature(x_tap_signature=None)

    def test_accepts_the_configured_signature(self) -> None:
        with patch("app.routes.v1.tap_integrations.settings.TAP_ACK_SECRET", "s3cret"):
            verify_tap_ack_signature(x_tap_signature="s3cret")  # must not raise

    def test_fails_closed_when_no_secret_is_configured(self) -> None:
        """An unset secret is a deployment mistake, not "auth disabled".

        Returning early here would leave both callbacks open to anyone who can
        reach the API — a project's entire requirements hierarchy readable and
        its sync flags writable.
        """
        with (
            patch("app.routes.v1.tap_integrations.settings.TAP_ACK_SECRET", ""),
            pytest.raises(ServiceUnavailableError),
        ):
            verify_tap_ack_signature(x_tap_signature="anything")
