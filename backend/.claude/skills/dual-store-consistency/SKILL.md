---
name: dual-store-consistency
description: Use when a feature or task needs to write/read the same logical entity in both PostgreSQL (metadata, via UnitOfWork/SQLAlchemy) and Neo4j (graph representation), or when reviewing code that touches both stores in one request/task. Covers write ordering, idempotency, partial-failure handling, and testing guidance for this repo's dual-store pattern.
---

# Dual-store (Postgres + Neo4j) consistency pattern

Several resources in this repo (projects, sources, module/feature, user
stories, fragments) have a record in Postgres (system-of-record for
metadata/status/ownership) **and** a corresponding node/subgraph in Neo4j
(the requirement graph). There is no distributed transaction across the two
— consistency is achieved by ordering, idempotency, and reconciliation, not
2PC.

## Write ordering

- **Postgres first, Neo4j second**, for any write that both stores need:
  commit the Postgres row (status/metadata) before performing the Neo4j
  write it depends on triggering (e.g. a Celery dispatch or Neo4j upsert) —
  see `app/db/unit_of_work.py`'s docstring rule that the caller must call
  `uow.commit()` explicitly before triggering an external side effect that
  depends on the Postgres write being durable.
- If the Neo4j write fails after the Postgres commit, the entity must be
  left in a state that a retry/reconciliation job (or a redelivered Celery
  task) can detect and safely redo — never leave it silently
  half-written with no status indicating the graph side is incomplete.
- Never make a Neo4j write, then a Postgres write that can fail and roll
  back — Neo4j has no rollback tied to the Postgres transaction, so a
  Postgres-side failure after a successful Neo4j write leaves the graph
  ahead of the metadata store with no automatic corrective action.

## Idempotency (both sides)

- Neo4j: always `MERGE` (never `CREATE`), per `.claude/rules/repositories.md`
  — a redelivered Celery message or retried request must not create
  duplicate nodes/relationships. (Version-snapshot writes —
  `ModuleVersion`/`FeatureVersion`/`UserStoryVersion` — are the sole
  intentional exception.)
- Postgres: guard status transitions (e.g. only `PENDING -> PROCESSING`,
  never overwrite a terminal `COMPLETED`/`FAILED` status) so a retried
  operation is a no-op rather than corrupting already-finalized state.
- Resolve the **canonical id** (e.g. `project_id`) from the owning
  Postgres/Neo4j record before using a caller-supplied id for a graph
  upsert — a caller-provided id that has drifted from the canonical one is a
  known source of `NotFoundError` on Neo4j writes.

## Reads

- Don't silently prefer one store over the other when both could answer a
  query — decide explicitly which store is authoritative for each field
  (status/ownership → Postgres; graph relationships/content → Neo4j) and
  document it in the service docstring if it's not obvious from the method
  name.
- When a schema evolves a field's storage shape (e.g. nested vs. flat), keep
  a read-side fallback (`coalesce(new_field, old_field, default)` in Cypher,
  or an `or`/`getattr` fallback in Python) rather than requiring a backfill
  migration before the code can ship (see how `sources`/`bboxes` handled this).

## Testing

- Unit tests must mock both the `UnitOfWork`/Postgres repository and the
  Neo4j repository/driver — never hit a real database of either kind (see
  `.claude/rules/tests.md`).
- Add a test asserting the **write order** (Postgres commit happens-before
  the Neo4j/Celery side effect) for any new dual-store service method, not
  just the happy-path result — this is the property most likely to regress
  silently.

## What NOT to do

- Don't wrap a Postgres write and a Neo4j write in a single `try/except`
  that treats them as one atomic unit — handle and log each store's failure
  mode distinctly so a partial failure is diagnosable.
- Don't add a new dual-store resource without deciding up front which store
  is authoritative for which fields — retrofitting this decision after
  divergent reads appear in production is expensive.
