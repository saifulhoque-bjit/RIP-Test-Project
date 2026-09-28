"""Tests for the mid-stream cancellation polling added to
app/services/source_code_pipeline/src/ai/llm_client.py's _stream_and_rebuild.

Scope: _stream_and_rebuild runs on a spawned watchdog daemon thread (see
_completion_with_watchdog), where the usual thread-local cancel check
(_raise_if_run_cancelled / _RUN_CANCEL) is blind — these tests cover the
explicit request_id plumbing that closes that gap, without exercising the
rest of this large module (no LLMClient instantiation, no config loading).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.source_code_pipeline.src.ai.llm_client import (
    PipelineRunCancelled,
    _is_request_cancelled,
    _stream_and_rebuild,
)

_MODULE = "app.services.source_code_pipeline.src.ai.llm_client"


class _FakeStream:
    """Minimal stand-in for litellm's streaming iterator."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def __iter__(self):
        return iter(self._chunks)

    def close(self):
        self.closed = True


class TestIsRequestCancelled:
    def test_false_when_no_request_id(self):
        assert _is_request_cancelled(None) is False

    def test_true_when_task_control_reports_cancelled(self):
        with patch("app.core.task_control.is_request_cancelled", return_value=True):
            assert _is_request_cancelled("req-1") is True

    def test_false_when_task_control_reports_not_cancelled(self):
        with patch("app.core.task_control.is_request_cancelled", return_value=False):
            assert _is_request_cancelled("req-1") is False

    def test_fails_open_when_task_control_import_fails(self):
        with patch.dict("sys.modules", {"app.core.task_control": None}):
            assert _is_request_cancelled("req-1") is False


class TestStreamAndRebuildCancellation:
    def test_drains_fully_when_no_request_id_supplied(self):
        chunks = [MagicMock() for _ in range(3)]
        fake_stream = _FakeStream(chunks)
        with (
            patch(f"{_MODULE}.litellm.completion", return_value=fake_stream),
            patch(f"{_MODULE}.litellm.stream_chunk_builder", return_value="rebuilt") as builder,
        ):
            result = _stream_and_rebuild({"model": "x"})

        assert result == "rebuilt"
        assert builder.call_args.args[0] == chunks
        assert fake_stream.closed is True

    def test_drains_fully_when_never_cancelled(self):
        chunks = [MagicMock() for _ in range(3)]
        fake_stream = _FakeStream(chunks)
        with (
            patch(f"{_MODULE}.litellm.completion", return_value=fake_stream),
            patch(f"{_MODULE}.litellm.stream_chunk_builder", return_value="rebuilt"),
            patch("app.core.task_control.is_request_cancelled", return_value=False),
        ):
            result = _stream_and_rebuild({"model": "x"}, request_id="req-1", poll_every=5)

        assert result == "rebuilt"

    def test_aborts_mid_stream_once_cancelled(self):
        """Cancellation flips true after the first chunk (poll_every=1 checks
        every chunk) — the stream must stop draining immediately rather than
        consuming the rest of the (possibly very long) generation."""
        chunks = [MagicMock() for _ in range(5)]
        fake_stream = _FakeStream(chunks)
        cancelled_after_first = iter([False, True, True, True, True])
        with (
            patch(f"{_MODULE}.litellm.completion", return_value=fake_stream),
            patch(f"{_MODULE}.litellm.stream_chunk_builder") as builder,
            patch(
                "app.core.task_control.is_request_cancelled",
                side_effect=lambda _rid: next(cancelled_after_first),
            ),
            pytest.raises(PipelineRunCancelled, match="req-1"),
        ):
            _stream_and_rebuild({"model": "x"}, request_id="req-1", poll_every=1)

        builder.assert_not_called()
        assert fake_stream.closed is True

    def test_closes_stream_even_when_cancelled(self):
        chunks = [MagicMock() for _ in range(3)]
        fake_stream = _FakeStream(chunks)
        with (
            patch(f"{_MODULE}.litellm.completion", return_value=fake_stream),
            patch("app.core.task_control.is_request_cancelled", return_value=True),
            pytest.raises(PipelineRunCancelled),
        ):
            _stream_and_rebuild({"model": "x"}, request_id="req-1", poll_every=1)

        assert fake_stream.closed is True
