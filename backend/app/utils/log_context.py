"""Structured logging context propagation.

Mirrors the ``correlation_id`` ``ContextVar`` pattern in ``correlation.py``
but for arbitrary business identifiers (``project_id``, ``module_id``,
``source_id``, ...). Every log record emitted while a context is bound
automatically includes these fields — via ``_LogContextFilter`` in
``app/utils/logger.py`` — without threading them through every function
signature.

Usage (route handlers, services, worker tasks):
    from app.utils.log_context import bind_log_context

    with bind_log_context(project_id=str(project_id)):
        ...  # every log line in this block includes "project_id"

Celery propagation: ``app/core/celery_app.py`` attaches the current context
to outgoing task messages (``before_task_publish``) and rebinds it inside
the worker process for the task's duration (``task_prerun``/``task_postrun``),
so a log line emitted by a background task can be traced back to the
project/request that triggered it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_log_context_var: ContextVar[dict[str, object]] = ContextVar("log_context", default={})


def get_log_context() -> dict[str, object]:
    """Return the structured logging context for the current async context."""
    return _log_context_var.get()


@contextmanager
def bind_log_context(**fields: object) -> Iterator[None]:
    """Merge ``fields`` into the logging context for the duration of the block.

    ``None`` values are dropped so callers can pass optional identifiers
    (e.g. ``module_id=None`` when not yet known) without polluting output.
    Nested calls merge on top of the existing context and restore it on exit.
    """
    current = _log_context_var.get()
    merged = {**current, **{k: v for k, v in fields.items() if v is not None}}
    token = _log_context_var.set(merged)
    try:
        yield
    finally:
        _log_context_var.reset(token)


def set_log_context(context: dict[str, object]) -> None:
    """Replace the logging context outright (no restore token).

    Used only by the Celery ``task_prerun``/``task_postrun`` signal handlers
    in ``app/core/celery_app.py`` to bind/clear the context for a worker task
    running in its own thread — those are two separate signal invocations,
    not a single enclosing ``with`` block, so ``bind_log_context``'s
    token-based restore does not apply. Prefer ``bind_log_context`` elsewhere.
    """
    _log_context_var.set(context)
