---
paths:
  - "app/routes/**/*.py"
---

# Route handler conventions (`app/routes/v1/*.py`)

Routes are thin HTTP adapters. They parse the request, call exactly one
service method, and shape the response — nothing else.

## Structure

- Function-based handlers registered on an `APIRouter(prefix="/resource", tags=[...])`
  — never class-based controllers.
- Declare `Annotated[Type, Depends(fn)]` aliases once at module level (after the
  imports, before the handlers) and reuse them across every handler in the file.
  Example (see `app/routes/v1/projects.py`):
  ```python
  CurrentUser = Annotated[User, Depends(get_current_db_user)]
  CurrentUow  = Annotated[UnitOfWork, Depends(get_uow)]
  ```
  Do not write `Depends(get_current_db_user)` inline on more than one handler.
- Every handler returns `ApiResponse[T]` with a concrete `T`
  (`ApiResponse[ProjectResponse]`, `ApiResponse[SourceListResponse]`, ...) — never
  a bare `ApiResponse` or a raw Pydantic schema.
- Use `ApiResponse.ok(data=..., message=MSG_*)`. Reuse existing `SUMMARY_*` /
  `MSG_*` constants from `app/core/messages.py` for the `summary=` on the route
  decorator and the response `message=` — don't inline literal strings.
- A handler only needs to be `async def` if it does genuine async I/O itself
  or awaits an async dependency/service method. If the service it calls is
  sync (see `.claude/rules/services.md` — most Postgres-only services are),
  declare the handler as plain `def`; FastAPI runs it in its threadpool
  automatically. Compare `app/routes/v1/notifications.py`/`app/routes/v1/users.py`
  (sync, DB-only) against an endpoint that genuinely awaits Neo4j/S3/Cognito I/O.

## Zero business logic

- No validation, branching on business state, DB queries, or Neo4j/S3 calls in
  a route handler. If you're tempted to add an `if`/`try` beyond simple request
  shaping, that logic belongs in the service.
- Catch nothing here — let `RIPBaseException` subclasses raised by the service
  propagate; the centralized handlers in `app/core/exception_handlers.py`
  convert them to the standard JSON error envelope. Never wrap a call in
  `try/except` just to re-raise `HTTPException`.

## Rate limiting

- Any new upload, download, auth, AI-generation-triggering (regenerate,
  generate, reprocess — anything that enqueues an LLM-backed Celery job), or
  otherwise expensive/abuse-prone endpoint MUST be rate-limited. Pattern (see
  `app/routes/v1/auth.py`, `app/routes/v1/sources.py`, and the
  `ai_regenerate_limit`-decorated endpoints in `app/routes/v1/module_features.py`
  / `app/routes/v1/user_stories.py`):
  ```python
  from app.core.rate_limiter import limiter, some_limit_var

  @router.post(...)
  @limiter.limit(some_limit_var)
  async def handler(request: Request, ...):
  ```
  `@limiter.limit(...)` must sit directly below the `@router.*(...)` decorator
  and above `async def`, and the handler MUST declare a `request: Request`
  parameter (SlowAPI reads the request from the signature). Add new limit
  strings to `app/core/config.py` (`RATE_LIMIT_*`) and
  `app/core/rate_limiter.py`, not as inline literals.

## Pagination & query params

- Use `app.utils.pagination.PaginationParams` for list endpoints rather than
  hand-rolling `skip`/`limit` query params.
- Bound optional query params directly in the signature
  (`Annotated[int, Query(ge=1, le=500)]`) instead of manually clamping values
  inside the handler body.

## Testing requirement

- Every new/modified route handler needs a matching test in
  `tests/test_<resource>_routes.py` asserting: the service is called with the
  parsed request data, the response is wrapped in `ApiResponse[T]`, and (for
  a rate-limited endpoint) that the limiter decorator is present. See
  `.claude/rules/tests.md`.

## What NOT to do

- Don't instantiate a repository or `UnitOfWork` directly in a route — always
  go through the `Depends(get_uow)` / service-factory dependency.
- Don't import from `app/repositories/**` in a route module.
- Don't add a new route module without a module docstring listing the endpoint
  summary and design rules (see the header of `app/routes/v1/auth.py` or
  `app/routes/v1/projects.py` for the expected format).
