---
applyTo: "app/websockets/**/*.py,app/messaging/**/*.py"
---

# Real-time & messaging conventions (`app/websockets/*.py`, `app/messaging/*.py`)

This layer delivers progress/notification events to clients (WebSockets over
Redis pub/sub) and exchanges messages with external systems (SQS
producer/consumer, project publisher). Treat every inbound/outbound message
as untrusted and every delivery as best-effort, not guaranteed.

## WebSockets (`app/websockets/*.py`)

- Never reuse an async client/connection created on one event loop (e.g. a
  `redis.asyncio` client instantiated once in a manager's `startup()`, called
  from `app/core/lifespan.py`) from a different event loop or thread. A sync
  service method that publishes to it may run in FastAPI's worker threadpool
  (see `.github/instructions/services.instructions.md`); bridging that call
  with a fresh `asyncio.run()`/`_run_async` would spin up an unrelated loop
  and drive the shared client from it, which is unsafe and can raise
  `RuntimeError: ... attached to a different loop` or silently corrupt the
  connection under concurrent load. Instead, capture the owning loop
  (`asyncio.get_running_loop()` inside `startup()`) and expose a sync,
  thread-safe entry point that calls back into it via
  `asyncio.run_coroutine_threadsafe(coro, owning_loop).result(timeout=...)` —
  see `NotificationWebSocketManager.publish_threadsafe` in
  `app/websockets/notification_manager.py` for the reference implementation.
  Only fall back to a fresh one-off loop + ephemeral connection when there is
  no shared loop to call back into at all (e.g. a Celery worker process that
  never runs the FastAPI lifespan).
- Broadcast to multiple connections concurrently (`asyncio.gather(...)` with
  `return_exceptions=True` and a per-send timeout) rather than sequentially —
  one slow/dead client must not block delivery to the others (see
  `app/websockets/manager.py::broadcast` as the known sequential-send gap;
  don't copy that pattern into a new manager — copy
  `app/websockets/notification_manager.py::broadcast`/`_safe_send` instead,
  which fans out with `asyncio.gather` + a per-send `asyncio.wait_for` timeout
  and is the corrected reference implementation).
  Isolate the failure to that one connection: log it, drop/close the
  connection, and continue.
- A WebSocket handler is still `async def` and must never call a blocking
  sync function directly (JWT/JWKS decode, a sync SQLAlchemy `Session` call,
  etc.) — wrap it in `starlette.concurrency.run_in_threadpool` so it doesn't
  stall the event loop for every other concurrent connection/request (see
  `app/websockets/notification_ws.py::_authenticate`/`_resolve_user_id` for
  the pattern). Prefer this over reaching for `asyncio.to_thread` on a
  service method — see `.github/instructions/services.instructions.md` for
  why service methods with no genuine async I/O should just be `def`.
- Never trust a client-supplied `project_id`/`source_id`/room name at the
  connection handshake without checking the authenticated user actually has
  access to that resource — validate the same way the corresponding REST
  route would (reuse the service's ownership/role check, don't re-implement
  it). Known current gap: `app/websockets/source_ws.py`'s
  `/ws/projects/{project_id}` handshake only checks the project *exists*, not
  that the caller owns/has a role on it (contrast with
  `app/routes/v1/projects.py`'s `GET /projects/{id}`, which passes
  `requester_id`/`requester_roles` into the ownership check) — any
  authenticated user can currently subscribe to any other user's/tenant's
  project progress stream. Don't copy this into a new WS handler; fix by
  reusing the same ownership check the REST route performs before
  `manager.connect`.
- Keep payloads small and JSON-serializable primitives only (status enums,
  ids, short messages) — never push large file contents or raw AI
  generations over a WebSocket message.

## Messaging (`app/messaging/*.py`)

- SQS producer/consumer messages must be small, versioned JSON payloads
  (ids + status/type, not full documents) — the receiver re-fetches full data
  from Postgres/Neo4j/S3 by id rather than trusting the message body as the
  source of truth.
- Consumers must be idempotent (the same message may be redelivered at-least-once):
  guard state transitions the same way Celery tasks do (see
  `.github/instructions/workers.instructions.md`), and treat a duplicate
  message as a no-op, not an error.
- Any per-message handler must have an explicit timeout and a bounded retry
  count; unhandled/poison messages must go to a DLQ (or be logged and acked)
  rather than being retried forever or silently dropped.
- Never log full message bodies if they may contain PII or large payload
  content — log ids, message type, and outcome only.

## What NOT to do

- Don't perform business logic or direct DB/Neo4j writes inside a WebSocket
  handler or SQS consumer callback — delegate to the existing service layer
  method for that resource, matching the REST route's behavior for the same
  operation.
- Don't add a new pub/sub channel or SQS queue name as a string literal
  scattered across modules — define it once (matching how existing
  channel/queue names are centralized) and import it.
- Don't swallow a delivery/connection error silently — log it with enough
  context (connection/user id, message id) to trace a missed notification.
