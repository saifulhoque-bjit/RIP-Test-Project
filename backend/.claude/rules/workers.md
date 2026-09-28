---
paths:
  - "app/workers/**/*.py"
---

# Celery worker conventions (`app/workers/*.py`)

Workers run source-processing pipelines (document/image/source-code parsing,
AI extraction phases) as Celery tasks. They must be safe to redeliver and
never leave a source stuck in a partial/locked state.

## Shared helpers

- Reuse `app/workers/_task_helpers.py` for status updates (`_mark_status`),
  retry bookkeeping (`_increment_retry`), running async code from a sync task
  (`_run_async`), and the ZIP-extraction path-traversal guard
  (`_extract_zip_to_codebase_folder` / `_assert_zip_members_are_safe`) — don't
  duplicate this logic in a new task module.
- `_run_async(coro)` spins up a brand-new event loop per call — safe for a
  coroutine that opens/owns its own connection for that one call, but never
  safe for a coroutine that reuses an async client/SDK object cached as a
  module-level singleton (that client is bound to whichever loop was running
  when it was first created; a later `_run_async` call runs on a *different*
  loop). Known current gap — don't copy: `app/clients/llamaparser_client.py`'s
  module-level `AsyncLlamaCloud` singleton is reused this way across
  `_run_async` calls in `document_task_stages.py`/`incremental_task.py` —
  either create the client fresh per call, or follow the owning-loop-capture
  pattern in `NotificationWebSocketManager.publish_threadsafe`
  (`app/websockets/notification_manager.py`) if the connection is expensive
  enough to need reuse. See `.claude/rules/services.md` for the full rule.
- Route failures through the existing `_handle_task_exception` pattern instead
  of a bare `except Exception: pass`, so status/error fields are updated
  consistently and the exception is logged with `exc_info=True`.

## Idempotency

- Every task must be safe to run twice for the same input (Celery redelivers
  on ack timeout/broker restart). Use Neo4j `MERGE` (never `CREATE`) for graph
  writes, and guard Postgres status transitions (e.g. only transition
  `PENDING -> PROCESSING`, never blindly overwrite a terminal `COMPLETED`/`FAILED`
  status).
- Queues are configured with `acks_late=True` and exponential backoff
  (60s → 120s → 240s, 3 retries, in `app/core/celery_app.py`) — don't override
  `acks_late=False` on a new task without a specific reason (document it in
  the task docstring if you do).
- New task types need an entry in `task_routes` (`app/core/celery_app.py`) to
  land on a dedicated queue — `tasks.sync_to_jira` is a known example that
  fell through to the default `celery` queue by omission; don't repeat that
  mistake for a new task.
- Task cancellation is cooperative (Redis flag keyed by `request_id`, not
  `terminate=True`) because production runs the `-P threads` pool — read
  README's "Task cancellation" section and `app/core/task_control.py` before
  touching anything in a long-running pipeline's checkpoint logic.
- A task that blocks inside its own body on `AsyncResult.get()` waiting for
  child tasks it just dispatched (e.g. `tasks.parse_code` /
  `_run_pipeline_orchestrator` in `source_code_task.py`, waiting on its
  per-module `source_code_processing`/`source_code_persistence` chain) MUST
  run on a worker pool that does NOT also consume the queue(s) those child
  tasks land on. Otherwise enough concurrent blocking parents fill every
  child in the shared pool, leaving none free to run the very tasks they're
  waiting on — a self-deadlock. `source_code_parsing` has its own dedicated
  pool (`i4_worker3` in `docker-compose.yml`/`scripts/dev_up.sh`, see
  `app/core/celery_app.py`'s instance-4 notes) for exactly this reason —
  never merge it back onto the same pool as `source_code_processing`/
  `source_code_persistence`.

## Retries & timeouts

- On `SoftTimeLimitExceeded`, prefer `self.retry(exc=..., countdown=...)` with
  a bounded `max_retries` over returning a terminal failed dict — a soft
  timeout is usually transient (slow LLM call, large file), not a permanent
  failure.
- Any lock acquired for exclusivity (e.g. a per-source/module completion lock)
  must only be held for as long as the guarded critical section actually needs
  — acquire it right before the guarded write and release it (via `finally` or
  the lock's context manager) immediately after, including on the exception
  path. Don't hold a long-TTL lock across a step that can fail independently
  (e.g. a DB write) without releasing it in the `except` branch too.

## Security

- Any ZIP/archive extraction MUST go through
  `_assert_zip_members_are_safe`/`_extract_zip_to_codebase_folder` (or an
  equivalent path-traversal guard) before calling `extractall()` — never call
  `zipfile.ZipFile.extractall()` directly on user-supplied archives.
- Any file path built from user-controlled input (filename, project id used in
  a path segment, etc.) must be validated to stay within the intended base
  directory before writing.

## Logging

- `from app.utils.logger import get_logger; logger = get_logger(__name__)` —
  never `print()`. Log task start/success/failure with enough context
  (project_id, source_id, task_id) to trace a run through the pipeline, but
  never log file contents, tokens, or credentials.

## Testing requirement

- Every new/modified task needs a test in the corresponding
  `tests/test_*_tasks.py` covering: success, a redelivered/duplicate run being
  a no-op (idempotency), and the failure path routing through
  `_handle_task_exception`. Never hit a real Celery broker/Postgres/Neo4j —
  see `.claude/rules/tests.md`.

## What NOT to do

- Don't call an async service method directly from a sync Celery task body —
  use `_run_async` (or the task's existing async-bridging helper) to run it.
- Don't add a new task without wiring it into the correct Celery queue/routing
  key in `app/core/celery_app.py` (match the existing per-purpose queue
  convention: routing, parsing, AI generation, notifications).
- Don't call `sys.exit()`/`os._exit()` anywhere in a task body or a service
  method it calls to signal a failure. `SystemExit` is a `BaseException`, not
  an `Exception` — it passes straight through `except Exception` blocks and
  the `_handle_task_exception` pattern and kills the entire worker process
  (including any other in-flight task on it) instead of failing just the one
  task. Known current gap — don't copy: `DocumentService.get_file`
  (`app/services/document_parser_service.py`) does this on an empty S3 read;
  raise a `StorageError`/`ServiceError` instead so the task's normal
  retry/failure path handles it.
- Don't let one step's uncaught exception silently abort an entire Celery
  `chain(...)` of otherwise-independent per-item work (e.g. one chain per
  module/file). If later items in the chain must still run even when an
  earlier one fails, wrap each item's body in its own try/except that routes
  through `_handle_task_exception`/`_mark_status`, rather than letting the
  exception propagate up through the chain primitive.
