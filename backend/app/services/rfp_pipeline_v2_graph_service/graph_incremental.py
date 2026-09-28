import json
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
from app.schemas.rfp_pipeline_v2_graph_schema import IncrementalCriticReviewSchema
from app.utils.cancellable_llm import (
    LLMCancelled,
    ainvoke_cancellable,
    ainvoke_cancellable_structured,
)
from app.utils.common import enforce_dev_only_skip_processing
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Prompts ────────────────────────────────────────────────────────────────────
def _load_prompt(filename: str) -> str:
    path = os.path.join(settings.ALT_PIPELINE_PROMPT_BASE_PATH, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


PROMPT_GEN = _load_prompt("incremental_update_generator.md")
PROMPT_CRIT = _load_prompt("incremental_update_critic.md")


# ── State ─────────────────────────────────────────────────────────────────────
class IncrementalState(TypedDict, total=False):
    backlog: str  # JSON string of the full backlog
    meeting_notes: str  # JSON string of PDF note fragments array
    image_notes: str  # JSON string of image fragments array ("[]" if none)
    user_message: str
    previous_output: str
    context_mode: str
    output: str
    status: str
    ai_feedback: str
    iteration: int
    options: dict
    critic_review: dict


# ── Helpers ───────────────────────────────────────────────────────────────────
def _extract_text(content) -> str:
    """Normalise LLM response content to a plain string."""
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return content


def _strip_output_tags(text: str) -> str:
    match = re.search(r"<OUTPUT>(.*?)</OUTPUT>", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def _strip_json_fences(text: str) -> str:
    """Remove markdown ```json ... ``` or ``` ... ``` fences the LLM may emit."""
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL)
    return m.group(1).strip() if m else text


def _parse_output_for_report(output_text: str) -> dict:
    """
    Best-effort parse of the generator's final JSON output, used ONLY to build
    the reporting fields (`has_changes`, `no_changes_explanation`) in
    run_incremental_update. Never raises — a parse failure just means the
    report falls back to conservative defaults. This intentionally does NOT
    validate against IncrementalUpdateOutput; it must never be able to block
    or alter `output`/`status`/`ai_feedback` themselves.
    """
    try:
        data = json.loads(output_text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _build_notes_section(meeting_notes: str, image_notes: str) -> str:
    """Combine PDF and image note arrays into labelled input blocks for the LLM."""
    parts = [f"SOURCE NOTES (PDF):\n{meeting_notes}"]
    image_notes = (image_notes or "").strip()
    if image_notes and image_notes != "[]":
        parts.append(f"SOURCE NOTES (IMAGES):\n{image_notes}")
    return "\n\n---\n\n".join(parts)


# ── Nodes ─────────────────────────────────────────────────────────────────────
async def node_generate(state: IncrementalState) -> dict:
    iteration = state.get("iteration", 0) + 1
    logger.info("[INCREMENTAL GENERATOR] Starting iteration=%d", iteration)

    opts = state.get("options") or {}
    request_id = opts.get("request_id")
    if request_id and task_control.is_request_cancelled(request_id):
        logger.info("[INCREMENTAL GENERATOR] Skipping — request %s cancelled", request_id)
        return {"status": "CANCELLED", "iteration": iteration}

    provider = opts.get("llm_provider") or settings.ALT_ARCHITECT_MODEL_PROVIDER
    model_name = opts.get("llm_model") or settings.ALT_ARCHITECT_MODEL_NAME
    api_key = opts.get("llm_api_key")

    llm = get_llm(
        provider=provider,
        model_name=model_name,
        temperature=settings.ALT_ARCHITECT_TEMPERATURE,
        max_tokens=settings.ALT_MAX_TOKENS,
        api_key=api_key,
    )

    context_mode = (state.get("context_mode") or "FULL").upper()
    context_mode_line = (
        "CONTEXT MODE: SUBSET — You are receiving a partial backlog. Only features "
        "directly relevant to this input are included. Sister features and other modules "
        "not shown still exist in the system. Do not infer the absence of an item from "
        "its absence here. For all new items in 'adds', set codes (module_code, "
        "feature_code, fun_code, user_story_code) to null — the backend assigns real "
        "codes on insert. For updates and deletes, copy all codes and UUIDs verbatim "
        "from the backlog as normal."
        if context_mode == "SUBSET"
        else "CONTEXT MODE: FULL — You are receiving the complete backlog. Apply all "
        "sequencing rules as documented."
    )

    user_msg = (state.get("user_message") or "").strip()
    extra_user_context = f"\n\n---\n\nUSER MESSAGE:\n{user_msg}" if user_msg else ""

    previous_output = (state.get("previous_output") or "").strip()
    previous_output_block = (
        f"\n\n---\n\nPREVIOUS OUTPUT (REGENERATION MODE):\n{previous_output}"
        if previous_output
        else ""
    )

    ai_feedback = (state.get("ai_feedback") or "").strip()
    extra_feedback = (
        f"\n\n---\n\nCRITIC FEEDBACK (you MUST address every point below before resubmitting):\n{ai_feedback}"
        if ai_feedback
        else ""
    )

    notes_section = _build_notes_section(
        state.get("meeting_notes", "[]"),
        state.get("image_notes", "[]"),
    )

    messages = [
        SystemMessage(content=PROMPT_GEN),
        HumanMessage(
            content=(
                f"{context_mode_line}\n\n---\n\n"
                f"EXISTING BACKLOG:\n{state['backlog']}\n\n---\n\n"
                f"{notes_section}"
                f"{previous_output_block}"
                f"{extra_user_context}"
                f"{extra_feedback}"
            )
        ),
    ]

    start_ts = time.monotonic()
    try:
        response = await ainvoke_cancellable(llm, messages, request_id=request_id)
    except LLMCancelled:
        logger.info("[INCREMENTAL GENERATOR] Cancelled mid-stream — request %s", request_id)
        return {"status": "CANCELLED", "iteration": iteration}
    except Exception as exc:
        classification = classify_llm_error(exc, provider=provider)
        if not classification.retryable:
            logger.error(
                "[INCREMENTAL GENERATOR] Non-retryable LLM error (reason=%s): %s",
                classification.reason.value,
                classification.message,
            )
            raise NonRetryableLLMError(classification, original=exc) from exc
        raise
    latency_ms = int((time.monotonic() - start_ts) * 1000)

    raw_text = _extract_text(response.content)
    output_text = _strip_json_fences(_strip_output_tags(raw_text))

    logger.info(
        "[INCREMENTAL GENERATOR] Done latency_ms=%d iteration=%d output_len=%d",
        latency_ms,
        iteration,
        len(output_text),
    )
    return {
        "output": output_text,
        "iteration": iteration,
        "ai_feedback": "",
    }


async def node_critic(state: IncrementalState) -> dict:
    if state.get("status") == "CANCELLED":
        return state

    logger.info("[INCREMENTAL CRITIC] Reviewing iteration=%d", state.get("iteration", 0))

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
    if provider == "deepseek":
        structured_llm = llm.with_structured_output(
            IncrementalCriticReviewSchema, method="json_mode"
        )
    else:
        structured_llm = llm.with_structured_output(
            IncrementalCriticReviewSchema, method="function_calling"
        )

    user_msg = (state.get("user_message") or "").strip()
    extra_user_context = f"\n\n---\n\nUSER MESSAGE:\n{user_msg}" if user_msg else ""

    notes_section = _build_notes_section(
        state.get("meeting_notes", "[]"),
        state.get("image_notes", "[]"),
    )

    context_mode = (state.get("context_mode") or "FULL").upper()
    context_mode_line = (
        "CONTEXT MODE: SUBSET — Skip Criterion 3 (code sequencing) for all items in "
        "'adds'. Codes for new items are null by design in subset mode."
        if context_mode == "SUBSET"
        else "CONTEXT MODE: FULL — Apply all criteria including Criterion 3."
    )

    # inject as first line of HumanMessage, before EXISTING BACKLOG:
    messages = [
        SystemMessage(content=PROMPT_CRIT),
        HumanMessage(
            content=(
                f"{context_mode_line}\n\n---\n\n"
                f"EXISTING BACKLOG:\n{state['backlog']}\n\n---\n\n"
                f"{notes_section}\n\n---\n\n"
                f"DRAFT CHANGE PROPOSAL:\n{state['output']}"
                f"{extra_user_context}"
            )
        ),
    ]

    start_ts = time.monotonic()
    try:
        if provider == "deepseek":
            review: IncrementalCriticReviewSchema = await ainvoke_cancellable(
                structured_llm, messages, request_id=request_id
            )
        else:
            review: IncrementalCriticReviewSchema = await ainvoke_cancellable_structured(
                structured_llm, messages, request_id=request_id
            )
    except LLMCancelled:
        logger.info("[INCREMENTAL CRITIC] Cancelled mid-stream — request %s", request_id)
        return {"status": "CANCELLED"}
    except Exception as exc:
        classification = classify_llm_error(exc, provider=provider)
        if not classification.retryable:
            logger.error(
                "[INCREMENTAL CRITIC] Non-retryable LLM error (reason=%s): %s",
                classification.reason.value,
                classification.message,
            )
            raise NonRetryableLLMError(classification, original=exc) from exc
        raise
    latency_ms = int((time.monotonic() - start_ts) * 1000)

    # Flatten flagged_items into the same imperative feedback string the
    # generator has always consumed via ai_feedback — the generator's own
    # prompt/logic is untouched, it just keeps reading a string.
    ai_feedback_text = "\n".join(
        f"- [{item.entity_type} {item.entity_id}]: {item.suggested_fix}"
        for item in review.flagged_items
    )

    logger.info(
        "[INCREMENTAL CRITIC] status=%s latency_ms=%d flagged=%d",
        review.status,
        latency_ms,
        len(review.flagged_items),
    )
    logger.info("[INCREMENTAL CRITIC] summary=%s", review.summary)
    logger.debug("[INCREMENTAL CRITIC] reasoning=%s", review.reasoning)
    if ai_feedback_text:
        logger.info("[INCREMENTAL CRITIC] flattened_feedback=%s", ai_feedback_text)

    # Visibility only — deliberately NOT auto-corrected. Silently flipping
    # FAIL->PASS risks rubber-stamping a real problem the model reasoned
    # through but failed to write into flagged_items; silently flipping
    # PASS->FAIL when items exist would second-guess the model in the other
    # direction. Either "fix" could be wrong, so we log loudly instead of
    # guessing, and rely on the reasoning-first prompt structure to prevent
    # this at the source.
    if (review.status == "PASS") != (len(review.flagged_items) == 0):
        logger.warning(
            "[INCREMENTAL CRITIC] status/flagged_items INCONSISTENCY: status=%s "
            "but flagged_items has %d entries. reasoning=%s",
            review.status,
            len(review.flagged_items),
            review.reasoning,
        )

    return {
        "status": review.status,
        "ai_feedback": ai_feedback_text,
        "critic_review": review.model_dump(),
    }


# ── Routing ───────────────────────────────────────────────────────────────────
def _route(state: IncrementalState) -> str:
    if (
        state["status"] in ("PASS", "CANCELLED")
        or state["iteration"] >= settings.ALT_MAX_INCREMENTAL_CRITIC_ITERATIONS + 1
    ):
        if state["status"] not in ("PASS", "CANCELLED"):
            logger.warning(
                "[INCREMENTAL] Max iterations reached at iteration=%d, moving forward",
                state["iteration"],
            )
        return "pass"
    return "fail"


# ── Graph ─────────────────────────────────────────────────────────────────────
def _build_graph():
    policy = RetryPolicy(
        max_attempts=3,
        initial_interval=1.0,
        backoff_factor=2.0,
        max_interval=10.0,
        jitter=True,
        retry_on=(Exception,),
    )

    graph = StateGraph(IncrementalState)
    graph.add_node("generate", node_generate, retry_policy=policy)
    graph.add_node("critic", node_critic, retry_policy=policy)
    graph.set_entry_point("generate")
    graph.add_edge("generate", "critic")
    graph.add_conditional_edges("critic", _route, {"pass": END, "fail": "generate"})

    return graph.compile()


# ── Public runner ─────────────────────────────────────────────────────────────
async def run_incremental_update(
    backlog: str,
    meeting_notes: str,
    image_notes: str = "[]",
    user_message: str = "",
    previous_output: str = "",
    context_mode: str = "FULL",
    skip_processing: bool = False,
    options: dict = None,
) -> dict:
    """
    backlog:       JSON string of the full backlog object.
    meeting_notes: JSON string of PDF note fragments array.
    image_notes:   JSON string of image fragments array. Pass "[]" if none.
                   The caller must NOT flatten to plain text — the LLM needs
                   the full fragment objects to build correct SourceRefs.
    previous_output:  JSON string of a prior run's output, for regeneration.
                      When provided, the generator copies it forward and applies
                      only the corrections in user_message.
    context_mode:     "FULL" (complete backlog) or "SUBSET" (selector-filtered).
                      Controls sequencing validation and add-code behaviour.
    """

    skip_processing = enforce_dev_only_skip_processing(skip_processing)
    _CACHE_DIR = pathlib.Path(
        "app/services/rfp_pipeline_v2_graph_service/sample_result/Incremental"
    )
    cache_file = _CACHE_DIR / "incremental_change.json"
    if skip_processing:
        if not cache_file.exists():
            return {
                "status": "failed",
                "error": (
                    f"No cached output found at '{cache_file}'. "
                    "Run once with skip_processing=False to generate and save the output."
                ),
            }
        logger.info("[INCREMENTAL CHANGE] Loading cached output from %s", cache_file)
        return json.loads(cache_file.read_text(encoding="utf-8"))

    # ── Normal processing ──

    app = _build_graph()
    try:
        result = await app.ainvoke(
            {
                "backlog": backlog,
                "meeting_notes": meeting_notes,
                "image_notes": image_notes,
                "user_message": user_message or "",
                "previous_output": previous_output or "",
                "context_mode": context_mode or "FULL",
                "output": "",
                "status": "",
                "ai_feedback": "",
                "iteration": 0,
                "options": options or {},
            }
        )
        critic_review = result.get("critic_review") or {}
        raw_flagged_items = critic_review.get("flagged_items", [])

        # The report (generation_metadata) is user-facing — it gets the
        # critic's plain-language user_summary, NOT the technical issue/
        # suggested_fix. Those stay internal: they're what node_critic already
        # flattened into ai_feedback (top-level, sibling to generation_metadata)
        # to drive the generator's next pass. Nothing about that flattening
        # changes here — this only reshapes what gets reported back out.
        flagged_items_report = [
            {
                "entity_id": item.get("entity_id"),
                "entity_type": item.get("entity_type"),
                "user_summary": item.get("user_summary"),
            }
            for item in raw_flagged_items
        ]

        final_status = (
            "PASS"
            if result["status"] == "PASS"
            else "CANCELLED"
            if result["status"] == "CANCELLED"
            else "FAIL_CORRECTION_EXHAUSTED"
        )

        # Best-effort parse of the generator's own output, purely to derive
        # has_changes / no_changes_explanation for the report. Never blocks or
        # alters `output`/`status`/`ai_feedback` on parse failure.
        parsed_output = _parse_output_for_report(result.get("output", ""))
        has_changes = any(
            len(parsed_output.get(key) or []) > 0
            for key in ("updates", "adds", "deletes", "source_enrichments")
        )
        no_changes_explanation = parsed_output.get("no_changes_explanation") or None

        # Safety net only: normal operation should have Criterion 12 catch a
        # missing explanation well before max iterations. This only fires if
        # the critic loop was exhausted with the field still unpopulated, and
        # is skipped on CANCELLED since there's nothing to explain there.
        if not has_changes and not no_changes_explanation and result["status"] != "CANCELLED":
            no_changes_explanation = (
                "No changes were proposed from this input, and the system wasn't "
                "able to determine a specific reason why. Please review the "
                "source notes and try again."
            )

        generation_metadata = {
            "final_status": final_status,
            "iteration_count": result["iteration"],
            "flagged_items": flagged_items_report,
            "has_changes": has_changes,
            "no_changes_explanation": no_changes_explanation,
            "summary": critic_review.get("summary", ""),
        }

        final = {
            "output": result["output"],
            "status": final_status,
            "ai_feedback": result["ai_feedback"],
            "iteration": result["iteration"],
            "generation_metadata": generation_metadata,
        }
        return final
    except Exception as e:
        logger.exception("[Incremental] Graph execution failed")
        logger.info(str(e))

        return {
            "status": "failed",
            "error": str(e),
        }
