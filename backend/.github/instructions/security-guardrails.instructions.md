---
applyTo: "app/**/*.py"
---

# Security guardrails (all of `app/`)

These are hard rules distilled from a full architecture/security review of
this codebase (2026-07). Follow them for any new or modified code, regardless
of layer.

## Injection

- **SQL**: use SQLAlchemy expressions/ORM query methods. If raw SQL is ever
  unavoidable, use `text()` with bound parameters — never f-string/`.format()`/
  `%`-format user input into a query string.
- **Cypher**: always parameterize with `$param` placeholders passed as
  `tx.run(cypher, param=value)` keyword arguments — never interpolate a
  variable directly into the Cypher string. Use `MERGE` (not `CREATE`) for
  writes so redelivered Celery messages don't create duplicate nodes.

## Path traversal (CWE-22)

- Any code that extracts an archive (ZIP) or writes a file to a path derived
  from user input (filename, project id, uploaded path) MUST validate the
  resolved path stays within the intended base directory before writing.
  Canonical pattern: `_assert_zip_members_are_safe` in
  `app/workers/_task_helpers.py` — reject absolute paths, Windows drive-letter
  paths, and any entry whose `Path(base, member).resolve()` escapes
  `base.resolve()`, checked *before* calling `extractall()`.
- Never trust a client-supplied filename as a literal path segment without
  sanitizing/normalizing it first (see `app.utils.common.normalize_filename`).

## Uploads & resource exhaustion

- Every file-upload endpoint must enforce: (1) an allow-list of MIME types,
  (2) a per-file max size, and (3) for bulk/multi-file endpoints, a cumulative
  max total size and a max file count — check these before reading file bytes
  into memory where possible. See `SourceService._validate_bulk_upload_request`
  and `IncrementalBulkUploadService.upload_and_enqueue` for the current
  reference implementation (`SOURCE_BULK_MAX_FILES`, `SOURCE_BULK_MAX_TOTAL_SIZE_MB`,
  `SOURCE_MAX_FILE_SIZE_MB` in `app/core/config.py`).
- New upload, download, auth, or AI-generation-triggering (regenerate/
  generate — anything enqueuing an LLM-backed Celery job) endpoints must be
  rate-limited via `app/core/rate_limiter.py` (`@limiter.limit(...)` +
  `request: Request` param) — see `.github/instructions/routes.instructions.md`
  for the exact pattern and the current known gap (module-feature/user-story
  regenerate endpoints).

## Secrets & configuration

- All configuration/secrets flow through `app.core.config.settings`
  (Pydantic `BaseSettings`) — never hardcode credentials, API keys, connection
  strings, or environment-specific URLs in application code.
- Never log secrets, tokens, passwords, or PII. Structured logging via
  `app.utils.logger.get_logger` already strips nothing automatically — the
  caller is responsible for not passing sensitive values into a log call.

## CORS & cookies

- Never set `CORS_ORIGINS` to `"*"` while `allow_credentials=True` is enabled
  (browsers will reject it anyway, but don't rely on that — keep an explicit
  origin allow-list).
- Auth cookies are `HttpOnly`; don't add a new auth-adjacent cookie without
  also setting `HttpOnly`/`Secure`/appropriate `SameSite`.

## Authentication & authorization

- JWTs are validated via `app.core.security.decode_cognito_token` — RS256
  only, `iss`/`aud`/`token_use` are checked, JWKS is cached with rotation
  support. Don't add a second token-verification code path; extend the
  existing one if a new claim needs checking.
- Use `require_roles`/`require_permissions` dependency factories from
  `app/deps.py` for role/permission-gated endpoints — don't hand-roll a role
  check inline in a route or service.

## Error handling

- Never let a raw exception (stack trace, DB error text, internal file paths)
  reach the HTTP response. Raise a specific `RIPBaseException` subclass
  (`app/core/exceptions.py`) with a safe, user-facing message; the centralized
  handler in `app/core/exception_handlers.py` converts it to the standard
  JSON error envelope.

## Dependency & data hygiene

- Don't add a new third-party dependency without checking it's actually
  needed — this repo pins versions in `requirements.txt`/`requirements-dev.txt`.
- Don't disable a Pydantic validator, type check, or `ruff`/`mypy` rule via a
  blanket `# noqa`/`# type: ignore` to silence a real issue — fix the
  underlying code, or scope the suppression to the exact rule if it's a
  genuine false positive.
