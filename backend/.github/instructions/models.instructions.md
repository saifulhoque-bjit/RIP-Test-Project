---
applyTo: "app/models/**/*.py"
---

# Model layer conventions (`app/models/{postgres,neo4j}/*.py`)

Models are the data representation only — no business logic, no validation
beyond what the storage engine enforces structurally (types, FKs, not-null).

## Postgres models (`app/models/postgres/*.py`)

- SQLAlchemy 2.0 declarative style: `Mapped[...]` / `mapped_column(...)`
  extending `app.db.base.Base` — don't mix in the legacy `Column(...)` style.
- `id: Mapped[uuid.UUID]` primary key with `default=uuid.uuid4`.
- `created_at`/`updated_at` use `server_default=func.now()` (and
  `onupdate=func.now()` for `updated_at`) so timestamps are set by the
  database, not application code.
- Declare relationships explicitly with `back_populates` (not
  `backref`) and an explicit `lazy=` strategy — default to `lazy="select"`
  only for relationships that are rarely traversed in a list endpoint; use
  `lazy="selectin"` (or an explicit `.options(selectinload(...))` at the
  query site) for anything loaded as part of a list response to avoid N+1
  queries (see `app/models/postgres/project_task_model.py`'s `project`
  relationship as the known N+1 gap — don't copy that pattern for a new
  relationship without eager-loading it from the repository query).
- Every new table needs a matching Alembic migration — never hand-edit an
  already-applied migration under `alembic/versions/`; add a new revision.

## Neo4j models (`app/models/neo4j/*.py`)

- Plain dataclasses/Pydantic models representing graph nodes/relationships —
  no driver/session access, no Cypher, no I/O.
- Field names should mirror the Cypher property names used by the
  corresponding repository (`app/repositories/neo4j/*.py`) exactly, so
  `record["p"]` mapping stays a straight attribute-for-property translation.
- Include `created_at`/`updated_at` fields when the corresponding repository
  writes them via `coalesce(p.created_at, datetime())` / `p.updated_at =
  datetime()` (see `ProjectNode`) so the model doesn't silently drop
  timestamps a caller expects.

## What NOT to do

- Don't add methods to a model that call a repository, service, or session
  (e.g. a `save()`/`refresh()` method) — persistence is the repository's job.
- Don't put response-formatting logic (e.g. computed/derived display fields)
  in a Postgres model — that belongs in the corresponding `*Response` schema.
- Don't add a new Postgres relationship without deciding its `lazy=` strategy
  deliberately — an unreviewed default can turn a list endpoint into an N+1
  query pattern.
