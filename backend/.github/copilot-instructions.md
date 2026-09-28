# RIP Backend — Copilot Instructions

RIP (Requirement Intelligence Platform) is a FastAPI + Celery backend that ingests
PDF/DOCX/image/ZIP source documents, runs a multi-pass LangGraph AI extraction
pipeline, and stores structured requirements in Neo4j (graph) with metadata in
PostgreSQL (relational). Real-time progress is delivered over WebSockets via
Redis pub/sub.

Stack: Python 3.13, FastAPI 0.136, SQLAlchemy 2.0 (sync ORM), Postgres 17 +
pgvector, Neo4j 5.28, Redis, Celery 5.6, AWS (Cognito/S3/SQS via aioboto3),
LangGraph, SlowAPI (rate limiting), Pydantic v2.

## Layered architecture — always follow this flow

```
routes/v1/*.py  →  services/*.py  →  repositories/{postgres,neo4j}/*.py  →  models/{postgres,neo4j}/*.py
```

- **Routes** (`app/routes/v1/`): function-based handlers (NOT class controllers).
  Zero business logic — only request parsing, calling one service method, and
  wrapping the result in `ApiResponse[T]`. Dependencies are resolved via
  `Annotated[X, Depends(...)]` type aliases declared once per module (see
  `app/routes/v1/projects.py` for the canonical pattern) and reused across handlers.
- **Services** (`app/services/`): all business logic and validation lives here.
  Receive a `UnitOfWork` (Postgres) or repository instances — never construct a
  `Session`, open a DB connection, or call `session.commit()` directly.
- **Repositories** (`app/repositories/postgres/*.py`, `app/repositories/neo4j/*.py`):
  the only layer allowed to write raw SQL/Cypher. Postgres repos extend
  `BaseRepository` (`app/repositories/postgres/base_repository.py`) and never call
  `.commit()` — that belongs solely to the `UnitOfWork`. Neo4j repos always use
  parameterized queries (`$param`) and `MERGE` (not `CREATE`) for idempotent writes.
- **Models**: `app/models/postgres/*.py` (SQLAlchemy ORM) and
  `app/models/neo4j/*.py` (plain dataclasses/Pydantic representing graph nodes).

## Sync vs. async

- Postgres access is sync SQLAlchemy (`UnitOfWork`/`Session`). A route or
  service method with no genuine async I/O (no `AsyncUnitOfWork`, no
  awaited Redis/HTTP/Celery-result call) should be plain `def`, not
  `async def` wrapping DB calls in `asyncio.to_thread(...)`. See
  `app/services/notification_service.py` / `app/routes/v1/notifications.py`
  for the reference (sync end-to-end except the post-commit Redis publish),
  and `.github/instructions/services.instructions.md` for the full rule.
- A WebSocket handler (`app/websockets/*.py`) stays `async def` but must
  never call a blocking sync function (JWT/JWKS decode, sync `Session` call)
  directly — wrap it in `starlette.concurrency.run_in_threadpool` (see
  `app/websockets/notification_ws.py`).
- Never drive a shared async client (e.g. a `redis.asyncio` connection
  created once in a manager's `startup()`, or a cached SDK client reused
  across `_run_async` calls) from a fresh `asyncio.run()`/`_run_async()` call
  in a different thread/loop — that mixes event loops unsafely. Use
  `asyncio.run_coroutine_threadsafe(coro, owning_loop)` to call back into
  the loop that owns the client instead, or create the client fresh per call
  if it's cheap. See `NotificationWebSocketManager.publish_threadsafe` and
  `.github/instructions/eventing.instructions.md`/`workers.instructions.md`
  for the reference pattern and a currently-known violation
  (`app/clients/llamaparser_client.py`) — don't copy it.
- A 2026-07 audit found the "keep DB-only methods plain `def`" rule below
  violated in `ProjectService` (converted to sync — every method there is
  genuinely sync end-to-end) and `observability.py` (converted); `auth.py::login`
  needed a narrower fix (`run_in_threadpool` around one sync sub-call, not a
  full conversion, since it also awaits genuine Cognito I/O). **Not**
  `ModuleFeatureService`/`UserStoryService`: every method there awaits a
  Neo4j repository call that internally uses `asyncio.to_thread` — genuine
  async I/O — so they're correctly `async def` and must stay that way. See
  the "Known legacy async/sync gap" and "Neo4j repositories called with
  `await` count as genuine async I/O" notes in
  `.github/instructions/services.instructions.md` before converting anything
  in this area.

## Dependency injection

- All shared dependencies live in `app/deps.py`. Declare `Annotated[Type, Depends(fn)]`
  aliases at the top of each route module (e.g. `CurrentUser = Annotated[User, Depends(get_current_db_user)]`)
  and reuse them — don't repeat `Depends(...)` inline on every handler.
- Request-scoped Postgres access uses `app.db.unit_of_work.UnitOfWork` via
  `get_uow()`; it's a sync context manager — repositories attach to it on
  `__enter__`. Services receive the `uow` instance; they don't create their own.
- Neo4j services should receive their repository via a `deps.py` factory
  (see `get_project_service` for the canonical pattern) rather than instantiating
  `SomeNeo4jRepository(get_neo4j_driver())` inline in the service — this keeps
  services unit-testable with mocks. (Several older services still instantiate
  directly; don't copy that pattern in new code — see
  `.github/instructions/services.instructions.md`.)

## Error handling

- Raise a subclass of `RIPBaseException` (`app/core/exceptions.py`) from services —
  never raise `HTTPException` directly and never let a bare `Exception`/`ValueError`
  propagate out of a service to the route layer.
- Available exceptions: `NotFoundError` (404), `ConflictError` (409),
  `ValidationError` (400), `UnauthorizedError` (401), `ForbiddenError` (403),
  `CognitoError`/`AIServiceError`/`OmniParserClientError`/`StorageError` (502),
  `ServiceError` (500), `ServiceUnavailableError` (503), `AIWorkflowError` (500).
- All exceptions are converted to a standard JSON error response by the
  centralized handlers in `app/core/exception_handlers.py` — do not add
  per-route try/except-to-HTTP mapping.
- User-facing strings are message constants in `app/core/messages.py`
  (`MSG_*`, `SUMMARY_*`, `DESC_*`) — don't hardcode error/summary strings inline.

## API responses

- Every route returns `ApiResponse[T]` (`app/utils/response.py`):
  `ApiResponse.ok(data=..., message=...)`. Use a concrete `T` (e.g.
  `ApiResponse[ProjectResponse]`), never a bare `ApiResponse`.

## Logging

- Use `from app.utils.logger import get_logger; logger = get_logger(__name__)`.
  Never use `print()` for operational output.
- Logs are structured JSON with an automatic `correlation_id` — don't manually
  thread a request/correlation ID through function signatures.
- Never log secrets, tokens, passwords, or PII.

## Background jobs (Celery)

- Task modules live in `app/workers/`. Shared helpers (status updates, retry
  counters, safe ZIP extraction) live in `app/workers/_task_helpers.py` — reuse
  them instead of duplicating status-update/error-handling logic in a new task.
- Tasks must be idempotent (use `MERGE`/status guards) since Celery may redeliver
  messages; queues use `acks_late` with exponential backoff.
- Wrap task bodies so failures route through the existing `_handle_task_exception`
  pattern rather than bare `except Exception: pass`.

## Security guardrails

- Never build a Cypher/SQL query with string formatting/concatenation of
  user input — always use parameterized queries (`$param` in Cypher,
  SQLAlchemy expressions in Postgres).
- Any code that extracts an archive (ZIP) or writes a file to a path derived
  from user input MUST validate the resulting path stays within the intended
  directory (see `_assert_zip_members_are_safe` in `app/workers/_task_helpers.py`
  for the canonical guard against CWE-22 path traversal).
- New upload/auth/AI-generation-triggering (regenerate/generate) endpoints
  must be rate-limited via `app/core/rate_limiter.py`
  (`@limiter.limit(some_limit_var)` + a `request: Request` parameter — see
  `app/routes/v1/auth.py` or `app/routes/v1/sources.py` for the pattern).
  See `.github/instructions/routes.instructions.md` for a currently-known gap
  (module-feature/user-story regenerate endpoints) — don't copy it.
- Never widen `CORS_ORIGINS` to `*` while `allow_credentials=True` is set.
- Secrets/config only via `app.core.config.settings` (Pydantic `BaseSettings`)
  — never hardcode credentials, API keys, or connection strings.
- See `.github/instructions/security-guardrails.instructions.md` for more detail.

## Testing

- Tests live in `tests/`, mirroring `app/` module names (e.g.
  `tests/test_source_service.py` tests `app/services/source_service.py`).
- Never hit a real Postgres/Neo4j/Redis/S3/SQS/Celery broker in a unit test —
  mock the `UnitOfWork`, repositories, and `get_neo4j_driver` (see
  `tests/conftest.py`'s `_no_celery_dispatch` fixture for the standard mocking
  pattern already applied to every test).
- Run tests with `pytest` (not the `runTests` tool, which has had discovery
  issues in this repo — prefer `python -m pytest tests/<file> -v` in a terminal
  with the venv activated).
- Lint with `ruff check app tests` (config in `pyproject.toml`); this repo
  targets Python 3.13, double quotes, 100-char lines (E501 ignored).

## Naming conventions

- Message/summary/description constants: `MSG_*`, `SUMMARY_*`, `DESC_*` in
  `app/core/messages.py`, grouped by resource with a `# ── Resource ──` header.
- Settings: `SCREAMING_SNAKE_CASE` in `app/core/config.py`, grouped by concern
  with a comment header (e.g. `# ── Rate limiting ──`).
- Route modules, service classes, and repository classes follow
  `<Resource>Service` / `<Resource>Repository` naming exactly matching the
  resource they operate on (e.g. `SourceService`, `SourceRepository`).

## AI-assisted development & cost optimization

This repo is developed with heavy Copilot/LLM assistance — token and credit
cost is a first-class concern:

- Read existing code first; make the smallest correct diff instead of
  regenerating a whole file.
- Reuse existing services/repositories/schemas/utilities (see layer
  instructions below) instead of parallel implementations.
- Full checklist: `.github/skills/copilot-cost-optimization/SKILL.md`.
  Always-on enforced rules: `.github/instructions/ai-cost-guardrails.instructions.md`.

## Out of scope / do not touch without explicit request

- `Jenkinsfile.*` (CI/CD pipelines) — managed separately, explicitly out of scope.
- Do not set up MCP servers unless explicitly asked.
- Do not modify `alembic/versions/*` migrations that have already been applied;
  create a new migration instead.

## Layer-specific instructions

More detailed, auto-applied rules exist for each layer in `.github/instructions/`:
`routes.instructions.md`, `services.instructions.md`, `repositories.instructions.md`,
`schemas.instructions.md`, `models.instructions.md`, `eventing.instructions.md`,
`workers.instructions.md`, `tests.instructions.md`, `security-guardrails.instructions.md`,
`ai-cost-guardrails.instructions.md`.
