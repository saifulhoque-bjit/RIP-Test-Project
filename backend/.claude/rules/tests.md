---
paths:
  - "tests/**/*.py"
---

# Test conventions (`tests/*.py`)

See the root `CLAUDE.md` "Testing is not optional" section for the
project-wide rule that every code change ships with tests in the same
change — this file covers *how* to write them.

## Structure & naming

- One test file per source module, mirroring the `app/` path:
  `tests/test_<module>.py` tests `app/**/<module>.py` (e.g.
  `tests/test_source_service.py` ↔ `app/services/source_service.py`,
  `tests/test_fragment_repository.py` ↔ `app/repositories/.../fragment_repository.py`).
- Group related tests in a `class Test<Thing>:` with `async def test_...`
  methods (see `tests/test_source_service.py`'s `TestUploadBulk`,
  `TestDeleteSource` etc.) rather than one flat list of functions per file.
- For a brand-new module, create the test file in the same change — don't
  defer it to a follow-up.

## No real external systems

- **Never** hit a real Postgres, Neo4j, Redis, S3, SQS, or Celery broker in a
  unit test. `tests/conftest.py`'s `_no_celery_dispatch` autouse fixture
  already blocks the confirmed unguarded dispatch/Neo4j-driver call sites —
  extend that fixture (don't create a parallel one) if you add a new call site
  that needs blocking.
- Mock the `UnitOfWork` as a `MagicMock` (or `AsyncMock` for async methods) and
  set `.__enter__`/`.__exit__` if the code under test uses it as a context
  manager (see the `_make_task_uow_mock` helper in `conftest.py` for the
  pattern). Mock individual repository attributes (`uow.sources.get_by_checksum`,
  `uow.projects.get_by_uuid`, ...) rather than mocking the whole ORM session.
- Patch `get_neo4j_driver` (and any Neo4j repository constructed from it) at
  the module where it's *imported/used*, not where it's originally defined —
  e.g. `patch("app.services.source_service.get_neo4j_driver", ...)`, not
  `patch("app.db.neo4j.get_neo4j_driver", ...)`, if the service does
  `from app.db.neo4j import get_neo4j_driver` at module scope. For imports done
  lazily *inside* a function body, patch at the repository's own source module
  instead.

## Mocking `UploadFile` and other framework objects

- When building a mock for `fastapi.UploadFile` (or similar), explicitly set
  every attribute the code under test touches — an unconfigured
  `MagicMock`/`AsyncMock` attribute returns a new `Mock`, not `None`/`0`, which
  will raise a confusing `TypeError` deep in arithmetic/comparison code
  (e.g. set `.size = len(data)` if the service sums `file.size`).

## Required coverage for any new/modified unit of code

- **Service method**: success path + every distinct exception it can raise
  (`NotFoundError`/`ConflictError`/`ValidationError`/etc.), and — for a
  dual-store method — the Postgres-commit-before-Neo4j-write ordering.
- **Route handler**: asserts the service is called correctly and the response
  is wrapped in `ApiResponse[T]`; for a rate-limited endpoint, assert the
  limiter decorator is present (or exercise the 429 path if the test harness
  supports it).
- **Repository method** (non-trivial query only): the query filters/returns
  what's expected, using a mocked session/driver.
- **Celery task**: success, idempotent no-op on redelivery, and the failure
  path routing through `_handle_task_exception`.
- **Bug fix**: a regression test that fails on the pre-fix code and passes
  after — don't fix a bug without a test that would have caught it.

## Running tests

- Run via terminal: `source venv/bin/activate && python -m pytest tests/<file> -v`.
  The `runTests` tool has had discovery issues in this repo ("No tests found"
  even for valid files) — prefer the terminal/pytest route.
- Run the affected test file(s) first while iterating; run the full suite
  (`pytest -q`) before declaring a change complete, to catch regressions in
  sibling test files (e.g. `UnitOfWork` wiring, `router.py` registration).
- Lint with `ruff check app tests` — this repo's `pyproject.toml` ignores
  `E501` (line length) but enforces import sorting (`I`), bugbear (`B`),
  pyupgrade (`UP`), and simplify (`SIM`) rules.

## What NOT to do

- Don't add a new autouse fixture that duplicates isolation already provided
  by `_no_celery_dispatch` in `conftest.py` — extend the existing one.
- Don't assert on exact log message text or internal implementation details
  that aren't part of the module's public contract — assert on return values,
  raised exceptions, and calls made to injected mocks.
- Don't report a task as complete without having actually run the relevant
  test file(s) — "this should work" is not a substitute for a passing
  `pytest` run.
