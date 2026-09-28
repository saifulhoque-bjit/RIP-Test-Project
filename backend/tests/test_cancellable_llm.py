"""Unit tests for app.utils.cancellable_llm."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.cancellable_llm import (
    LLMCancelled,
    _merge,
    ainvoke_cancellable,
    ainvoke_cancellable_structured,
)


class _FakeStream:
    """An async-iterable that also exposes an awaitable `aclose()`."""

    def __init__(self, chunks, aclose_error: Exception | None = None):
        self._chunks = chunks
        self._aclose_error = aclose_error
        self.aclose_called = False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        self.aclose_called = True
        if self._aclose_error is not None:
            raise self._aclose_error


class TestMerge:
    def test_merges_addable_chunks(self):
        assert _merge("a", "b") == "ab"

    def test_falls_back_to_latest_chunk_when_not_addable(self):
        assert _merge(1, "not addable") == "not addable"


class TestAinvokeCancellable:
    async def test_no_request_id_falls_back_to_plain_ainvoke(self):
        runnable = MagicMock()
        runnable.ainvoke = AsyncMock(return_value="result")

        result = await ainvoke_cancellable(runnable, ["msg"], request_id=None)

        assert result == "result"
        runnable.ainvoke.assert_awaited_once_with(["msg"])

    async def test_streams_and_merges_chunks(self):
        runnable = MagicMock()
        stream = _FakeStream(["a", "b", "c"])
        runnable.astream = MagicMock(return_value=stream)

        with patch("app.core.task_control.is_request_cancelled", return_value=False):
            result = await ainvoke_cancellable(runnable, ["msg"], request_id="req-1")

        assert result == "abc"
        assert stream.aclose_called is True

    async def test_raises_llm_cancelled_when_cancel_detected(self):
        runnable = MagicMock()
        stream = _FakeStream(["a", "b", "c"])
        runnable.astream = MagicMock(return_value=stream)

        with patch("app.core.task_control.is_request_cancelled", return_value=True):
            with pytest.raises(LLMCancelled):
                await ainvoke_cancellable(runnable, ["msg"], request_id="req-1")

    async def test_swallows_aclose_error(self):
        runnable = MagicMock()
        stream = _FakeStream(["a"], aclose_error=RuntimeError("close failed"))
        runnable.astream = MagicMock(return_value=stream)

        with patch("app.core.task_control.is_request_cancelled", return_value=False):
            result = await ainvoke_cancellable(runnable, ["msg"], request_id="req-1")

        assert result == "a"

    async def test_polls_only_on_first_and_every_poll_every_chunks(self):
        runnable = MagicMock()
        stream = _FakeStream(["a", "b", "c", "d"])
        runnable.astream = MagicMock(return_value=stream)

        with patch("app.core.task_control.is_request_cancelled", return_value=False) as mock_check:
            await ainvoke_cancellable(runnable, ["msg"], request_id="req-1", poll_every=2)

        # n=1 (first) and n=2 (poll_every) trigger a check; n=3 doesn't; n=4 does.
        assert mock_check.call_count == 3


class TestAinvokeCancellableStructured:
    async def test_falls_back_when_not_a_runnable_sequence(self):
        structured_llm = object()  # has neither .first nor .last

        with patch(
            "app.utils.cancellable_llm.ainvoke_cancellable", new=AsyncMock(return_value="fallback")
        ) as mock_fallback:
            result = await ainvoke_cancellable_structured(
                structured_llm, ["msg"], request_id="req-1"
            )

        assert result == "fallback"
        mock_fallback.assert_awaited_once()

    async def test_no_request_id_falls_back_to_plain_ainvoke(self):
        bound_model = MagicMock()
        bound_model.ainvoke = AsyncMock(return_value="raw")
        parser = MagicMock()
        parser.ainvoke = AsyncMock(return_value="parsed")
        structured_llm = MagicMock(first=bound_model, last=parser)

        result = await ainvoke_cancellable_structured(structured_llm, ["msg"], request_id=None)

        assert result == "parsed"
        parser.ainvoke.assert_awaited_once_with("raw")

    async def test_streams_model_half_then_parses_result(self):
        bound_model = MagicMock()
        stream = _FakeStream(["a", "b"])
        bound_model.astream = MagicMock(return_value=stream)
        parser = MagicMock()
        parser.ainvoke = AsyncMock(return_value="parsed-result")
        structured_llm = MagicMock(first=bound_model, last=parser)

        with patch("app.core.task_control.is_request_cancelled", return_value=False):
            result = await ainvoke_cancellable_structured(
                structured_llm, ["msg"], request_id="req-1"
            )

        assert result == "parsed-result"
        parser.ainvoke.assert_awaited_once_with("ab")

    async def test_raises_llm_cancelled_when_cancel_detected(self):
        bound_model = MagicMock()
        stream = _FakeStream(["a", "b"])
        bound_model.astream = MagicMock(return_value=stream)
        parser = MagicMock()
        structured_llm = MagicMock(first=bound_model, last=parser)

        with patch("app.core.task_control.is_request_cancelled", return_value=True):
            with pytest.raises(LLMCancelled):
                await ainvoke_cancellable_structured(structured_llm, ["msg"], request_id="req-1")
