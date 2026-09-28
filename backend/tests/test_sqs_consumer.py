"""Unit tests for app.messaging.sqs_consumer."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.messaging.sqs_consumer import (
    _HANDLERS,
    _dispatch,
    register_handler,
)

# ── _dispatch ──────────────────────────────────────────────────────────────


class TestDispatch:
    @pytest.mark.asyncio
    async def test_calls_registered_handler(self) -> None:
        handler = AsyncMock()
        _HANDLERS["TEST_EVENT_DISPATCH"] = handler
        try:
            body = json.dumps({"event_type": "TEST_EVENT_DISPATCH", "data": 42})
            await _dispatch(body)
            handler.assert_awaited_once()
            call_arg = handler.await_args.args[0]
            assert call_arg["event_type"] == "TEST_EVENT_DISPATCH"
        finally:
            _HANDLERS.pop("TEST_EVENT_DISPATCH", None)

    @pytest.mark.asyncio
    async def test_unknown_event_type_does_not_raise(self) -> None:
        body = json.dumps({"event_type": "TOTALLY_UNKNOWN_999"})
        # Must not raise — just logs a warning
        await _dispatch(body)

    @pytest.mark.asyncio
    async def test_malformed_json_does_not_raise(self) -> None:
        await _dispatch("{not valid json}")

    @pytest.mark.asyncio
    async def test_missing_event_type_uses_unknown(self) -> None:
        """Payload without event_type should default to 'unknown' gracefully."""
        body = json.dumps({"some": "data"})
        # No handler for 'unknown' — should not raise
        await _dispatch(body)

    @pytest.mark.asyncio
    async def test_handler_receives_full_payload(self) -> None:
        received: list[dict] = []

        async def _handler(payload: dict) -> None:
            received.append(payload)

        _HANDLERS["FULL_PAYLOAD_TEST"] = _handler
        try:
            body = json.dumps({"event_type": "FULL_PAYLOAD_TEST", "key": "value", "num": 7})
            await _dispatch(body)
            assert received[0]["key"] == "value"
            assert received[0]["num"] == 7
        finally:
            _HANDLERS.pop("FULL_PAYLOAD_TEST", None)


# ── register_handler ───────────────────────────────────────────────────────


class TestRegisterHandler:
    def test_decorator_registers_function(self) -> None:
        @register_handler("MY_TEST_EVENT")
        async def _handler(payload: dict) -> None:
            pass

        try:
            assert _HANDLERS["MY_TEST_EVENT"] is _handler
        finally:
            _HANDLERS.pop("MY_TEST_EVENT", None)

    def test_decorator_returns_original_function(self) -> None:
        async def _fn(p: dict) -> None:
            pass

        result = register_handler("MY_TEST_EVENT_2")(_fn)
        try:
            assert result is _fn
        finally:
            _HANDLERS.pop("MY_TEST_EVENT_2", None)

    def test_re_registering_overwrites(self) -> None:
        async def _h1(p: dict) -> None:
            pass

        async def _h2(p: dict) -> None:
            pass

        register_handler("OVERWRITE_TEST")(_h1)
        register_handler("OVERWRITE_TEST")(_h2)
        try:
            assert _HANDLERS["OVERWRITE_TEST"] is _h2
        finally:
            _HANDLERS.pop("OVERWRITE_TEST", None)


# ── run_consumer (shallow smoke test) ─────────────────────────────────────


class TestRunConsumer:
    @pytest.mark.asyncio
    async def test_processes_single_message_and_deletes_it(self) -> None:
        """Simulate one loop iteration returning one message then empty."""

        from app.messaging.sqs_consumer import run_consumer

        handler = AsyncMock()
        _HANDLERS["CONSUMER_TEST_EVT"] = handler

        msg_body = json.dumps({"event_type": "CONSUMER_TEST_EVT"})
        call_count = 0

        async def _receive(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"Messages": [{"Body": msg_body, "ReceiptHandle": "handle-001"}]}
            # Raise after second poll so the loop terminates in the test
            raise StopAsyncIteration

        sqs_client = AsyncMock()
        sqs_client.receive_message = _receive
        sqs_client.delete_message = AsyncMock()

        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=sqs_client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("app.messaging.sqs_consumer._SESSION") as mock_session:
            mock_session.client.return_value = cm
            with pytest.raises(StopAsyncIteration):
                await run_consumer(queue_url="https://sqs.us-east-1.amazonaws.com/q")

        handler.assert_awaited_once()
        sqs_client.delete_message.assert_awaited_once_with(
            QueueUrl="https://sqs.us-east-1.amazonaws.com/q",
            ReceiptHandle="handle-001",
        )

        _HANDLERS.pop("CONSUMER_TEST_EVT", None)

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_delete_message(self) -> None:
        """If handler raises, the message should NOT be deleted (for DLQ retry)."""

        from app.messaging.sqs_consumer import run_consumer

        async def _bad_handler(payload: dict) -> None:
            raise RuntimeError("handler blew up")

        _HANDLERS["BAD_EVT"] = _bad_handler

        call_count = 0

        async def _receive(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "Messages": [
                        {"Body": json.dumps({"event_type": "BAD_EVT"}), "ReceiptHandle": "rh-002"}
                    ]
                }
            raise StopAsyncIteration

        sqs_client = AsyncMock()
        sqs_client.receive_message = _receive
        sqs_client.delete_message = AsyncMock()

        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=sqs_client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("app.messaging.sqs_consumer._SESSION") as mock_session:
            mock_session.client.return_value = cm
            with pytest.raises(StopAsyncIteration):
                await run_consumer(queue_url="q")

        sqs_client.delete_message.assert_not_awaited()
        _HANDLERS.pop("BAD_EVT", None)
