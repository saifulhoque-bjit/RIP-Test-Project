"""Cooperative-cancellation wrapper for LLM calls.

`.ainvoke()` sends the request non-streaming: the provider computes the full
completion before returning anything, so there is no point at which a
client-side cancel stops you being billed for it. Streaming and aborting the
connection early *does* stop billing on providers that support it (OpenAI,
Anthropic, DeepSeek — not Google/Gemini, which bills the full generation
regardless). This module streams the response instead, polling the existing
``task_control`` Redis flag every few chunks, and aborts the connection the
moment it sees the request was cancelled.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core import task_control
from app.utils.logger import get_logger

logger = get_logger(__name__)


class LLMCancelled(Exception):
    """Raised when a streaming LLM call is aborted mid-flight due to cancellation."""

    def __init__(self, request_id: object):
        self.request_id = request_id
        super().__init__(f"LLM call aborted — request {request_id} was cancelled")


def _merge(accumulated: Any, chunk: Any) -> Any:
    try:
        return accumulated + chunk
    except TypeError:
        # Structured-output runnables (with_structured_output) yield the fully
        # parsed object per chunk rather than an addable delta — this is the
        # expected, routine case for those runnables, not an error. DEBUG only:
        # a long structured stream can hit this on every single chunk, and
        # that would flood INFO-level logs for something that isn't wrong.
        logger.debug(
            "[CANCELLABLE] chunk of type %s isn't incrementally addable — expected "
            "for structured-output runnables, keeping the latest chunk instead of merging",
            type(chunk).__name__,
        )
        return chunk


async def ainvoke_cancellable(
    runnable: Any,
    messages: list,
    *,
    request_id: object | None,
    poll_every: int = 5,
) -> Any:
    """Equivalent to ``await runnable.ainvoke(messages)``, but cancellable.

    Streams the response and checks ``task_control.is_request_cancelled`` every
    ``poll_every`` chunks. Raises ``LLMCancelled`` and closes the underlying
    stream as soon as the request is seen to be cancelled. Falls back to a
    plain ``ainvoke`` when no ``request_id`` is supplied (nothing to poll).
    """
    if not request_id:
        logger.debug(
            "[CANCELLABLE] no request_id supplied — falling back to plain (non-cancellable) ainvoke"
        )
        return await runnable.ainvoke(messages)

    result = None
    stream = runnable.astream(messages)
    t_start = time.monotonic()
    n = 0
    try:
        async for chunk in stream:
            result = chunk if result is None else _merge(result, chunk)
            n += 1
            if (n == 1 or n % poll_every == 0) and await asyncio.to_thread(
                task_control.is_request_cancelled, request_id
            ):
                logger.info(
                    "[CANCELLABLE] request=%s cancel detected at n=%d t=%.1fs",
                    request_id,
                    n,
                    time.monotonic() - t_start,
                )
                raise LLMCancelled(request_id)
        logger.info(
            "[LLM] request=%s stream done: n=%d t=%.1fs", request_id, n, time.monotonic() - t_start
        )
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception as e:
                logger.warning(
                    "[CANCELLABLE] request=%s failed to close underlying stream cleanly (%s: %s) — "
                    "suppressed so it doesn't mask an in-flight LLMCancelled",
                    request_id,
                    type(e).__name__,
                    e,
                )
    return result


# cancellable_llm.py — new function alongside ainvoke_cancellable


async def ainvoke_cancellable_structured(
    structured_llm: Any,
    messages: list,
    *,
    request_id: object | None,
    poll_every: int = 1,
) -> Any:
    """For llm.with_structured_output(..., method="function_calling") runnables.

    That wrapper is [tool-bound model] | [output parser]. Streaming the whole
    thing buffers every tool_call_chunk inside the parser and only yields once
    the full (already-billed) call is done. This streams just the model half —
    which does emit progressive tool_call_chunks — and runs the existing parser
    once at the end. Same schema, same validation, now actually cancellable.
    """
    if not (hasattr(structured_llm, "first") and hasattr(structured_llm, "last")):
        logger.warning(
            "[LLM] request=%s structured_llm has no .first/.last (not a "
            "RunnableSequence in this langchain-core version) — falling back to "
            "ainvoke_cancellable on the whole chain. That buffers the full "
            "structured-output call before yielding anything, so cancellation "
            "will only take effect once the whole (already-billed) completion is done.",
            request_id,
        )
        return await ainvoke_cancellable(
            structured_llm, messages, request_id=request_id, poll_every=poll_every
        )

    bound_model, parser = structured_llm.first, structured_llm.last

    if not request_id:
        logger.debug(
            "[CANCELLABLE] no request_id supplied — falling back to plain (non-cancellable) ainvoke"
        )
        return await parser.ainvoke(await bound_model.ainvoke(messages))

    result = None
    stream = bound_model.astream(messages)
    try:
        n = 0
        t_start = time.monotonic()
        async for chunk in stream:
            result = chunk if result is None else result + chunk
            n += 1
            if (n == 1 or n % poll_every == 0) and await asyncio.to_thread(
                task_control.is_request_cancelled, request_id
            ):
                logger.info(
                    "[CANCELLABLE] request=%s cancel detected at n=%d t=%.1fs",
                    request_id,
                    n,
                    time.monotonic() - t_start,
                )
                raise LLMCancelled(request_id)
        logger.info(
            "[LLM] request=%s stream done: n=%d t=%.1fs", request_id, n, time.monotonic() - t_start
        )
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception as e:
                logger.warning(
                    "[CANCELLABLE] request=%s failed to close underlying stream cleanly (%s: %s) — "
                    "suppressed so it doesn't mask an in-flight LLMCancelled",
                    request_id,
                    type(e).__name__,
                    e,
                )

    return await parser.ainvoke(result)
