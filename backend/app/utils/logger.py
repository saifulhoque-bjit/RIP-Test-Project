"""Centralised logger factory.

Usage (one logger per module):
    from app.utils.logger import get_logger
    logger = get_logger(__name__)

Rules:
- Never log passwords, tokens, PII.
- Always pass exc_info=True on error/critical calls.
- Never use print() for operational output.

Correlation ID
──────────────
The ``_CorrelationIdFilter`` reads the current correlation ID from the
``ContextVar`` set by ``CorrelationIdMiddleware`` so every log line emitted
during a request automatically includes it without callers having to pass it
down the call stack.

JSON format
───────────
All log records are emitted as single-line JSON so they can be ingested by
CloudWatch Logs Insights, Datadog, ELK, or any structured-log pipeline
without a custom parser.  The ``python-json-logger`` package is used for
the formatter; key fields are:

    timestamp     — ISO-8601 UTC timestamp
    level         — DEBUG | INFO | WARNING | ERROR | CRITICAL
    logger        — dotted module name
    correlation_id — per-request trace ID (from CorrelationIdMiddleware)
    message       — log message
    exc_info      — exception traceback (only when exc_info=True)

Any field bound via ``app.utils.log_context.bind_log_context`` (e.g.
``project_id``, ``module_id``) is added automatically too — see
``_LogContextFilter`` below and ``app/utils/log_context.py``.
"""

from __future__ import annotations

from functools import cache
import logging
import sys

from pythonjsonlogger.json import JsonFormatter


# Capture the real stream before any raw-output wrapper is installed. Structured
# log records must never be modified by the raw print tagging layer.
_REAL_STDOUT = sys.stdout


class _CorrelationIdFilter(logging.Filter):
    """Inject the per-request ``correlation_id`` into every log record.

    Reads from the ContextVar maintained by ``CorrelationIdMiddleware`` so
    the value propagates automatically through async call chains without
    any explicit passing.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            # Late import avoids a module-level circular import since
            # correlation.py has no dependency on logger.py.
            from app.utils.correlation import get_correlation_id  # noqa: PLC0415

            record.correlation_id = get_correlation_id()  # type: ignore[attr-defined]
        return True


class _LogContextFilter(logging.Filter):
    """Inject bound structured context (project_id, module_id, ...) into every record.

    Reads from the ContextVar maintained by ``bind_log_context`` (see
    ``app/utils/log_context.py``) so business identifiers propagate
    automatically through async/worker call chains, mirroring
    ``_CorrelationIdFilter``. Fields are only added when bound, so log lines
    outside a ``bind_log_context`` block are unaffected.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        from app.utils.log_context import get_log_context  # noqa: PLC0415

        for key, value in get_log_context().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


# Field order: timestamp first so log aggregators sort naturally. Names here
# must match the *source* LogRecord attributes (asctime/levelname/name) —
# rename_fields below maps them to the desired output keys. Using the
# renamed keys directly (e.g. %(level)s) looks right but silently resolves
# to None, since LogRecord has no "level" attribute — only "levelname".
_JSON_FORMAT = "%(asctime)s %(levelname)s %(name)s %(correlation_id)s %(message)s"


def _build_json_handler() -> logging.StreamHandler:
    """Build a stdout handler emitting single-line JSON, correlation-id tagged."""
    handler = logging.StreamHandler(_REAL_STDOUT)
    formatter = JsonFormatter(
        fmt=_JSON_FORMAT,
        rename_fields={
            "asctime": "timestamp",
            "levelname": "level",
            "name": "logger",
        },
        timestamp=True,
    )
    handler.setFormatter(formatter)
    handler.addFilter(_CorrelationIdFilter())
    handler.addFilter(_LogContextFilter())
    return handler


@cache
def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger, configured once per name."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.addHandler(_build_json_handler())
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def configure_root_json_logging(level: int | str = logging.INFO) -> None:
    """Route every logger that propagates to root through the JSON formatter.

    Celery (and the libraries it wraps: kombu, redis, billiard) configure
    their own plain-text, multi-line logging by default, which is why broker
    reconnect tracebacks show up as raw dumps instead of the structured JSON
    the rest of the app emits. Call this from the ``celery.signals.setup_logging``
    handler so worker processes log consistently with the API process.
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(_build_json_handler())
    root.setLevel(level)
