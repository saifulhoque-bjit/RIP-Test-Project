"""Unit tests for app.messaging.sqs_producer.send_event."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_sqs_context(message_id: str = "msg-abc-123") -> tuple[AsyncMock, MagicMock]:
    """Return (sqs_client_mock, context_manager_mock)."""
    sqs_client = AsyncMock()
    sqs_client.send_message = AsyncMock(return_value={"MessageId": message_id})

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=sqs_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return sqs_client, cm


# ── Tests ──────────────────────────────────────────────────────────────────


class TestSendEvent:
    @pytest.mark.asyncio
    async def test_returns_message_id_on_success(self) -> None:
        sqs_client, cm = _make_sqs_context("test-id-1")

        with patch("app.messaging.sqs_producer._SESSION") as mock_session:
            mock_session.client.return_value = cm

            from app.messaging.sqs_producer import send_event

            result = await send_event(
                {"event_type": "TEST_CREATED"}, "https://sqs.us-east-1.amazonaws.com/123/queue.fifo"
            )

        assert result == "test-id-1"

    @pytest.mark.asyncio
    async def test_sends_json_serialised_body(self) -> None:
        data = {"event_type": "TEST_UPDATED", "test_id": "abc-123"}
        sqs_client, cm = _make_sqs_context()

        with patch("app.messaging.sqs_producer._SESSION") as mock_session:
            mock_session.client.return_value = cm

            from app.messaging.sqs_producer import send_event

            await send_event(data, "https://sqs.us-east-1.amazonaws.com/123/q.fifo")

        call_kwargs = sqs_client.send_message.call_args.kwargs
        parsed = json.loads(call_kwargs["MessageBody"])
        assert parsed["event_type"] == "TEST_UPDATED"
        assert parsed["test_id"] == "abc-123"

    @pytest.mark.asyncio
    async def test_uses_rip_events_as_group_id(self) -> None:
        sqs_client, cm = _make_sqs_context()

        with patch("app.messaging.sqs_producer._SESSION") as mock_session:
            mock_session.client.return_value = cm

            from app.messaging.sqs_producer import send_event

            await send_event({}, "https://sqs.us-east-1.amazonaws.com/123/q.fifo")

        call_kwargs = sqs_client.send_message.call_args.kwargs
        assert call_kwargs["MessageGroupId"] == "rip-events"

    @pytest.mark.asyncio
    async def test_deduplication_id_is_unique_uuid(self) -> None:
        sqs_client, cm = _make_sqs_context()
        ids: list[str] = []

        async def _capture_send(**kwargs):
            ids.append(kwargs["MessageDeduplicationId"])
            return {"MessageId": "mid"}

        sqs_client.send_message = _capture_send
        cm.__aenter__ = AsyncMock(return_value=sqs_client)

        with patch("app.messaging.sqs_producer._SESSION") as mock_session:
            mock_session.client.return_value = cm

            from app.messaging.sqs_producer import send_event

            await send_event({}, "q.fifo")
            cm2 = AsyncMock()
            sqs2 = AsyncMock()
            sqs2.send_message = _capture_send
            cm2.__aenter__ = AsyncMock(return_value=sqs2)
            cm2.__aexit__ = AsyncMock(return_value=False)
            mock_session.client.return_value = cm2
            await send_event({}, "q.fifo")

        assert ids[0] != ids[1], "Deduplication IDs should be unique per call"

    @pytest.mark.asyncio
    async def test_returns_unknown_when_message_id_absent(self) -> None:
        sqs_client, cm = _make_sqs_context()
        sqs_client.send_message = AsyncMock(return_value={})  # no MessageId

        with patch("app.messaging.sqs_producer._SESSION") as mock_session:
            mock_session.client.return_value = cm

            from app.messaging.sqs_producer import send_event

            result = await send_event({}, "q")

        assert result == "unknown"

    @pytest.mark.asyncio
    async def test_reraises_after_exhausted_retries(self) -> None:
        """tenacity reraises after max_retries; stop_after_attempt is 3."""
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(side_effect=Exception("SQS unavailable"))
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("app.messaging.sqs_producer._SESSION") as mock_session:
            mock_session.client.return_value = cm

            from app.messaging.sqs_producer import send_event

            with pytest.raises(Exception, match="SQS unavailable"):
                await send_event({}, "q")
