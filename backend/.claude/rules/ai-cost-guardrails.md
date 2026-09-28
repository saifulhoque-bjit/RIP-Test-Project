# AI cost & token guardrails (all files)

Apply these on every request in this repo, regardless of layer. Full
rationale/checklist: `.claude/skills/ai-cost-optimization/SKILL.md`.

## Before generating

- Read the target file(s) first — don't guess contents from memory or
  regenerate from scratch what already exists.
- Search for an existing service/repository/schema/utility/base class that
  already does this before writing a new one. The per-layer rules
  (`.claude/rules/routes.md`, `services.md`, `repositories.md`, `models.md`,
  `schemas.md`, `workers.md`) document the canonical existing pattern —
  follow it instead of proposing an alternative architecture unless
  explicitly asked.

## While generating

- Make the smallest correct change: edit only the lines that need to
  change; never rewrite/regenerate a whole file when a targeted edit
  suffices.
- Don't duplicate an existing implementation (helper, validator, base
  class, DI factory) — extend or call it.
- Don't add a new dependency, abstraction, or design pattern not already
  used in this repo without an explicit request.
- Don't add comments/docstrings to unrelated code you didn't otherwise
  change.
- **Write or update the corresponding unit test in the same change** — see
  the root `CLAUDE.md` "Testing is not optional" section and
  `.claude/rules/tests.md`. This is not optional and is not a separate
  follow-up task; a code change and its test are one unit of work.

## Output

- Return only the changed files/functions — not full-file dumps of
  unaffected code.
- Skip restating requirements already covered by an in-scope rule/skill
  file — reference it instead of re-deriving the rule.
- Keep explanations proportional to the change: one line for a small fix, a
  short paragraph for a multi-file change — don't restate the diff in prose.

## What NOT to do

- Don't scan the entire repository when a targeted search/read will answer
  the question.
- Don't produce a large new markdown doc/report unless explicitly
  requested.
- Don't re-run the full test suite mid-task when only the affected test
  file(s) are needed to validate a step — run the full suite once, before
  declaring a multi-file change done.
- Don't skip writing tests to save tokens/time — untested code is not a
  finished change in this repo, regardless of how small the diff looks.
