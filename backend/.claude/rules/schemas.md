---
paths:
  - "app/schemas/**/*.py"
---

# Schema layer conventions (`app/schemas/*.py`)

Schemas are the Pydantic v2 request/response contracts between routes and the
outside world. They validate *shape*, not business rules.

## Naming & structure

- One module per resource: `<resource>_schema.py` (e.g. `project_schema.py`).
- Standard set per resource, matching `app/schemas/project_schema.py`:
  `<Resource>Create` (all required-for-creation fields, `model_config =
  ConfigDict(extra="forbid")`), `<Resource>Update` (every field `| None = None`
  for partial updates), `<Resource>Response` (read model, `model_config =
  ConfigDict(from_attributes=True)` so it can be built directly from an ORM
  instance), and `<Resource>ListResponse` (paginated wrapper — reuse the
  existing pagination envelope shape, don't invent a new one per resource).
- Nested/embedded output shapes (e.g. `sources` with `pages[]`/`bboxes[]` in
  `user_story_schema.py`) must declare an explicit default (`= []`/`=
  Field(default_factory=list)`) — services that read `.sources` assume the
  attribute exists even when empty; a missing default breaks deserialization
  of older stored payloads.

## Validation

- Use `Field(...)` constraints (`min_length`, `max_length`, `ge`/`le`, `pattern`)
  for shape-level constraints (string length, numeric ranges) directly in the
  schema — don't defer purely structural checks to the service layer.
- Use `@field_validator`/`@model_validator` only for validation that depends
  solely on the request payload itself (cross-field consistency, format
  normalization). Anything requiring a DB lookup (uniqueness, existence,
  ownership) belongs in the service, not the schema.
- Prefer `extra="forbid"` on `*Create`/`*Update` input schemas so unexpected
  client fields raise a 422 instead of being silently dropped.

## Testing requirement

- Schemas with a custom `@field_validator`/`@model_validator` need a direct
  unit test (`tests/test_<resource>_schema.py` or inline in the resource's
  service/route test file) covering the valid case and each rejection case —
  don't rely solely on indirect coverage through a route test.

## What NOT to do

- Don't put business logic, DB queries, or external I/O in a schema
  validator — schemas must stay side-effect-free and fast.
- Don't reuse a `*Response` schema as a request body, or vice versa — even if
  the fields currently match, they evolve independently.
- Don't return a raw dict or ORM model from a route when a `*Response` schema
  exists for the resource — the route's `response_model`/service return type
  should be the schema.
- Don't duplicate a field's validation rule by hand in multiple schemas if it
  represents the same domain constraint — factor it into a shared
  `Annotated[...]` type alias or a reusable validator function if it appears
  in three or more schemas.
