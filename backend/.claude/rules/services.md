---
paths:
  - "app/services/**/*.py"
---

# Service layer conventions (`app/services/*.py`)

Services own all business logic, validation, and orchestration across
repositories. They are the only layer routes are allowed to call directly.

## Sync vs. async methods

- This repo's Postgres ORM is sync SQLAlchemy (`UnitOfWork`/`Session`, not
  `AsyncSession`). A service method that only does Postgres reads/writes has
  no genuine async I/O to benefit from `async def` — keep it plain `def`, and
  let the calling route be plain `def` too (FastAPI runs sync `def` handlers
  in its threadpool automatically). See `app/services/notification_service.py`
  and `app/routes/v1/notifications.py`/`app/routes/v1/users.py` as the
  reference: every DB-only method there is sync.
- **Don't** wrap a purely synchronous DB call in `asyncio.to_thread(...)`
  just to make a service method `async def` — that adds a thread-hop per call
  for no benefit and makes the method's true (lack of) I/O profile harder to
  read at the call site.
- Only make a method `async def` when it does genuine async I/O directly
  (an `await`-based Redis/HTTP/Celery-result client, an `AsyncUnitOfWork`
  query via `app/db/async_unit_of_work.py`). If a sync method needs to call
  into that kind of async code (e.g. publishing a WebSocket/Redis event after
  a DB commit), bridge it with `app.workers._task_helpers._run_async` — but
  **only** when the async call opens/owns its own connection for that one
  call (no shared client created elsewhere). `_run_async`/`asyncio.run()`
  spins up a brand-new event loop in the calling thread; if the async code
  instead reuses a client/connection that was created on a *different*,
  already-running event loop (e.g. a `redis.asyncio` client created once in
  `app/core/lifespan.py`'s `startup()`), you must NOT drive it via a fresh
  `asyncio.run()` — that silently mixes event loops, which `redis.asyncio`
  (and asyncio in general) does not support safely. Instead capture the
  owning loop where the client was created and call back into it with
  `asyncio.run_coroutine_threadsafe(coro, owning_loop).result(timeout=...)`.
  See `NotificationWebSocketManager.publish_threadsafe`
  (`app/websockets/notification_manager.py`) for the reference implementation
  of this pattern, and `NotificationService._publish_event`
  (`app/services/notification_service.py`) for how a sync service method
  calls it. Don't invent a new `asyncio.to_thread` call site either way.
  Known current gap — don't copy: `app/clients/llamaparser_client.py` caches
  a module-level `AsyncLlamaCloud` singleton reused across many `_run_async`
  calls in `app/workers/document_task_stages.py` / `app/workers/incremental_task.py`,
  which is exactly this unsafe pattern.

## Dependency injection

- Receive a `UnitOfWork` (Postgres) instance as a method/constructor parameter
  from the caller (route or another service) — never construct
  `UnitOfWork()` or a raw `Session` inside a service method for request-scoped
  work. (Celery task bodies are the one place a fresh `with UnitOfWork() as uow:`
  is expected, since there's no request-scoped instance to inject.)
- For Neo4j access, prefer receiving the repository via a `deps.py` factory
  (see `get_project_service` in `app/deps.py` for the canonical pattern):
  ```python
  def get_project_service(
      repo: Annotated[Neo4jProjectRepository, Depends(get_neo4j_project_repo)],
  ) -> ProjectService:
      return ProjectService(repo)
  ```
  Several older services still instantiate `SomeNeo4jRepository(get_neo4j_driver())`
  directly inline — this is a known gap, not a pattern to copy. New services
  must use the injected-repository pattern so they stay unit-testable with mocks.
- A service method called from a request path (even indirectly, from another
  service) must not open its own `with UnitOfWork() as uow:` — that carve-out
  is for Celery task bodies only. Pass the caller's `uow` through instead.
- Never call `uow.commit()` implicitly relying on side effects elsewhere —
  either let the `UnitOfWork` context manager auto-commit on clean exit, or
  call `uow.commit()` explicitly before triggering an external side-effect
  (Celery dispatch, S3 call, webhook) that depends on the Postgres write being
  durable first. See the docstring in `app/db/unit_of_work.py` for the full rule.

## Neo4j repositories called with `await` count as genuine async I/O

`ModuleFeatureRepository`/`UserStoryRepository` (`app/repositories/neo4j/*.py`)
wrap their sync Neo4j driver calls in `asyncio.to_thread(...)` internally —
so a service method that `await`s one of these repo methods (or
`asyncio.gather`s several) genuinely benefits from `async def`, even though
the underlying Neo4j driver itself is sync. Every public method on
`ModuleFeatureService` and `UserStoryService` does this — they are correctly
`async def` end-to-end and must stay that way; don't "fix" them to plain
`def`, which would remove the `asyncio.to_thread` offloading and the real
`asyncio.gather` concurrency several methods rely on. Contrast with
`Neo4jProjectRepository.get_user_story_counts`, which is plain sync `def`
with no `to_thread` wrapping — that's why `ProjectService` is different and
correctly sync.

**Before converting any service's sync/async style, verify per-method
whether it awaits genuine async I/O** — don't assume every `async def`
service with a DB call is guilty of unnecessary async.

## Error handling

- Raise a specific `RIPBaseException` subclass from `app/core/exceptions.py`
  for every foreseeable failure (`NotFoundError`, `ConflictError`,
  `ValidationError`, `ForbiddenError`, `StorageError`, `AIServiceError`, etc.) —
  never raise bare `Exception`, `ValueError`, or `HTTPException` from a service.
- Only use a broad `except Exception` when wrapping a genuinely unpredictable
  external call (S3, Cognito, an LLM provider) and immediately re-raise as a
  specific `RIPBaseException` (e.g. `StorageError`, `AIServiceError`) with a
  useful message — never swallow the exception silently (`except Exception: pass`).
- Never call `sys.exit()`/`os._exit()` to signal a failure from a service
  method — `SystemExit` is a `BaseException`, not an `Exception`; it passes
  straight through `except Exception` blocks and kills the entire worker/app
  process instead of just failing the one request/task. Known current gap —
  don't copy: `DocumentService.get_file` (`app/services/document_parser_service.py`)
  does `sys.exit(1)` on an empty S3 read; raise `StorageError`/`ServiceError`
  instead.
- Use message constants (`MSG_*`) from `app/core/messages.py`, formatted with
  `.format(...)` — don't hardcode error strings inline.

## Validation

- Validate all business-level constraints in the service (existence checks,
  ownership/role checks, size/count limits, MIME/type checks) — routes only do
  request *shape* validation via Pydantic schemas.
- When adding a new resource/count/size limit, add the threshold to
  `app/core/config.py` as a named `Field(default=...)` setting rather than a
  magic number in the service, and add a matching `MSG_*` constant for the
  rejection message.

## Logging

- `from app.utils.logger import get_logger; logger = get_logger(__name__)` at
  module level. Log with `logger.info/warning/error(...)`, never `print()`.
- Never log secrets, tokens, passwords, or raw PII (emails are generally OK if
  already logged elsewhere in the codebase for this resource — check existing
  patterns in the same service file first).

## Testing requirement

- Every new/modified service method needs a matching test in
  `tests/test_<resource>_service.py` covering the success path, and every
  distinct failure branch (`NotFoundError`/`ConflictError`/`ValidationError`/
  etc.) it can raise. See `.claude/rules/tests.md`.

## What NOT to do

- Don't return SQLAlchemy ORM models directly to the route layer if a Pydantic
  response schema exists for the resource — build/return the schema (or let the
  route's response_model validate the ORM object, matching the existing
  resource's pattern).
- Don't put raw SQL or Cypher strings in a service — that belongs in a
  repository method.
- Don't add a new external I/O call (S3, Cognito, LLM, HTTP) without wrapping
  it so failures become a specific `RIPBaseException`.
