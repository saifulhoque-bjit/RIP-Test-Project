# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

RIP (Requirement Intelligence Platform) — a FastAPI + Celery backend that ingests PDF/DOCX/CSV/XLSX/image/ZIP source documents, runs multi-pass LangGraph generator→critic AI pipelines to derive a Module → Feature → User Story backlog, and stores the graph in Neo4j (relational metadata in PostgreSQL). Real-time progress and notifications are delivered over WebSockets via Redis pub/sub. Multi-tenant RBAC, per-project membership, and outbound sync to Jira/TAP round out the platform.

Stack: Python 3.13, FastAPI 0.136, SQLAlchemy 2.0 (sync ORM), PostgreSQL 17 + pgvector, Neo4j 5.26 + APOC, Redis 7.4, Celery 5.6, AWS (Cognito/S3/SQS via aioboto3), LangGraph, SlowAPI, Pydantic v2.

The **README.md** in the repo root is the authoritative architecture reference — project structure, Neo4j graph schema, AI pipeline diagrams, Celery queue/worker topology, the full endpoint table, and every environment variable are documented there in depth. Read it before making non-trivial changes; this file covers what the README doesn't: enforced coding rules and day-to-day commands.

## Commands

```bash
# Run the full test suite (no external services needed — everything is mocked)
python -m pytest -q

# Run one test file / a single test
python -m pytest tests/test_source_service.py -v
python -m pytest tests/test_source_service.py::TestClassName::test_name -v

# Coverage
python -m pytest --cov=app --cov-report=term-missing

# Lint / format (config in pyproject.toml — py313, double quotes, 100-char lines, E501 ignored)
ruff check app tests
ruff format app tests

# Alembic migrations
alembic revision --autogenerate -m "short description"
alembic upgrade head
alembic downgrade -1

# Run locally (Postgres/Neo4j/Redis via `docker compose up postgres neo4j redis -d`)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
celery -A app.core.celery_app.celery_app worker --loglevel=info --concurrency=4

# Or everything via Docker Compose
docker compose up --build
```

Do **not** use the `runTests` tool — it has had discovery issues in this repo; use `pytest` directly in a terminal with the venv activated.

## Layered architecture — always follow this flow

```
routes/v1/*.py  →  services/*.py  →  repositories/{postgres,neo4j}/*.py  →  models/{postgres,neo4j}/*.py
```

- **Routes** (`app/routes/v1/`): function-based handlers, not class controllers (see README's "Why routes/v1 Instead of Controllers" for the rationale). Zero business logic — only request parsing, one service call, and wrapping the result in `ApiResponse[T]`. `Annotated[X, Depends(...)]` aliases are declared once per module and reused across handlers (see `app/routes/v1/projects.py` for the canonical pattern).
- **Services** (`app/services/`): all business logic and validation. Receive a `UnitOfWork` (Postgres) or repository instances — never construct a `Session`, open a DB connection, or call `session.commit()` directly.
- **Repositories** (`app/repositories/postgres/`, `app/repositories/neo4j/`): the only layer allowed to write raw SQL/Cypher. Postgres repos extend `BaseRepository` and never call `.commit()` — that belongs solely to `UnitOfWork`. Neo4j repos always use parameterized queries (`$param`) and `MERGE` (not `CREATE`) for idempotent writes — the sole exception is version-snapshot writes (`ModuleVersion`/`FeatureVersion`/`UserStoryVersion`), which intentionally `CREATE` a new immutable node per change.
- **Models**: `app/models/postgres/*.py` (SQLAlchemy ORM) and `app/models/neo4j/*.py` (plain dataclasses/Pydantic representing graph nodes).

## Sync vs. async

- Postgres access is sync SQLAlchemy (`UnitOfWork`/`Session`). A route or service method with no genuine async I/O (no `AsyncUnitOfWork`, no awaited Redis/HTTP/Celery-result call) should be plain `def`, not `async def` wrapping DB calls in `asyncio.to_thread(...)`. Reference: `app/services/notification_service.py` / `app/routes/v1/notifications.py` (sync end-to-end except the post-commit Redis publish). Full rule: `.claude/rules/services.md`.
- Neo4j repositories called with `await` count as genuine async I/O (they internally use `asyncio.to_thread`) — so `ModuleFeatureService`/`UserStoryService` are correctly `async def` throughout and must stay that way. Don't "fix" them to sync.
- A WebSocket handler (`app/websockets/*.py`) stays `async def` but must never call a blocking sync function (JWT/JWKS decode, sync `Session` call) directly — wrap it in `starlette.concurrency.run_in_threadpool` (see `app/websockets/notification_ws.py`).
- Never drive a shared async client (e.g. a `redis.asyncio` connection created once in a manager's `startup()`) from a fresh `asyncio.run()`/`_run_async()` call in a different thread/loop — that mixes event loops unsafely. Use `asyncio.run_coroutine_threadsafe(coro, owning_loop)` instead, or create the client fresh per call if cheap. Reference: `NotificationWebSocketManager.publish_threadsafe`. `app/clients/llamaparser_client.py` is a currently-known violation — don't copy it.

## Dependency injection

- Shared dependencies live in `app/deps.py`. Declare `Annotated[Type, Depends(fn)]` aliases at the top of each route module and reuse them — don't repeat `Depends(...)` inline on every handler.
- Request-scoped Postgres access uses `app.db.unit_of_work.UnitOfWork` via `get_uow()` — a sync context manager; repositories attach to it on `__enter__`. Services receive the `uow` instance; they don't create their own.
- Neo4j services should receive their repository via a `deps.py` factory (see `get_project_service`) rather than instantiating a repository inline in the service — keeps services unit-testable with mocks. Several older services still instantiate directly; don't copy that in new code.

## Error handling

- Raise a subclass of `RIPBaseException` (`app/core/exceptions.py`) from services — never raise `HTTPException` directly and never let a bare `Exception`/`ValueError` propagate out of a service to the route layer.
- Available exceptions: `NotFoundError` (404), `ConflictError` (409), `ValidationError` (400), `UnauthorizedError` (401), `ForbiddenError` (403), `CognitoError`/`AIServiceError`/`OmniParserClientError`/`StorageError` (502), `ServiceError` (500), `ServiceUnavailableError` (503), `AIWorkflowError` (500).
- All exceptions convert to a standard JSON error response via the centralized handlers in `app/core/exception_handlers.py` — don't add per-route try/except-to-HTTP mapping.
- User-facing strings are constants in `app/core/messages.py` (`MSG_*`, `SUMMARY_*`, `DESC_*`) — don't hardcode error/summary strings inline.

## API responses

Every route returns `ApiResponse[T]` (`app/utils/response.py`) via `ApiResponse.ok(data=..., message=...)`. Always use a concrete `T` (e.g. `ApiResponse[ProjectResponse]`), never a bare `ApiResponse`.

## Logging

- Use `from app.utils.logger import get_logger; logger = get_logger(__name__)`. Never `print()` for operational output.
- `get_logger` returns a stdlib `logging.Logger` — use `%`-style formatting (`logger.info("x=%s", x)`), not keyword arguments; passing kwargs raises `TypeError`.
- Logs are structured JSON with an automatic `correlation_id` — don't manually thread a request/correlation ID through function signatures.
- Never log secrets, tokens, passwords, or PII.

## Background jobs (Celery)

- Task modules live in `app/workers/`; shared helpers (`_mark_status`, `emit_task_event`, `_run_async`, safe ZIP extraction) live in `app/workers/_task_helpers.py` — reuse them instead of duplicating status-update/error-handling logic in a new task.
- Tasks must be idempotent (`MERGE`/status guards) since Celery may redeliver messages; queues use `acks_late` with exponential backoff (60s → 120s → 240s, 3 retries).
- Wrap task bodies so failures route through the existing exception-handling pattern rather than bare `except Exception: pass`.
- Task cancellation is cooperative (Redis flag keyed by `request_id`, not `terminate=True`) because production runs the `-P threads` pool — see README's "Task cancellation" section and `app/core/task_control.py` before touching anything in a long-running pipeline's checkpoint logic.
- New task types need an entry in `task_routes` (`app/core/celery_app.py`) to land on a dedicated queue — `tasks.sync_to_jira` is a known example that fell through to the default `celery` queue by omission; don't repeat that mistake.

## Security guardrails

- Never build a Cypher/SQL query with string formatting/concatenation of user input — always parameterized queries (`$param` in Cypher, SQLAlchemy expressions in Postgres).
- Any code that extracts an archive (ZIP) or writes a file to a path derived from user input MUST validate the resulting path stays within the intended directory (see `_assert_zip_members_are_safe` in `app/workers/_task_helpers.py` for the canonical CWE-22 path-traversal guard).
- New upload/auth/AI-generation-triggering (regenerate/generate) endpoints must be rate-limited via `app/core/rate_limiter.py` (`@limiter.limit(some_limit_var)` + a `request: Request` parameter — see `app/routes/v1/auth.py` or `app/routes/v1/sources.py`).
- Never widen `CORS_ORIGINS` to `*` while `allow_credentials=True` is set.
- Secrets/config only via `app.core.config.settings` (Pydantic `BaseSettings`) — never hardcode credentials, API keys, or connection strings.
- More detail: `.claude/rules/security-guardrails.md`.

## Testing is not optional

**Every code change ships with tests in the same change.** Writing or
modifying a route handler, service method, repository method, worker task,
Neo4j model/repository, or schema validator without also adding/updating the
corresponding unit test in `tests/` is an incomplete change, not a smaller
one — do not defer tests to a follow-up, and do not report a task as done
without having actually run the affected test file(s). A bug fix needs a
regression test that fails on the pre-fix code. Full conventions (mocking,
naming, required coverage per layer): `.claude/rules/tests.md`.

- Tests live in `tests/`, mirroring `app/` module names (e.g. `tests/test_source_service.py` tests `app/services/source_service.py`).
- Never hit a real Postgres/Neo4j/Redis/S3/SQS/Celery broker in a unit test — mock the `UnitOfWork`, repositories, and `get_neo4j_driver` (see `tests/conftest.py`'s `_no_celery_dispatch` fixture, applied to every test).
- Run the affected test file(s) while iterating; run the full suite (`python -m pytest -q`) before declaring any change complete.

## Naming conventions

- Message/summary/description constants: `MSG_*`, `SUMMARY_*`, `DESC_*` in `app/core/messages.py`, grouped by resource with a `# ── Resource ──` header.
- Settings: `SCREAMING_SNAKE_CASE` in `app/core/config.py`, grouped by concern with a comment header.
- Route modules, service classes, and repository classes follow `<Resource>Service` / `<Resource>Repository` naming exactly matching the resource they operate on.

## AI-assisted development & cost optimization

This repo is developed with heavy AI-assistant usage (Claude Code and GitHub Copilot) — token and credit cost is a first-class concern:
- Read existing code first; make the smallest correct diff instead of regenerating a whole file.
- Reuse existing services/repositories/schemas/utilities instead of parallel implementations.
- Full checklist: `.claude/skills/ai-cost-optimization/SKILL.md`. Always-on rules: `.claude/rules/ai-cost-guardrails.md`.

## Out of scope / do not touch without explicit request

- `Jenkinsfile.*` (CI/CD pipelines) — managed separately.
- Do not set up MCP servers unless explicitly asked.
- Do not modify `alembic/versions/*` migrations that have already been applied — create a new migration instead.
- PostgreSQL enum columns (e.g. `sources.status`) cannot have values dropped once added — only appended. Plan enum changes as additive.

## Layer-specific instructions

More detailed, auto-applied rules exist per layer in `.claude/rules/` (glob-scoped via `paths:` frontmatter — Claude Code loads them automatically when you read/edit a matching file): `routes.md`, `services.md`, `repositories.md`, `schemas.md`, `models.md`, `eventing.md`, `workers.md`, `tests.md`, `security-guardrails.md`, `ai-cost-guardrails.md`. Consult the relevant one before making a non-trivial change in that layer. These mirror (and where the underlying code has since changed, correct) the equivalent `.github/instructions/*.instructions.md` files used by GitHub Copilot in this repo — the two AI assistants share one set of architectural rules; if you update one side's substance, update the other to match.

Reusable how-to skills (invoke via the Skill tool, or they auto-trigger by description): `.claude/skills/ai-cost-optimization`, `.claude/skills/dual-store-consistency`, `.claude/skills/langgraph-ai-pipeline`, plus the pre-existing `.claude/skills/fastapi`. A `/new-crud-resource` slash command (`.claude/commands/new-crud-resource.md`) scaffolds a full new resource end-to-end (model → schema → repository → service → route → tests) following the `Project` resource's pattern.
