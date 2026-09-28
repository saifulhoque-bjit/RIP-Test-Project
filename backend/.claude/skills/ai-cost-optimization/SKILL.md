---
name: ai-cost-optimization
description: Use when explicitly asked to optimize AI-assistant token usage, reduce LLM API/credit costs, review how Claude should approach a task in this repo, or when planning a large multi-file change where context/response size matters. This is the detailed reference; the always-on enforced rules live in .claude/rules/ai-cost-guardrails.md.
---

# AI-assistant cost optimization reference

This repo is developed with heavy AI-assistant usage (Claude Code and
GitHub Copilot side by side) — token/response size is a cost, not just a
quality dimension. `.claude/rules/ai-cost-guardrails.md` has the always-on
enforced rules (read that first); this skill is the fuller checklist for
larger or cost-sensitive tasks.

## Response optimization

- Return only the requested output; skip preambles/summaries not asked for.
- Generate incremental diffs, not full-file regenerations.
- Modify existing code over adding a parallel/duplicate implementation.
- Skip boilerplate a reusable component already provides (`BaseRepository`,
  `ApiResponse[T]`, the `RIPBaseException` hierarchy, the
  `Annotated[..., Depends(...)]` DI alias pattern — see the root `CLAUDE.md`).
- Add code comments only where the "why" isn't obvious from the code —
  don't narrate straightforward code.

## Prompt optimization

- State the task and point at the relevant existing file(s)/pattern to
  mirror, rather than re-describing architecture already documented in the
  layer rules.
- Follow existing conventions unless an alternative is explicitly
  requested — don't default to "suggest a better design."
- Ask for the minimum examples needed (usually one canonical reference
  file, e.g. "follow the `Project` resource pattern").
- Don't restate rules already covered by an in-scope rule/skill file.
- Prefer structured output (table, short list, diff) over open-ended prose
  when the answer is inherently structured.

## Context management

- Load only files relevant to the task (targeted `Read`/`Grep`) — don't
  re-read the whole `app/` tree for a single-file change.
- Use targeted search instead of a broad/semantic scan when the exact
  name/pattern is already known.
- Reference an existing implementation by path instead of pasting it in
  full when instructing an extension of it.

## Architecture reuse

- Routes → Services → Repositories → Models is the only call direction;
  don't add a layer or bypass an existing one to "simplify" a one-off task.
- Check `BaseRepository`, existing `<Resource>Service` classes, and
  `app/core/exceptions.py`/`app/core/messages.py` before adding a new
  abstraction — most CRUD/error-handling needs are already covered.
- Avoid new dependencies/frameworks for something an existing
  `requirements.txt` library already covers.

## Rules/skill design

- One skill = one responsibility (e.g. `langgraph-ai-pipeline`,
  `dual-store-consistency`) — don't create a catch-all skill.
- Put durable architectural decisions in a rule/skill file once instead of
  re-explaining them per prompt/session — path-scoped `.claude/rules/*.md`
  files (`paths:` frontmatter) attach automatically by file path.
- Keep rule/skill files scannable (headers + short bullets); a skill is
  loaded on demand, but a bloated one still costs tokens every time it's read.

## Cost-reduction checklist for a non-trivial task

1. Identify the smallest set of files that must change.
2. Check for an existing pattern/abstraction to reuse (grep for a similar
   resource/service first).
3. Make the diff — including the corresponding test in the same pass, per
   `.claude/rules/tests.md`; don't touch unrelated code.
4. Run only the relevant test file(s) first; run the full suite once, at
   the end.
5. Summarize the result in 1–3 sentences — link to changed files instead of
   quoting them back in full.

## What NOT to do

- Don't regenerate a full module when a partial update satisfies the
  request.
- Don't produce a large standalone documentation file for a change unless
  explicitly requested — update the relevant rule/skill file in place instead.
- Don't run a broad, repo-wide semantic search when a targeted grep/file
  search already has enough signal.
- Don't treat "write the tests" as an optional or separate follow-up step
  to save tokens — an untested change is an unfinished change here, and
  finishing it later costs more total tokens than doing it once.
