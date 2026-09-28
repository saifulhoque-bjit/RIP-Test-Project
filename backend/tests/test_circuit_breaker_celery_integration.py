"""TC-3.7 (docs/testing/source_code_pipeline_user_test_cases.md) — the
"5-minute check" from docs/CircuitBreakerError_Handling_Issue_Implications.docx:

    raise CircuitBreakerError from a trivial Celery task, run it, and
    confirm: result.state == "FAILURE", the traceback is in the backend,
    the worker stays alive (doesn't crash), and the task is not
    redelivered.

This is deliberately NOT a mocked unit test — the whole point is verifying
how *Celery's own* task-tracing machinery classifies an exception, which
mocking our application code cannot exercise. It runs a real (but fully
in-process) Celery worker via `celery.contrib.testing.worker.start_worker`
against Celery's built-in `memory://` transport — not a real Redis/RabbitMQ
broker, so this doesn't violate the "never hit a real broker in a unit
test" rule (see `.claude/rules/tests.md`); it's Celery's own recommended
harness for exactly this kind of test.

Uses a throwaway `Celery('t', ...)` app with trivial tasks, not the real
`app.core.celery_app.celery_app` — the goal is isolating Celery's generic
BaseException-vs-Exception handling, not exercising the real pipeline.

Runs the test worker with `pool="solo"` rather than production's `-P
prefork` — the `cache+memory://` backend used here is an in-process dict
that isn't visible across the separate OS processes a real prefork pool
spawns, which made this harness unreliable under prefork (confirmed: even
the *passing* case couldn't retrieve its own result). `solo` keeps everything
in one process so the backend round-trip is reliable, and it exercises the
exact same code path responsible for the bug (`celery/concurrency/base.py`'s
`apply_target`, which converts an unhandled BaseException into
`WorkerLostError` regardless of pool type) — separately confirmed by hand
against a real `-P prefork` pool, where an unhandled BaseException crashes
the worker child outright (exitcode 0, "Worker exited prematurely").
"""

from __future__ import annotations

from celery import Celery
from celery.contrib.testing.worker import start_worker
import pytest


def _make_app() -> Celery:
    app = Celery("test_circuit_breaker", broker="memory://", backend="cache+memory://")
    app.conf.update(task_always_eager=False, worker_hijack_root_logger=False)
    return app


class _BaseExceptionTrip(BaseException):
    """Stand-in for the real CircuitBreakerError (also a bare BaseException,
    see app/services/source_code_pipeline/src/ai/llm_client.py) — using a
    throwaway class here keeps this test app import-independent."""


class _ExceptionTrip(Exception):
    """Stand-in for CircuitBreakerTaskFailure (app/workers/_task_helpers.py)
    — the plain-Exception re-raise our fix uses at a Celery task boundary."""


class TestCircuitBreakerTaskFailureIsRecordedAsFailure:
    """The fixed behavior: catching a BaseException-style breaker trip and
    re-raising a plain Exception at the task boundary."""

    def test_plain_exception_trip_is_recorded_as_failure(self):
        app = _make_app()

        @app.task(bind=True, name="t.exception_trip", acks_late=True)
        def exception_trip(self):
            raise _ExceptionTrip("breaker tripped, converted for Celery")

        with start_worker(app, perform_ping_check=False, pool="solo"):
            result = exception_trip.delay()
            # Must resolve promptly to FAILURE — not hang waiting for a
            # PENDING task that never completes.
            outcome = result.get(timeout=5, propagate=False)

        assert result.state == "FAILURE"
        assert result.traceback is not None
        assert isinstance(outcome, _ExceptionTrip)


class TestUnhandledBaseExceptionIsLostByCelery:
    """Documents the ORIGINAL bug this fix addresses: an unhandled
    BaseException (the shape of the real CircuitBreakerError before a task
    boundary converts it) is NOT recorded as a normal Celery failure — the
    task result never resolves. This is what motivated
    CircuitBreakerTaskFailure existing at all; if this test ever starts
    failing (i.e. Celery starts handling bare BaseExceptions like normal
    Exceptions), the wrapper class may no longer be necessary — but until
    then, every CircuitBreakerError-raising Celery task boundary in
    app/workers/source_code_task.py MUST catch it explicitly."""

    def test_unhandled_base_exception_never_resolves_to_failure(self):
        app = _make_app()

        @app.task(bind=True, name="t.base_exception_trip", acks_late=True)
        def base_exception_trip(self):
            raise _BaseExceptionTrip("breaker tripped, left unconverted")

        with start_worker(app, perform_ping_check=False, pool="solo"):
            result = base_exception_trip.delay()
            with pytest.raises(Exception, match="(?i)timed out"):
                # Never resolves — Celery's trace_task only recognizes
                # Exception, so this is not recorded as FAILURE at all.
                result.get(timeout=2, propagate=False)

        assert result.state == "PENDING"
