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
from app.schemas.rfp_pipeline_v2_graph_schema import ModuleFeatureOutput
from app.utils.cancellable_llm import (
    LLMCancelled,
    ainvoke_cancellable,
    ainvoke_cancellable_structured,
)
from app.utils.common import enforce_dev_only_skip_processing
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ─── Load Prompts ────────────────────────────────────────────────────────────


def _load_prompt(filename: str) -> str:
    path = os.path.join(settings.ALT_PIPELINE_PROMPT_BASE_PATH, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


PROMPT_GEN = _load_prompt("02A_business_architecture_generator.md")
PROMPT_CRIT = _load_prompt("02A_business_architecture_critic.md")

# ─── State ───────────────────────────────────────────────────────────────────


class State(TypedDict, total=False):
    parsed_items: str
    previous_output: str
    human_feedback: str
    ai_feedback: str
    output: str
    status: str
    iteration: int
    options: dict


# ─── Nodes ───────────────────────────────────────────────────────────────────


async def node_generate(state: State) -> dict:
    iteration = state["iteration"] + 1
    logger.info("[MODULE/FEATURE GENERATOR] Starting iteration %d", iteration)

    opts = state.get("options") or {}
    request_id = opts.get("request_id")
    if request_id and task_control.is_request_cancelled(request_id):
        logger.info("[MODULE/FEATURE GENERATOR] Skipping — request %s cancelled", request_id)
        return {**state, "status": "CANCELLED"}

    provider = opts.get("llm_provider") or settings.ALT_ARCHITECT_MODEL_PROVIDER
    model_name = opts.get("llm_model") or settings.ALT_ARCHITECT_MODEL_NAME
    api_key = opts.get("llm_api_key")

    llm = get_llm(
        provider=provider,
        model_name=model_name,
        temperature=settings.ALT_ARCHITECT_TEMPERATURE,
        api_key=api_key,
    )

    if provider == "deepseek":
        structured_llm = llm.with_structured_output(ModuleFeatureOutput, method="json_mode")
    else:
        structured_llm = llm.with_structured_output(ModuleFeatureOutput, method="function_calling")
    # Build system prompt with appended context
    system_content = PROMPT_GEN
    if state["human_feedback"]:
        logger.info("[MODULE/FEATURE GENERATOR] Incorporating human feedback")
        system_content += f"\n\n---\nHUMAN REVIEW FEEDBACK:\n{state['human_feedback']}"
    if state["ai_feedback"]:
        logger.info("[MODULE/FEATURE GENERATOR] Incorporating AI critic feedback")
        system_content += f"\n\n---\nAI CRITIC FEEDBACK:\n{state['ai_feedback']}"

    # Build user message
    user_parts = []
    if state["previous_output"]:
        user_parts.append(f"PREVIOUS DRAFT:\n{state['previous_output']}")
    user_parts.append(f"RFP CONTENT:\n{state['parsed_items']}")

    schema_json = json.dumps(ModuleFeatureOutput.model_json_schema(), indent=2)
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
            output: ModuleFeatureOutput = await ainvoke_cancellable(
                structured_llm, messages, request_id=request_id
            )
        else:
            output: ModuleFeatureOutput = await ainvoke_cancellable_structured(
                structured_llm, messages, request_id=request_id
            )
    except LLMCancelled:
        logger.info("[MODULE/FEATURE GENERATOR] Cancelled mid-stream — request %s", request_id)
        return {**state, "status": "CANCELLED"}
    except Exception as exc:
        classification = classify_llm_error(exc, provider=provider)
        if not classification.retryable:
            logger.error(
                "[MODULE/FEATURE GENERATOR] Non-retryable LLM error (reason=%s): %s",
                classification.reason.value,
                classification.message,
            )
            raise NonRetryableLLMError(classification, original=exc) from exc
        raise
    latency_ms = int((time.monotonic() - start_ts) * 1000)

    logger.info(
        "[MODULE/FEATURE GENERATOR] Draft generated iteration=%d latency_ms=%d",
        iteration,
        latency_ms,
    )
    draft_json = output.model_dump_json()
    return {**state, "output": draft_json, "previous_output": draft_json, "iteration": iteration}


async def node_critic(state: State) -> dict:
    if state.get("status") == "CANCELLED":
        return state

    logger.info("[MODULE/FEATURE CRITIC] Reviewing draft iteration=%d", state["iteration"])

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

    # messages = [
    #     SystemMessage(content=PROMPT_CRIT),
    #     HumanMessage(content=f"DRAFT TO REVIEW:\n{state['output']}"),
    # ]
    messages = [
        SystemMessage(content=PROMPT_CRIT),
        HumanMessage(
            content=f"RFP SOURCE CHUNKS:\n{state['parsed_items']}\n\n---\n\nDRAFT TO REVIEW:\n{state['output']}"
        ),
    ]

    start_ts = time.monotonic()
    try:
        response = await ainvoke_cancellable(llm, messages, request_id=request_id)
    except LLMCancelled:
        logger.info("[MODULE/FEATURE CRITIC] Cancelled mid-stream — request %s", request_id)
        return {**state, "status": "CANCELLED"}
    except Exception as exc:
        classification = classify_llm_error(exc, provider=provider)
        if not classification.retryable:
            logger.error(
                "[MODULE/FEATURE CRITIC] Non-retryable LLM error (reason=%s): %s",
                classification.reason.value,
                classification.message,
            )
            raise NonRetryableLLMError(classification, original=exc) from exc
        raise
    latency_ms = int((time.monotonic() - start_ts) * 1000)
    # text = response.content

    content = response.content
    if isinstance(content, list):
        text = "".join(
            block.get("text", "") if isinstance(block, dict) else getattr(block, "text", str(block))
            for block in content
        )
    else:
        text = content

    status_m = re.search(r"<STATUS>(.*?)</STATUS>", text, re.DOTALL)
    feedback_m = re.search(r"<FEEDBACK>(.*?)</FEEDBACK>", text, re.DOTALL)
    suggested_m = re.search(r"<SUGGESTED_FIX>(.*?)</SUGGESTED_FIX>", text, re.DOTALL)

    status_val = status_m.group(1).strip() if status_m else "UNKNOWN"
    feedback_val = feedback_m.group(1).strip() if feedback_m else ""
    fix_val = suggested_m.group(1).strip() if suggested_m else ""

    logger.info("[MODULE/FEATURE CRITIC] status=%s latency_ms=%d", status_val, latency_ms)
    logger.info("[MODULE/FEATURE CRITIC] feedback=%s", feedback_val)
    if fix_val:
        logger.info("[MODULE/FEATURE CRITIC] suggested_fix=%s", fix_val)

    return {**state, "ai_feedback": fix_val, "status": status_val}


# ─── Routing ─────────────────────────────────────────────────────────────────


def _route(state: State) -> str:
    if (
        state["status"] in ("PASS", "CANCELLED")
        or state["iteration"] >= settings.ALT_MAX_CRITIC_ITERATIONS + 1
    ):
        if state["status"] not in ("PASS", "CANCELLED"):
            logger.warning(
                "[MODULE/FEATURE] Max iterations reached at iteration=%d, moving forward",
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


async def run_module_feature(
    fragments: str,
    modules_and_features: str = "",
    feedback: str = "",
    skip_processing: bool = False,
    options: dict = None,
) -> dict:
    skip_processing = enforce_dev_only_skip_processing(skip_processing)
    _CACHE_DIR = pathlib.Path("app/services/rfp_pipeline_v2_graph_service/sample_result/RFP")
    cache_file = _CACHE_DIR / "module_feature.json"
    if skip_processing:
        if not cache_file.exists():
            raise FileNotFoundError(
                f"No cached output found at '{cache_file}'. "
                "Run once with skip_processing=False to generate and save the output."
            )
        logger.info("[MODULE FEATURE] Loading cached output from %s", cache_file)
        return json.loads(cache_file.read_text(encoding="utf-8"))

    # ── Normal processing ──
    iteration = 0
    app = _build_graph()
    initial_state: State = {
        "parsed_items": fragments,
        "previous_output": modules_and_features,
        "human_feedback": feedback,
        "ai_feedback": "",
        "output": "",
        "status": "",
        "iteration": iteration,
        "options": options or {},
    }
    try:
        result = await app.ainvoke(initial_state)
        final = {
            "output": result["output"],
            "status": result["status"],
            "ai_feedback": result["ai_feedback"],
            "iteration": result["iteration"],
        }
        return final
    except Exception as e:
        logger.exception("[MODULE FEATURE] Graph execution failed")
        logger.info(str(e))

        return {
            "status": "failed",
            "error": str(e),
        }
