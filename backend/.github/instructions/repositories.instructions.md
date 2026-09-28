---
applyTo: "app/repositories/**/*.py"
---

# Repository layer conventions (`app/repositories/{postgres,neo4j}/*.py`)

Repositories are the only layer allowed to write raw SQL/Cypher. They are
data-access only — no business rules, no validation, no HTTP/exception
translation.

## Postgres repositories (`app/repositories/postgres/*.py`)

- Extend `BaseRepository[T]` (`app/repositories/postgres/base_repository.py`)
  for standard CRUD (`get`, `get_all`, `get_paginated`, `add`, `delete`) and add
  resource-specific query methods on top.
- Use SQLAlchemy 2.0 query constructs (`self._session.query(...)` matching the
  existing style in this repo, or `select(...)` — match whatever the sibling
  repositories in the same package already use) — never raw SQL string
  concatenation with user input. Parameterize any raw SQL via SQLAlchemy's
  `text()` with bound parameters if a query construct won't express it.
- **Never call `.commit()`** in a repository — commit/rollback belongs solely
  to `UnitOfWork` (`app/db/unit_of_work.py`). Repository methods may call
  `self._session.flush()` if the caller needs a DB-generated value (e.g. a new
  PK) before the transaction ends, but that's the exception, not the norm.
- Return typed model instances (or `None`/lists thereof) — don't return raw
  SQLAlchemy `Row` tuples or dicts from a repository method.

## Neo4j repositories (`app/repositories/neo4j/*.py`)

- Constructor takes a `Driver` (see `ProjectRepository.__init__`) — never
  construct or cache the driver itself; it's a singleton from
  `app.db.neo4j.get_neo4j_driver()` injected by the caller.
- **Always use parameterized Cypher** (`$param` placeholders passed as keyword
  arguments to `tx.run(cypher, param=value)`) — never f-string/`.format()` a
  Cypher query with any value that originated from user input or request data.
- **Use `MERGE`, not `CREATE`**, for any write that should be idempotent
  (which is virtually all writes here, since Celery tasks may redeliver
  messages). Follow the `coalesce(p.created_at, datetime())` /
  `p.updated_at = datetime()` pattern from `ProjectRepository.upsert_project_node`
  so re-running a write doesn't clobber the original creation timestamp.
- Wrap writes in `session.execute_write(lambda tx: tx.run(...))` and reads in
  `session.execute_read(...)` to get Neo4j's automatic transaction retry
  behavior — don't call `session.run(...)` directly outside a transaction
  function for anything beyond trivial one-off scripts. Known current gap:
  `SourceRepository._execute_write` (`app/repositories/neo4j/source_repository.py`)
  calls `session.run(...)` directly, unlike `_execute_merge_batch`/
  `_execute_delete_source_node` in the same file — don't copy `_execute_write`,
  it loses Neo4j's transient-error retry.
- Accept/return typed models from `app/models/neo4j/*.py`, not loose dicts or
  keyword-arg soup, matching the `ProjectNode` pattern.

## What NOT to do (either kind of repository)

- Don't raise `RIPBaseException` subclasses from a repository — return `None`
  / empty list / raise the underlying driver exception, and let the *service*
  decide what domain-level exception that maps to.
- Don't import from `app/services/**` or `app/routes/**` — repositories sit
  below services in the dependency direction and must not import upward.
- Don't add caching, retries, or business rules (e.g. "only return active
  records") silently — if a query needs a business-rule filter, make it an
  explicit parameter so the service caller decides.
