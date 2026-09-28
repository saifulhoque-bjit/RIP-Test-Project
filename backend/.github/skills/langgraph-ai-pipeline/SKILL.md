---
name: langgraph-ai-pipeline
description: Use when adding/modifying a LangGraph generator-critic AI extraction stage in this repo (app/services/rfp_pipeline_v2_graph_service/*, app/services/source_code_pipeline/*), adding a new prompt file, wiring a new pipeline node, or tuning AI cost/latency/token usage for any multi-pass LLM workflow. Covers state-graph structure, multi-provider LLM factory usage, iteration bounding, and concrete token-optimization checks specific to this codebase.
---

# LangGraph generator/critic pipeline conventions

This repo's AI extraction pipelines (RFP → business architecture / module &
feature / agile backlog, and the source-code → spec pipeline) all follow the
same **generator → critic → conditional loop** shape built with
`langgraph.graph.StateGraph`. Read `app/services/rfp_pipeline_v2_graph_service/graph_module_feature.py`
first as the canonical reference before adding a new stage.

## Standard shape to replicate

1. **State**: a `TypedDict(total=False)` with the fields the loop threads
   through (`parsed_items`, `previous_output`, `human_feedback`,
   `ai_feedback`, `output`, `status`, `iteration`, `options`). Add new fields
   here rather than smuggling extra data through `options`.
2. **Prompts as files**: system prompts live in a sibling `prompts/*.md` file
   and are loaded once at import time via a small `_load_prompt(filename)`
   helper reading from a `settings.*_PROMPT_BASE_PATH` — never inline a large
   prompt string in the node function. Keep generator and critic prompts in
   separate files (`..._generator.md` / `..._critic.md`).
2. **Nodes**: `async def node_x(state) -> dict` functions. Resolve
   provider/model per-node from `options` first, falling back to a
   per-agent `settings.ALT_<STAGE>_MODEL_PROVIDER` / `_MODEL_NAME` /
   `_TEMPERATURE` — this lets callers override model choice per request
   without code changes, and lets generator/critic use different (e.g.
   cheaper) models deliberately.
3. **Structured output**: bind the output schema via
   `llm.with_structured_output(SomeSchema, method="function_calling")` for
   providers that support tool calling; only fall back to
   `method="json_mode"` for providers that need it (e.g. `deepseek`).
4. **Routing / iteration bound**: a `_route(state) -> "pass" | "fail"`
   function that terminates on `status == "PASS"` **or**
   `iteration >= settings.ALT_MAX_CRITIC_ITERATIONS + 1` — every new
   generator/critic loop must have an equivalent hard cap sourced from
   settings, never an unbounded `while`/recursive loop.
5. **Retry policy**: attach `langgraph.types.RetryPolicy(max_attempts=3,
   initial_interval=1.0, backoff_factor=2.0, max_interval=10.0, jitter=True,
   retry_on=(Exception,))` to every node via `graph.add_node(..., retry_policy=policy)`
   — this is the existing standard for transient LLM/API failures. A retry
   count alone doesn't bound *latency* per attempt though — also pass an
   explicit request timeout to the underlying provider call (e.g.
   `litellm.completion(..., timeout=...)`) so a stalled connection fails and
   retries within a bounded time instead of hanging until the Celery task's
   own soft/hard time limit fires. Known current gap:
   `app/services/source_code_pipeline/src/ai/llm_client.py`'s
   `litellm.completion()` call has no explicit `timeout` — don't copy this
   into a new LLM call site.
6. **Skip/cache path**: support `skip_processing=True` reading a hand-placed
   `sample_result/RFP/<stage>.json` or `sample_result/Incremental/<stage>.json`
   file (RFP-mode stages vs. Incremental-mode stages) for local iteration
   without burning tokens — raise (don't silently return `{}`) if the sample
   file is missing, and treat a cached `{"error": ...}` payload as a terminal
   failure rather than passing it downstream for schema validation (see
   `/memories/repo/workers.md` for the exact prior bug this guards against).
   These files are placed manually and are never auto-written by a real run.

## Token-usage optimization checklist (apply to every new/modified node)

- **Don't re-send the full JSON schema as text if the model is already bound
  via `with_structured_output(..., method="function_calling")`** — the
  schema is already transmitted as a tool/function definition by the
  provider SDK. Dumping `model_json_schema()` again into the `HumanMessage`
  (as `node_generate` currently does) doubles the fixed per-call token cost
  for no benefit on function-calling providers; only include the schema
  dump as text for `json_mode` fallback providers that need it.
- **Don't unconditionally re-send the entire previous draft + entire source
  document on every critic iteration.** If `parsed_items`/`previous_output`
  can be large (RFP source chunks, full module/feature draft), consider:
  passing only the diff/section relevant to the critic's last feedback, or
  summarizing `parsed_items` once and reusing the summary across iterations
  instead of the raw text.
- **Use the cheapest adequate model for the critic pass.** Critic prompts
  (PASS/FAIL + structured `<FEEDBACK>`/`<SUGGESTED_FIX>` tags) are usually a
  simpler task than generation — prefer a smaller/cheaper
  `ALT_CRITIC_MODEL_NAME` than the generator's model unless quality testing
  shows it's insufficient.
- **Temperature 0 for structured/deterministic output** (already the
  convention) — non-zero temperature increases retry/critic-loop rate and
  therefore total token spend.
- **Log latency and, where the provider SDK exposes it, token usage**
  (prompt/completion tokens) per node call — without this, cost regressions
  in a prompt-file change are invisible until the bill arrives. Add a
  `logger.info(... input_tokens=..., output_tokens=...)` line next to the
  existing `latency_ms` log if the LangChain response exposes
  `usage_metadata`.
- **Keep prompts lean**: trim example blocks / redundant instructions in
  `prompts/*.md` files rather than growing them indefinitely; every token in
  a system prompt is paid on every single generator/critic call for the
  life of that prompt file.
- **Respect `ALT_MAX_CRITIC_ITERATIONS`** as a hard ceiling — raising it to
  "fix" a quality problem multiplies token cost linearly; prefer improving
  the prompt or critic feedback quality first.

## What NOT to do

- Don't add a new pipeline stage with an unbounded retry/critic loop — every
  loop needs a `_route`-style hard iteration cap from settings.
- Don't call an LLM provider directly (`ChatOpenAI(...)` etc.) — always go
  through `app.clients.llm_factory.get_llm(provider, model_name, temperature)`
  so API keys, provider validation, and error wrapping (`AIWorkflowError`)
  stay centralized.
- Don't log full prompt/response bodies containing source document content
  at `info` level in production — log lengths/hashes/latency, and gate full
  payload logging behind a debug flag if needed for local troubleshooting.
- Don't call a blocking sync SDK method directly inside an `async def` node
  or pipeline function — it stalls whatever event loop is running that
  coroutine for every other concurrent caller on it. Known current gap:
  `image_extraction_service.py::extract_image_fragment` calls
  `client.process_image(...)` (a sync SDK call) directly inside `async def`;
  low-impact today since it only runs inside one Celery task's own
  short-lived loop, but don't extend this pattern to a path that shares the
  main FastAPI loop or runs under `asyncio.gather`.
