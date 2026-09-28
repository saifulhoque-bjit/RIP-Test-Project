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
from app.schemas.rfp_pipeline_v2_graph_schema import (
    FeatureContext,
    StoryFeedbackItem,
    StoryPatch,
)
from app.utils.cancellable_llm import (
    LLMCancelled,
    ainvoke_cancellable,
    ainvoke_cancellable_structured,
)
from app.utils.common import enforce_dev_only_skip_processing
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ─── Load Prompts ─────────────────────────────────────────────────────────────


def _load_prompt(filename: str) -> str:
    path = os.path.join(settings.ALT_PIPELINE_PROMPT_BASE_PATH, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


PROMPT_GEN = _load_prompt("story_patch_generator.md")
PROMPT_CRIT = _load_prompt("story_patch_critic.md")

# ─── State ────────────────────────────────────────────────────────────────────


class PatchState(TypedDict, total=False):
    story_feedbacks: list[dict]  # serialised list[StoryFeedbackItem]
    feature_contexts: list[dict]  # serialised list[FeatureContext]
    persona_glossary: str
    valid_sources: str  # enriched pool: source_id→pages→bboxes+content
    ai_feedback: str
    output: str
    status: str
    iteration: int
    options: dict


# ─── Source enforcement ───────────────────────────────────────────────────────


def _enforce_valid_sources(patch_json: str, valid_sources_json: str) -> str:
    """
    Strip any fragment_ids from revised_stories that aren't in the valid pool.
    Also corrects source_id/page placement to match the pool exactly.
    Fallback uses the first fragment from the pool if a story ends up sourceless.
    """
    patch = json.loads(patch_json)
    pool = json.loads(valid_sources_json)

    # Build lookup: fragment_id -> { source_id, page, bbox }
    valid_fragment_map: dict[str, dict] = {}
    for src in pool:
        source_id = src.get("source_id", "")
        for pg in src.get("pages", []):
            page_num = pg.get("page")
            for entry in pg.get("bboxes", []):
                fid = entry.get("fragment_id")
                if fid:
                    valid_fragment_map[fid] = {
                        "source_id": source_id,
                        "page": page_num,
                        "bbox": entry.get("bbox", {}),
                    }

    for story in patch["revised_stories"]:
        cleaned_sources: list[dict] = []
        for src in story.get("sources", []):
            source_id = src.get("source_id", "")
            valid_pages = []
            for pg in src.get("pages", []):
                page_num = pg.get("page")
                valid_bboxes = [
                    entry
                    for entry in pg.get("bboxes", [])
                    if entry.get("fragment_id") in valid_fragment_map
                    and valid_fragment_map[entry["fragment_id"]]["source_id"] == source_id
                    and valid_fragment_map[entry["fragment_id"]]["page"] == page_num
                ]
                if valid_bboxes:
                    valid_pages.append({"page": page_num, "bboxes": valid_bboxes})
            if valid_pages:
                cleaned_sources.append({"source_id": source_id, "pages": valid_pages})

        # Fallback: if nothing survived, anchor to first valid fragment
        if not cleaned_sources and valid_fragment_map:
            fid, meta = next(iter(valid_fragment_map.items()))
            cleaned_sources = [
                {
                    "source_id": meta["source_id"],
                    "pages": [
                        {
                            "page": meta["page"],
                            "bboxes": [{"fragment_id": fid, "bbox": meta["bbox"]}],
                        }
                    ],
                }
            ]

        story["sources"] = cleaned_sources

    return json.dumps(patch, ensure_ascii=False)


# ─── Nodes ────────────────────────────────────────────────────────────────────


async def node_generate(state: PatchState) -> dict:
    iteration = state["iteration"] + 1
    logger.info("[STORY PATCH GENERATOR] Starting iteration %d", iteration)

    opts = state.get("options") or {}
    request_id = opts.get("request_id")
    if request_id and task_control.is_request_cancelled(request_id):
        logger.info("[STORY PATCH GENERATOR] Skipping — request %s cancelled", request_id)
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
    structured_llm = llm.with_structured_output(
        StoryPatch,
        method="json_mode" if provider == "deepseek" else "function_calling",
    )

    system_content = PROMPT_GEN
    if state.get("ai_feedback"):
        logger.info("[STORY PATCH GENERATOR] Incorporating AI critic feedback")
        system_content += (
            f"\n\n---\nAI CRITIC FEEDBACK (address all points):\n{state['ai_feedback']}"
        )

    # ── Build human message ───────────────────────────────────────────────────
    # Index feature contexts by fea_code for deduplication
    feature_map: dict[str, dict] = {fc["fea_code"]: fc for fc in state["feature_contexts"]}

    story_blocks: list[str] = []
    seen_features: set[str] = set()

    for item in state["story_feedbacks"]:
        story = item["story"]
        usc = story["user_story_code"]
        fea_code = ".".join(usc.split(" ")[1].split(".")[:2])  # "U.S 1.2.3" → "1.2"

        # Story block
        block_lines = [
            f"=== TARGET STORY: {usc} ===",
            json.dumps(story, ensure_ascii=False),
            "",
            "FEEDBACK:",
        ]

        if item.get("overall_feedback"):
            block_lines.append(f"  [Whole story] → {item['overall_feedback']}")
        for sel in item.get("specific_feedback") or []:
            block_lines.append(f'  [On: "{sel["selected_text"]}"] → {sel["selected_feedback"]}')

        # Feature context block — deduplicated
        if fea_code not in seen_features and fea_code in feature_map:
            fc = feature_map[fea_code]
            seen_features.add(fea_code)
            siblings_lines = (
                "\n".join(
                    f"    - {s['user_story_code']} | {s['title']} "
                    f"| i_want_to: {s['i_want_to']} | so_that: {s['so_that']}"
                    for s in fc.get("sibling_stories", [])
                )
                or "    (none)"
            )
            block_lines += [
                "",
                f"FEATURE CONTEXT → {fea_code} | {fc['name']}",
                f"  {fc['description']}",
                "  SIBLING STORIES (read-only — do not duplicate scope):",
                siblings_lines,
            ]

        story_blocks.append("\n".join(block_lines))

    user_parts = [
        "\n\n---\n".join(story_blocks),
        f"VALID SOURCES (fragment_ids to cite; content is grounding-only, do not copy):\n{state['valid_sources']}",
        f"PERSONA GLOSSARY:\n{state['persona_glossary']}",
        f"Respond with a JSON object matching this schema:\n{json.dumps(StoryPatch.model_json_schema(), indent=2)}",
    ]

    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content="\n\n---\n".join(user_parts)),
    ]

    start_ts = time.monotonic()
    try:
        if provider == "deepseek":
            output: StoryPatch = await ainvoke_cancellable(
                structured_llm, messages, request_id=request_id
            )
        else:
            output: StoryPatch = await ainvoke_cancellable_structured(
                structured_llm, messages, request_id=request_id
            )
    except LLMCancelled:
        logger.info("[STORY PATCH GENERATOR] Cancelled mid-stream — request %s", request_id)
        return {**state, "status": "CANCELLED"}
    except Exception as exc:
        classification = classify_llm_error(exc, provider=provider)
        if not classification.retryable:
            logger.error(
                "[STORY PATCH GENERATOR] Non-retryable LLM error (reason=%s): %s",
                classification.reason.value,
                classification.message,
            )
            raise NonRetryableLLMError(classification, original=exc) from exc
        raise
    latency_ms = int((time.monotonic() - start_ts) * 1000)
    logger.info(
        "[STORY PATCH GENERATOR] Draft generated iteration=%d latency_ms=%d", iteration, latency_ms
    )

    clean_output = _enforce_valid_sources(output.model_dump_json(), state["valid_sources"])
    return {**state, "output": clean_output, "iteration": iteration}


async def node_critic(state: PatchState) -> dict:
    if state.get("status") == "CANCELLED":
        return state

    logger.info("[STORY PATCH CRITIC] Reviewing patch iteration=%d", state["iteration"])

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

    # Reconstruct the same story+feedback blocks so critic has identical context
    feature_map: dict[str, dict] = {fc["fea_code"]: fc for fc in state["feature_contexts"]}
    story_blocks: list[str] = []
    seen_features: set[str] = set()

    for item in state["story_feedbacks"]:
        story = item["story"]
        usc = story["user_story_code"]
        fea_code = ".".join(usc.split(" ")[1].split(".")[:2])

        block_lines = [
            f"=== TARGET STORY: {usc} ===",
            json.dumps(story, ensure_ascii=False),
            "",
            "FEEDBACK:",
        ]
        if item.get("overall_feedback"):
            block_lines.append(f"  [Whole story] → {item['overall_feedback']}")
        for sel in item.get("specific_feedback") or []:
            block_lines.append(f'  [On: "{sel["selected_text"]}"] → {sel["selected_feedback"]}')

        if fea_code not in seen_features and fea_code in feature_map:
            fc = feature_map[fea_code]
            seen_features.add(fea_code)
            siblings_lines = (
                "\n".join(
                    f"    - {s['user_story_code']} | {s['title']}"
                    for s in fc.get("sibling_stories", [])
                )
                or "    (none)"
            )
            block_lines += [
                "",
                f"FEATURE CONTEXT → {fea_code} | {fc['name']}",
                "  SIBLING STORIES:",
                siblings_lines,
            ]

        story_blocks.append("\n".join(block_lines))

    user_parts = [
        "\n\n---\n".join(story_blocks),
        f"PATCH OUTPUT:\n{state['output']}",
        f"VALID SOURCES:\n{state['valid_sources']}",
        f"PERSONA GLOSSARY:\n{state['persona_glossary']}",
    ]

    messages = [
        SystemMessage(content=PROMPT_CRIT),
        HumanMessage(content="\n\n---\n".join(user_parts)),
    ]

    start_ts = time.monotonic()
    try:
        response = await ainvoke_cancellable(llm, messages, request_id=request_id)
    except LLMCancelled:
        logger.info("[STORY PATCH CRITIC] Cancelled mid-stream — request %s", request_id)
        return {**state, "status": "CANCELLED"}
    except Exception as exc:
        classification = classify_llm_error(exc, provider=provider)
        if not classification.retryable:
            logger.error(
                "[STORY PATCH CRITIC] Non-retryable LLM error (reason=%s): %s",
                classification.reason.value,
                classification.message,
            )
            raise NonRetryableLLMError(classification, original=exc) from exc
        raise
    latency_ms = int((time.monotonic() - start_ts) * 1000)

    content = response.content
    text = (
        "".join(
            b.get("text", "") if isinstance(b, dict) else getattr(b, "text", str(b))
            for b in content
        )
        if isinstance(content, list)
        else content
    )

    status_val = (
        (
            re.search(r"<STATUS>(.*?)</STATUS>", text, re.DOTALL)
            or type("", (), {"group": lambda *_: "UNKNOWN"})()
        )
        .group(1)
        .strip()
    )
    feedback_val = (
        (
            re.search(r"<FEEDBACK>(.*?)</FEEDBACK>", text, re.DOTALL)
            or type("", (), {"group": lambda *_: ""})()
        )
        .group(1)
        .strip()
    )
    fix_val = (
        (
            re.search(r"<SUGGESTED_FIX>(.*?)</SUGGESTED_FIX>", text, re.DOTALL)
            or type("", (), {"group": lambda *_: ""})()
        )
        .group(1)
        .strip()
    )

    logger.info("[STORY PATCH CRITIC] status=%s latency_ms=%d", status_val, latency_ms)
    logger.info("[STORY PATCH CRITIC] feedback=%s", feedback_val)
    if fix_val:
        logger.info("[STORY PATCH CRITIC] suggested_fix=%s", fix_val)

    return {**state, "ai_feedback": fix_val, "status": status_val}


# ─── Routing ──────────────────────────────────────────────────────────────────


def _route(state: PatchState) -> str:
    if (
        state["status"] in ("PASS", "CANCELLED")
        or state["iteration"] >= settings.ALT_MAX_PATCH_CRITIC_ITERATIONS + 1
    ):
        if state["status"] not in ("PASS", "CANCELLED"):
            logger.warning(
                "[STORY PATCH] Max iterations reached at iteration=%d, moving forward",
                state["iteration"],
            )
        return "pass"
    return "fail"


# ─── Graph ────────────────────────────────────────────────────────────────────


def _build_graph():
    policy = RetryPolicy(
        max_attempts=3,
        initial_interval=1.0,
        backoff_factor=2.0,
        max_interval=10.0,
        jitter=True,
        retry_on=(Exception,),
    )
    graph = StateGraph(PatchState)
    graph.add_node("generate", node_generate, retry_policy=policy)
    graph.add_node("critic", node_critic, retry_policy=policy)
    graph.set_entry_point("generate")
    graph.add_edge("generate", "critic")
    graph.add_conditional_edges("critic", _route, {"pass": END, "fail": "generate"})
    return graph.compile()


# ─── Entry Point ──────────────────────────────────────────────────────────────


async def run_agile_backlog_patch(
    story_feedbacks: list[StoryFeedbackItem],
    feature_contexts: list[FeatureContext],
    persona_glossary: str,
    valid_sources: str,
    skip_processing: bool = False,
    options: dict = None,
) -> dict:
    skip_processing = enforce_dev_only_skip_processing(skip_processing)
    _CACHE_DIR = pathlib.Path("app/services/rfp_pipeline_v2_graph_service/sample_result/RFP")
    cache_file = _CACHE_DIR / "agile_backlog_patch.json"

    if skip_processing:
        if not cache_file.exists():
            return {
                "status": "failed",
                "error": (
                    f"No cached output found at '{cache_file}'. "
                    "Run once with skip_processing=False to generate and save the output."
                ),
            }
        logger.info("[AGILE BACKLOG PATCH] Loading cached output from %s", cache_file)
        return json.loads(cache_file.read_text(encoding="utf-8"))

    # Serialise Pydantic input models to plain dicts for state
    feedbacks_raw = [f.model_dump() for f in story_feedbacks]
    contexts_raw = [c.model_dump() for c in feature_contexts]

    app = _build_graph()
    try:
        result = await app.ainvoke(
            {
                "story_feedbacks": feedbacks_raw,
                "feature_contexts": contexts_raw,
                "persona_glossary": persona_glossary,
                "valid_sources": valid_sources,
                "ai_feedback": "",
                "output": "",
                "status": "",
                "iteration": 0,
                "options": options or {},
            }
        )

        final = {
            "output": result["output"],
            "status": result["status"],
            "ai_feedback": result["ai_feedback"],
        }
        return final
    except Exception as e:
        logger.exception("[Agile Backlog Patch] Graph execution failed")
        logger.info(str(e))

        return {
            "status": "failed",
            "error": str(e),
        }
