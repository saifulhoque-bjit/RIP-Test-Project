---
applyTo: "tests/**/*.py"
---

# Test conventions (`tests/*.py`)

## Structure & naming

- One test file per source module, mirroring the `app/` path:
  `tests/test_<module>.py` tests `app/**/<module>.py` (e.g.
  `tests/test_source_service.py` ↔ `app/services/source_service.py`,
  `tests/test_fragment_repository.py` ↔ `app/repositories/.../fragment_repository.py`).
- Group related tests in a `class Test<Thing>:` with `async def test_...`
  methods (see `tests/test_source_service.py`'s `TestUploadBulk`,
  `TestDeleteSource` etc.) rather than one flat list of functions per file.

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

## Running tests

- Run via terminal: `source venv/bin/activate && python -m pytest tests/<file> -v`.
  The `runTests` tool has had discovery issues in this repo ("No tests found"
  even for valid files) — prefer the terminal/pytest route.
- Run the full suite (`pytest`) before considering a change complete, to catch
  regressions in sibling test files.
- Lint with `ruff check app tests` — this repo's `pyproject.toml` ignores
  `E501` (line length) but enforces import sorting (`I`), bugbear (`B`),
  pyupgrade (`UP`), and simplify (`SIM`) rules.

## What NOT to do

- Don't add a new autouse fixture that duplicates isolation already provided
  by `_no_celery_dispatch` in `conftest.py` — extend the existing one.
- Don't assert on exact log message text or internal implementation details
  that aren't part of the module's public contract — assert on return values,
  raised exceptions, and calls made to injected mocks.
