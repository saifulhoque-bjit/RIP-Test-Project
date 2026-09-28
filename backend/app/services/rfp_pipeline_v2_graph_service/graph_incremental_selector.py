"""
LangGraph: Incremental Selector — Generator → Critic → route.

Reads a meeting note and the full backlog feature list, and selects
the subset of feature UUIDs the incremental update pipeline needs to see.
Runs as a pre-filter before graph_incremental.py.
"""

import json as _json
import os
import pathlib
import re
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy
from typing_extensions import TypedDict

from app.clients.llm_factory import get_llm
from app.core import task_control
from app.core.config import settings
from app.core.llm_errors import NonRetryableLLMError, classify_llm_error
from app.schemas.rfp_pipeline_v2_graph_schema import SelectorOutput
from app.utils.cancellable_llm import LLMCancelled, ainvoke_cancellable
from app.utils.common import enforce_dev_only_skip_processing
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Prompt loader ──────────────────────────────────────────────────────────────


def _load_prompt(filename: str) -> str:
    path = os.path.join(settings.ALT_PIPELINE_PROMPT_BASE_PATH, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


PROMPT_GEN = _load_prompt("incremental_selector_generator.md")
PROMPT_CRIT = _load_prompt("incremental_selector_critic.md")


# ── State ──────────────────────────────────────────────────────────────────────


class SelectorState(TypedDict, total=False):
    note: str  # plain-text meeting note (already flattened by caller)
    items_prompt: str  # formatted ITEM LIST string sent to LLM
    items_label: str  # e.g. "FEATURE LIST"
    valid_ids: list  # source-of-truth UUIDs — used for post-graph filtering only
    output: str  # raw JSON string from <OUTPUT> block
    status: str
    ai_feedback: str
    iteration: int
    options: dict


# ── Helpers ────────────────────────────────────────────────────────────────────


def _extract_text(content) -> str:
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def note_from_json(fragments: list[dict]) -> str:
    """
    Flatten a parsed note fragment array into plain text for the selector LLM.
    The selector only needs textual context — no source/bbox data needed.

    Handles:
      - plain text / heading fragments: use content directly
      - handwriting fragments: content is a JSON-encoded OCR result — extract text lines
    """
    parts = []
    for frag in fragments:
        content = frag.get("content", "")
        frag_type = frag.get("frag_type", "")

        if frag_type == "handwriting":
            try:
                parsed = _json.loads(content)
                description = parsed.get("description", "")
                if description:
                    parts.append(description)
                for group in parsed.get("ocr_groups", []):
                    parts.extend(group.get("text", []))
            except (_json.JSONDecodeError, TypeError):
                if content:
                    parts.append(content)
        else:
            text = content.strip().lstrip("#").strip()
            if text:
                parts.append(text)

    return "\n".join(parts).strip()


def _format_items_for_prompt(items: list[dict]) -> str:
    """
    Format the feature list for the LLM prompt.
    Each item must have: id, title, description.
    Optional: functions (list[str]), stories (list[str]).
    """
    lines = []
    for item in items:
        lines.append(f"  ID: {item['id']}")
        lines.append(f"  {item['title']}")
        lines.append(f"  {item['description']}")
        if item.get("functions"):
            lines.append(f"    Functions: {', '.join(item['functions'])}")
        if item.get("stories"):
            lines.append(f"    Stories: {', '.join(item['stories'])}")
        lines.append("")
    return "\n".join(lines).strip()


def _strip_output_tags(text: str) -> str:
    """Extract the JSON payload from inside <OUTPUT>...</OUTPUT> tags."""
    match = re.search(r"<OUTPUT>(.*?)</OUTPUT>", text, re.DOTALL)
    if match:
        extracted = match.group(1).strip()
    else:
        # Tolerate an unclosed tag (LLM truncation)
        open_match = re.search(r"<OUTPUT>(.*)", text, re.DOTALL)
        extracted = open_match.group(1).strip() if open_match else text.strip()
    # Strip any markdown code fences the LLM may wrap around the JSON
    extracted = re.sub(r"^```(?:json)?\s*", "", extracted)
    extracted = re.sub(r"\s*```$", "", extracted)
    return extracted.strip()


def _extract_critic_response(text: str) -> tuple[str, str]:
    status_match = re.search(r"<STATUS>(.*?)</STATUS>", text, re.DOTALL)
    feedback_match = re.search(r"<FEEDBACK>(.*?)</FEEDBACK>", text, re.DOTALL)
    fix_match = re.search(r"<SUGGESTED_FIX>(.*?)</SUGGESTED_FIX>", text, re.DOTALL)

    status = status_match.group(1).strip() if status_match else "UNKNOWN"
    feedback = feedback_match.group(1).strip() if feedback_match else ""
    fix = fix_match.group(1).strip() if fix_match else ""

    combined = f"{feedback}\n\nSUGGESTED FIX:\n{fix}" if fix else feedback
    return status, combined.strip()


# ── Nodes ──────────────────────────────────────────────────────────────────────


async def node_generate(state: SelectorState) -> dict:
    iteration = state.get("iteration", 0) + 1
    logger.info("[INCREMENTAL SELECTOR GENERATOR] Starting iteration=%d", iteration)

    opts = state.get("options") or {}
    request_id = opts.get("request_id")
    if request_id and task_control.is_request_cancelled(request_id):
        logger.info("[INCREMENTAL SELECTOR GENERATOR] Skipping — request %s cancelled", request_id)
        return {"status": "CANCELLED", "iteration": iteration}

    provider = opts.get("llm_provider") or settings.ALT_ARCHITECT_MODEL_PROVIDER
    model_name = opts.get("llm_model") or settings.ALT_ARCHITECT_MODEL_NAME
    api_key = opts.get("llm_api_key")
    # print(api_key)

    llm = get_llm(
        provider=provider,
        model_name=model_name,
        temperature=settings.ALT_ARCHITECT_TEMPERATURE,
        max_tokens=settings.ALT_MAX_TOKENS,
        api_key=api_key,
    )

    label = state.get("items_label", "FEATURE LIST")
    feedback = (state.get("ai_feedback") or "").strip()
    feedback_block = (
        f"\n\n---\n\nCRITIC FEEDBACK — MANDATORY CORRECTIONS. "
        f"Do NOT re-argue or defend previous choices. "
        f"Execute each instruction exactly as stated and resubmit:\n{feedback}"
        if feedback
        else ""
    )

    messages = [
        SystemMessage(content=PROMPT_GEN),
        HumanMessage(
            content=(
                f"{label}:\n{state['items_prompt']}\n\n"
                f"---\n\n"
                f"MEETING NOTE:\n{state['note']}"
                f"{feedback_block}"
            )
        ),
    ]

    start_ts = time.monotonic()
    try:
        response = await ainvoke_cancellable(llm, messages, request_id=request_id)
    except LLMCancelled:
        logger.info(
            "[INCREMENTAL SELECTOR GENERATOR] Cancelled mid-stream — request %s", request_id
        )
        return {"status": "CANCELLED", "iteration": iteration}
    except Exception as exc:
        classification = classify_llm_error(exc, provider=provider)
        if not classification.retryable:
            logger.error(
                "[INCREMENTAL SELECTOR GENERATOR] Non-retryable LLM error (reason=%s): %s",
                classification.reason.value,
                classification.message,
            )
            raise NonRetryableLLMError(classification, original=exc) from exc
        raise
    latency_ms = int((time.monotonic() - start_ts) * 1000)

    raw = _extract_text(response.content)
    output = _strip_output_tags(raw)

    logger.info(
        "[INCREMENTAL SELECTOR GENERATOR] Done iteration=%d latency_ms=%d output_len=%d",
        iteration,
        latency_ms,
        len(output),
    )
    return {"output": output, "iteration": iteration, "ai_feedback": ""}


async def node_critic(state: SelectorState) -> dict:
    if state.get("status") == "CANCELLED":
        return state

    logger.info("[INCREMENTAL SELECTOR CRITIC] Reviewing iteration=%d", state.get("iteration", 0))

    opts = state.get("options") or {}
    request_id = opts.get("request_id")
    provider = opts.get("llm_provider") or settings.ALT_CRITIC_MODEL_PROVIDER
    model_name = opts.get("llm_model") or settings.ALT_CRITIC_MODEL_NAME
    api_key = opts.get("llm_api_key")

    llm = get_llm(
        provider=provider,
        model_name=model_name,
        temperature=settings.ALT_CRITIC_TEMPERATURE,
        max_tokens=settings.ALT_MAX_TOKENS,
        api_key=api_key,
    )

    label = state.get("items_label", "FEATURE LIST")

    messages = [
        SystemMessage(content=PROMPT_CRIT),
        HumanMessage(
            content=(
                f"{label}:\n{state['items_prompt']}\n\n"
                f"---\n\n"
                f"MEETING NOTE:\n{state['note']}\n\n"
                f"---\n\n"
                f"CURATOR PROPOSAL:\n{state['output']}"
            )
        ),
    ]

    start_ts = time.monotonic()
    try:
        response = await ainvoke_cancellable(llm, messages, request_id=request_id)
    except LLMCancelled:
        logger.info("[INCREMENTAL SELECTOR CRITIC] Cancelled mid-stream — request %s", request_id)
        return {"status": "CANCELLED"}
    except Exception as exc:
        classification = classify_llm_error(exc, provider=provider)
        if not classification.retryable:
            logger.error(
                "[INCREMENTAL SELECTOR CRITIC] Non-retryable LLM error (reason=%s): %s",
                classification.reason.value,
                classification.message,
            )
            raise NonRetryableLLMError(classification, original=exc) from exc
        raise
    latency_ms = int((time.monotonic() - start_ts) * 1000)

    raw = _extract_text(response.content)
    status, feedback = _extract_critic_response(raw)

    logger.info(
        "[INCREMENTAL SELECTOR CRITIC] status=%s latency_ms=%d feedback=%s",
        status,
        latency_ms,
        feedback,
    )
    return {"status": status, "ai_feedback": feedback}


# ── Routing ────────────────────────────────────────────────────────────────────


def _route(state: SelectorState) -> str:
    if (
        state["status"] in ("PASS", "CANCELLED")
        or state.get("iteration", 0) >= settings.ALT_MAX_SELECTOR_CRITIC_ITERATIONS + 1
    ):
        if state["status"] not in ("PASS", "CANCELLED"):
            logger.warning(
                "[INCREMENTAL SELECTOR] Max iterations reached at iteration=%d, moving forward",
                state.get("iteration", 0),
            )
        return "pass"
    return "fail"


# ── Graph ──────────────────────────────────────────────────────────────────────


def _build_graph():
    policy = RetryPolicy(
        max_attempts=3,
        initial_interval=1.0,
        backoff_factor=2.0,
        max_interval=10.0,
        jitter=True,
        retry_on=(Exception,),
    )
    g = StateGraph(SelectorState)
    g.add_node("generate", node_generate, retry_policy=policy)
    g.add_node("critic", node_critic, retry_policy=policy)
    g.set_entry_point("generate")
    g.add_edge("generate", "critic")
    g.add_conditional_edges("critic", _route, {"pass": END, "fail": "generate"})
    return g.compile()


# ── Public runner ──────────────────────────────────────────────────────────────


async def run_incremental_selector(
    note: str,
    items: list[dict],
    items_label: str = "FEATURE LIST",
    skip_processing: bool = False,
    options: dict = None,
) -> dict:
    """
    Select the backlog features relevant to a meeting note.

    Args:
        note:          Plain-text meeting note. Use note_from_json() to flatten
                       fragment arrays before passing here.
        items:         List of feature dicts, each with keys:
                         id (str UUID), title (str), description (str),
                         functions (list[str], optional), stories (list[str], optional)
        items_label:   Header label for the LLM prompt (default: "FEATURE LIST").
        skip_processing: When True, return cached output without calling the LLM.
        options:       Optional dict with llm_provider / llm_model overrides.

    Returns:
        {
            "selected_ids": list[str],   # UUIDs confirmed against valid_ids
            "meta":         dict[str, str],  # uuid → "direct" | "context-only"
            "output":       str,          # raw JSON string from <OUTPUT> block
            "status":       str,          # "PASS" | "CANCELLED" | "FAIL" | "UNKNOWN"
            "ai_feedback":  str,
            "iteration":    int,
        }
    """
    skip_processing = enforce_dev_only_skip_processing(skip_processing)
    _CACHE_DIR = pathlib.Path(
        "app/services/rfp_pipeline_v2_graph_service/sample_result/Incremental"
    )
    cache_file = _CACHE_DIR / "incremental_selector.json"

    if skip_processing:
        if not cache_file.exists():
            return {
                "status": "failed",
                "error": (
                    f"No cached output found at '{cache_file}'. "
                    "Run once with skip_processing=False to generate and save the output."
                ),
            }
        logger.info("[INCREMENTAL SELECTOR] Loading cached output from %s", cache_file)
        return _json.loads(cache_file.read_text(encoding="utf-8"))

    valid_ids = [i["id"] for i in items]
    items_prompt = _format_items_for_prompt(items)

    app = _build_graph()
    try:
        result = await app.ainvoke(
            {
                "note": note,
                "items_prompt": items_prompt,
                "items_label": items_label,
                "valid_ids": valid_ids,
                "output": "",
                "status": "",
                "ai_feedback": "",
                "iteration": 0,
                "options": options or {},
            }
        )
    except Exception as e:
        logger.exception("[Incremental Selector] Graph execution failed")
        logger.info(str(e))

        return {
            "status": "failed",
            "error": str(e),
        }

    # ── Parse and validate output ──────────────────────────────────────────────
    raw_output = result.get("output", "")
    selected_ids: list[str] = []
    meta: dict[str, str] = {}

    if result.get("status") != "CANCELLED":
        try:
            parsed = _json.loads(raw_output)
            selector_out = SelectorOutput(**parsed)

            # Hard post-graph filter: strip any UUID the LLM hallucinated
            valid_id_set = set(valid_ids)
            clean_ids = [uid for uid in selector_out.selected_ids if uid in valid_id_set]
            hallucinated = set(selector_out.selected_ids) - valid_id_set
            if hallucinated:
                logger.warning(
                    "[INCREMENTAL SELECTOR] Stripped %d hallucinated UUID(s): %s",
                    len(hallucinated),
                    hallucinated,
                )

            selected_ids = clean_ids
            meta = {uid: selector_out.meta[uid] for uid in clean_ids if uid in selector_out.meta}

        except Exception as exc:
            logger.error(
                "[INCREMENTAL SELECTOR] Output validation failed: %s — raw: %.200s",
                exc,
                raw_output,
            )

    final = {
        "selected_ids": selected_ids,
        "meta": meta,
        "output": raw_output,
        "status": result.get("status", "UNKNOWN"),
        "ai_feedback": result.get("ai_feedback", ""),
        "iteration": result.get("iteration", 0),
    }
    return final
