import json
import os
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy
from typing_extensions import TypedDict

from app.clients.llm_factory import get_llm
from app.core import task_control
from app.core.config import settings
from app.core.llm_errors import NonRetryableLLMError, classify_llm_error
from app.schemas.rfp_pipeline_v2_graph_schema import AgileBacklogOutput, CriticReviewSchema
from app.utils.cancellable_llm import (
    LLMCancelled,
    ainvoke_cancellable,
    ainvoke_cancellable_structured,
)
from app.utils.common import enforce_dev_only_skip_processing
from app.utils.logger import get_logger

logger = get_logger(__name__)

import pathlib

# ─── Load Prompts ────────────────────────────────────────────────────────────


def _load_prompt(filename: str) -> str:
    path = os.path.join(settings.ALT_PIPELINE_PROMPT_BASE_PATH, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


PROMPT_GEN = _load_prompt("02B_agile_backlog_generator.md")
PROMPT_CRIT = _load_prompt("02B_agile_backlog_critic.md")

# ─── State ───────────────────────────────────────────────────────────────────


class State(TypedDict, total=False):
    parsed_items: str
    output_2a: str
    previous_output: str
    human_feedback: str
    ai_feedback: str
    output: str
    status: str
    iteration: int
    options: dict
    critic_review: dict


# ─── Nodes ───────────────────────────────────────────────────────────────────


async def node_generate(state: State) -> dict:
    iteration = state["iteration"] + 1
    logger.info("[AGILE BACKLOG GENERATOR] Starting iteration %d", iteration)

    opts = state.get("options") or {}
    request_id = opts.get("request_id")
    if request_id and task_control.is_request_cancelled(request_id):
        logger.info("[AGILE BACKLOG GENERATOR] Skipping — request %s cancelled", request_id)
        return {**state, "status": "CANCELLED"}

    provider = opts.get("llm_provider") or settings.ALT_ARCHITECT_MODEL_PROVIDER
    model_name = opts.get("llm_model") or settings.ALT_ARCHITECT_MODEL_NAME
    api_key = opts.get("llm_api_key")

    llm = get_llm(
        provider=provider,
        model_name=model_name,
        api_key=api_key,
        temperature=settings.ALT_ARCHITECT_TEMPERATURE,
    )
    if provider == "deepseek":
        structured_llm = llm.with_structured_output(AgileBacklogOutput, method="json_mode")
    else:
        structured_llm = llm.with_structured_output(AgileBacklogOutput, method="function_calling")

    system_content = PROMPT_GEN
    if state["human_feedback"]:
        logger.info("[AGILE BACKLOG GENERATOR] Incorporating human feedback")
        system_content += f"\n\n---\nHUMAN REVIEW FEEDBACK:\n{state['human_feedback']}"
    if state["ai_feedback"]:
        logger.info("[AGILE BACKLOG GENERATOR] Incorporating AI critic feedback")
        system_content += f"\n\n---\nAI CRITIC FEEDBACK:\n{state['ai_feedback']}"

    user_parts = []
    if state["previous_output"]:
        user_parts.append(f"PREVIOUS DRAFT:\n{state['previous_output']}")
    user_parts.append(f"PHASE 2A OUTPUT:\n{state['output_2a']}")
    user_parts.append(f"ORIGINAL RFP CHUNKS:\n{state['parsed_items']}")

    schema_json = json.dumps(AgileBacklogOutput.model_json_schema(), indent=2)
    user_parts.append(
        "Respond with a JSON object that exactly matches this schema:\n" + schema_json
    )

    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content="\n\n---\n".join(user_parts)),
    ]

    start_ts = time.monotonic()
    try:
        if provider == "deepseek":
            output: AgileBacklogOutput = await ainvoke_cancellable(
                structured_llm, messages, request_id=request_id
            )
        else:
            output: AgileBacklogOutput = await ainvoke_cancellable_structured(
                structured_llm, messages, request_id=request_id
            )
    except LLMCancelled:
        logger.info("[AGILE BACKLOG GENERATOR] Cancelled mid-stream — request %s", request_id)
        return {**state, "status": "CANCELLED"}
    except Exception as exc:
        classification = classify_llm_error(exc, provider=provider)
        if not classification.retryable:
            logger.error(
                "[AGILE BACKLOG GENERATOR] Non-retryable LLM error (reason=%s): %s",
                classification.reason.value,
                classification.message,
            )
            raise NonRetryableLLMError(classification, original=exc) from exc
        raise
    latency_ms = int((time.monotonic() - start_ts) * 1000)

    logger.info(
        "[AGILE BACKLOG GENERATOR] Draft generated iteration=%d latency_ms=%d",
        iteration,
        latency_ms,
    )
    draft_json = output.model_dump_json()
    return {**state, "output": draft_json, "previous_output": draft_json, "iteration": iteration}


async def node_critic(state: State) -> dict:
    if state.get("status") == "CANCELLED":
        return state

    logger.info("[AGILE BACKLOG CRITIC] Reviewing draft iteration=%d", state["iteration"])

    opts = state.get("options") or {}
    provider = opts.get("llm_provider") or settings.ALT_CRITIC_MODEL_PROVIDER
    model_name = opts.get("llm_model") or settings.ALT_CRITIC_MODEL_NAME
    request_id = opts.get("request_id")
    api_key = opts.get("llm_api_key")

    llm = get_llm(
        provider=provider,
        model_name=model_name,
        temperature=settings.ALT_CRITIC_TEMPERATURE,
        api_key=api_key,
    )
    if provider == "deepseek":
        structured_llm = llm.with_structured_output(CriticReviewSchema, method="json_mode")
    else:
        structured_llm = llm.with_structured_output(CriticReviewSchema, method="function_calling")

    messages = [
        SystemMessage(content=PROMPT_CRIT),
        HumanMessage(
            content=f"MODULES AND FEATURES LIST:\n{state['output_2a']}\n\n---\n\nDRAFT TO REVIEW:\n{state['output']}"
        ),
    ]

    start_ts = time.monotonic()
    try:
        if provider == "deepseek":
            review: CriticReviewSchema = await ainvoke_cancellable(
                structured_llm, messages, request_id=request_id
            )
        else:
            review: CriticReviewSchema = await ainvoke_cancellable_structured(
                structured_llm, messages, request_id=request_id
            )
    except LLMCancelled:
        logger.info("[AGILE BACKLOG CRITIC] Cancelled mid-stream — request %s", request_id)
        return {**state, "status": "CANCELLED"}
    except Exception as exc:
        classification = classify_llm_error(exc, provider=provider)
        if not classification.retryable:
            logger.error(
                "[AGILE BACKLOG CRITIC] Non-retryable LLM error (reason=%s): %s",
                classification.reason.value,
                classification.message,
            )
            raise NonRetryableLLMError(classification, original=exc) from exc
        raise
    latency_ms = int((time.monotonic() - start_ts) * 1000)

    # Flatten flagged_items into the same style of imperative text the generator already consumes
    ai_feedback_text = "\n".join(
        f"- [{item.entity_type} {item.entity_id}]: {item.suggested_fix}"
        for item in review.flagged_items
    )

    logger.info(
        "[AGILE BACKLOG CRITIC] status=%s latency_ms=%d flagged=%d",
        review.status,
        latency_ms,
        len(review.flagged_items),
    )
    logger.info("[AGILE BACKLOG CRITIC] summary=%s", review.summary)
    if ai_feedback_text:
        logger.info("[AGILE BACKLOG CRITIC] flattened_feedback=%s", ai_feedback_text)

    return {
        **state,
        "ai_feedback": ai_feedback_text,
        "status": review.status,
        "critic_review": review.model_dump(),
    }


# ─── Routing ─────────────────────────────────────────────────────────────────


def _route(state: State) -> str:
    if (
        state["status"] in ("PASS", "CANCELLED")
        or state["iteration"] >= settings.ALT_MAX_CRITIC_ITERATIONS + 1
    ):
        if state["status"] not in ("PASS", "CANCELLED"):
            logger.warning(
                "[AGILE BACKLOG] Max iterations reached at iteration=%d, moving forward",
                state["iteration"],
            )
        return "pass"
    return "fail"


# ─── Build Graph ─────────────────────────────────────────────────────────────


def _build_graph():
    policy = RetryPolicy(
        max_attempts=3,  # Total attempts (including the first)
        initial_interval=1.0,  # Time to wait before 1st retry (in seconds)
        backoff_factor=2.0,  # Multiplier for interval growth
        max_interval=10.0,  # Maximum backoff interval (in seconds)
        jitter=True,  # Adds randomness to prevent thundering herd
        retry_on=(Exception,),
    )

    graph = StateGraph(State)

    graph.add_node("generate", node_generate, retry_policy=policy)
    graph.add_node("critic", node_critic, retry_policy=policy)

    graph.set_entry_point("generate")
    graph.add_edge("generate", "critic")
    graph.add_conditional_edges(
        "critic",
        _route,
        {
            "pass": END,
            "fail": "generate",
        },
    )

    return graph.compile()


# ─── Public Entry Point ───────────────────────────────────────────────────────


async def run_agile_backlog(
    fragments: str,
    modules_and_features: str,
    user_stories: str | None = None,
    feedback: str = "",
    skip_processing: bool = False,
    options: dict = None,
) -> dict:
    skip_processing = enforce_dev_only_skip_processing(skip_processing)
    _CACHE_DIR = pathlib.Path("app/services/rfp_pipeline_v2_graph_service/sample_result/RFP")
    cache_file = _CACHE_DIR / "agile_backlog.json"
    if skip_processing:
        if not cache_file.exists():
            return {
                "status": "failed",
                "error": (
                    f"No cached output found at '{cache_file}'. "
                    "Run once with skip_processing=False to generate and save the output."
                ),
            }
        logger.info("[AGILE BACKLOG] Loading cached output from %s", cache_file)
        return json.loads(cache_file.read_text(encoding="utf-8"))

    # ── Normal processing ──
    iteration: int = 0
    app = _build_graph()
    initial_state: State = {
        "parsed_items": fragments,
        "output_2a": modules_and_features,
        "previous_output": user_stories or "",
        "human_feedback": feedback,
        "ai_feedback": "",
        "output": "",
        "status": "",
        "iteration": iteration,
        "options": options or {},
    }
    try:
        result = await app.ainvoke(initial_state)

        review = result.get("critic_review") or {}
        flagged_items = review.get("flagged_items", [])
        final_status = (
            "PASS"
            if result["status"] == "PASS"
            else "CANCELLED"
            if result["status"] == "CANCELLED"
            else "FAIL_CORRECTION_EXHAUSTED"
        )

        generation_metadata = {
            "final_status": final_status,
            "iteration_count": result["iteration"],
            "flagged_items": flagged_items,
            "flagged_story_ids": [
                item["entity_id"] for item in flagged_items if item["entity_type"] == "story"
            ],
            "summary": review.get("summary", ""),
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
        logger.exception("[Agile Backlog] Graph execution failed")
        logger.info(str(e))
        return {
            "status": "failed",
            "error": str(e),
        }
