---
mode: agent
description: Scaffold a new CRUD resource end-to-end (model, schema, repository, service, route, tests) following this repo's layered architecture.
---

# Add a new CRUD resource

You are scaffolding a brand-new resource in the RIP backend, following the
exact layered pattern already used by the `Project` resource
(`app/models/postgres/project_model.py`, `app/schemas/project_schema.py`,
`app/repositories/postgres/project_repository.py`,
`app/services/project_service.py`, `app/routes/v1/projects.py`,
`tests/test_project_service.py`, `tests/test_project_routes.py`). Read those
five files first to mirror their exact style before writing anything.

Ask the user (if not already provided) for:
1. The resource name (singular, e.g. `Requirement`) and its plural route
   prefix (e.g. `/requirements`).
2. The fields it needs, with types and whether each is required.
3. Any parent resource it belongs to (e.g. scoped to a `Project`, like most
   resources in this repo).
4. Whether it needs a Neo4j graph representation in addition to Postgres, or
   Postgres only.

## Build order (routes → services → repositories → models is the *call* flow;
## build bottom-up so each layer's dependency already exists)

### 1. Model — `app/models/postgres/<resource>_model.py`
- SQLAlchemy 2.0 declarative model extending `app.db.base.Base`.
- `id: Mapped[uuid.UUID]` primary key with `default=uuid.uuid4`.
- `created_at`/`updated_at` timestamp columns with `server_default=func.now()`
  (match `Project`'s exact column definitions).
- FK to parent resource if scoped (e.g. `project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))`).
- Add a new Alembic migration for the table — do NOT hand-edit an existing
  applied migration in `alembic/versions/`.

### 2. Schemas — `app/schemas/<resource>_schema.py`
- `<Resource>Create` (`extra="forbid"`, all required fields for creation),
  `<Resource>Update` (all fields `| None = None`, partial update),
  `<Resource>Response` (read representation, `model_config = ConfigDict(from_attributes=True)`),
  `<Resource>ListResponse` (paginated wrapper — reuse the pagination envelope
  shape from `ProjectListResponse`).

### 3. Repository — `app/repositories/postgres/<resource>_repository.py`
- Extend `BaseRepository[<Resource>]` from
  `app/repositories/postgres/base_repository.py` for generic CRUD.
- Add resource-specific lookups (`get_by_uuid`, `list_by_project`, etc.) using
  SQLAlchemy query methods — never raw SQL string concatenation.
- Never call `.commit()` — only `.flush()` if a DB-generated value is needed
  before the transaction ends.
- Register the new repository on `UnitOfWork.__enter__` in
  `app/db/unit_of_work.py` (add both the import and the
  `self.<resource_plural> = <Resource>Repository(self._session)` line,
  following the existing entries).

### 4. Service — `app/services/<resource>_service.py`
- Class `<Resource>Service`. Methods receive `uow: UnitOfWork` as a parameter
  (don't construct one internally for request-scoped calls).
- Validate existence/ownership/business rules here; raise
  `NotFoundError`/`ConflictError`/`ValidationError`/`ForbiddenError` from
  `app.core.exceptions` using `MSG_*` constants — add any new constants needed
  to `app/core/messages.py` under a `# ── <Resource> ──` header.
- If this resource also needs a Neo4j representation, add a
  `Neo4j<Resource>Repository` under `app/repositories/neo4j/` and inject it via
  a new `get_<resource>_service` factory in `app/deps.py`
  (mirror `get_project_service` — do NOT instantiate the Neo4j repo inline in
  the service).

### 5. Route — `app/routes/v1/<resource_plural>.py`
- `APIRouter(prefix="/<resource_plural>", tags=["<Resources>"])` with a module
  docstring listing the endpoint summary + design rules (copy the format from
  `app/routes/v1/projects.py`).
- Declare `Annotated[Type, Depends(fn)]` aliases once at module level
  (`CurrentUser`, `CurrentUow`, etc.) and reuse across handlers.
- Every handler returns `ApiResponse[<Resource>Response]` (or
  `ApiResponse[<Resource>ListResponse]` for list endpoints) — never a bare
  `ApiResponse`.
- Zero business logic in the handler — one service call, then
  `ApiResponse.ok(data=..., message=MSG_*)`.
- If this is an upload/expensive endpoint, add rate limiting per
  `.github/instructions/routes.instructions.md`.
- Register the new router in `app/router.py` alongside the other `v1` routers.

### 6. Tests
- `tests/test_<resource>_service.py` — mock `UnitOfWork`/repositories, cover
  success + not-found + validation-failure paths for every service method.
- `tests/test_<resource>_routes.py` — use FastAPI's `TestClient`/dependency
  overrides to assert routes call the service and wrap results in
  `ApiResponse[T]` correctly (mirror `tests/test_project_routes.py`).
- If a repository has non-trivial queries, add
  `tests/test_<resource>_repository.py`.
- Follow `.github/instructions/tests.instructions.md` for mocking conventions
  (especially: never hit a real DB/Neo4j/Redis; explicitly set every mock
  attribute the code under test touches).

## After scaffolding

Run, in order:
1. `python -m pytest tests/test_<resource>_service.py tests/test_<resource>_routes.py -v`
2. `ruff check app/models/postgres/<resource>_model.py app/schemas/<resource>_schema.py app/repositories/postgres/<resource>_repository.py app/services/<resource>_service.py app/routes/v1/<resource_plural>.py tests/test_<resource>_*.py`
3. The full suite (`pytest`) to confirm no regressions in `UnitOfWork` wiring
   or `router.py` registration.

Report back: files created, migration generated (if any), and test results.
