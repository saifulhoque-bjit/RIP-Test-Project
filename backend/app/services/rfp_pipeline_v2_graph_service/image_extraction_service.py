from __future__ import annotations

import json
import os
import re
import time

from app.clients.llm_factory import get_image_llm
from app.core.config import settings
from app.schemas.image_extraction_schema import ImageExtractionOutput
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Prompt loader ─────────────────────────────────────────────────────────────


def _load_prompt() -> str:
    path = os.path.join(settings.ALT_PIPELINE_PROMPT_BASE_PATH, "image_extraction.md")
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _strip_output_tags(text: str) -> str:
    match = re.search(r"<OUTPUT>(.*?)</OUTPUT>", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def _build_fragment(image_id: str, extraction: ImageExtractionOutput) -> dict:
    return {
        "id": image_id,
        "source_id": image_id,
        "source_type": "image",
        "frag_type": extraction.image_type,
        "content": extraction.model_dump_json(),
        "bbox": [
            {
                "page": 1,
                "bbox": {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0},
                "confidence": 1.0,
            }
        ],
    }


# ── Public runner ─────────────────────────────────────────────────────────────


async def extract_image_fragment(
    image_bytes: bytes,
    image_id: str,
    mime_type: str = "image/jpeg",
    options: dict | None = None,
) -> dict:
    """Extract structured information from image bytes and return a note fragment.

    Parameters
    ----------
    image_bytes:
        Raw image bytes. The caller is responsible for loading the file.
    image_id:
        Caller-assigned UUID for this image. Used as both ``id`` and
        ``source_id`` in the returned fragment.
    mime_type:
        MIME type of the image (e.g. ``"image/jpeg"``, ``"image/png"``).
        Defaults to ``"image/jpeg"``.

    Returns
    -------
    dict
        Fragment dict compatible with the image_notes array expected by
        ``run_incremental_update``.

    Raises
    ------
    ValueError
        If the LLM response cannot be parsed as valid JSON or fails
        Pydantic validation.
    """
    prompt = _load_prompt()

    # replace the get_image_llm call
    opts = options or {}
    client = get_image_llm(
        provider=opts.get("llm_provider") or settings.IMAGE_EXTRACTION_PROVIDER,
        model_name=opts.get("llm_model") or settings.IMAGE_EXTRACTION_MODEL_NAME,
        max_tokens=settings.IMAGE_EXTRACTION_MAX_TOKENS,
        api_key=opts.get("llm_api_key"),
    )

    logger.info(
        "[IMAGE EXTRACTION] Starting  image_id=%s  provider=%s  model=%s  mime=%s",
        image_id,
        opts.get("llm_provider") or settings.IMAGE_EXTRACTION_PROVIDER,
        opts.get("llm_model") or settings.IMAGE_EXTRACTION_MODEL_NAME,
        mime_type,
    )

    start_ts = time.monotonic()
    try:
        raw_text = client.process_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=prompt,
        )
    except Exception as e:
        logger.exception("[Image Extraction] execution failed")
        logger.info(str(e))

        return {
            "status": "failed",
            "error": str(e),
        }
    latency_ms = int((time.monotonic() - start_ts) * 1000)

    logger.info("[IMAGE EXTRACTION] Done  latency_ms=%d  image_id=%s", latency_ms, image_id)

    clean_text = _strip_output_tags(raw_text)
    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError as e:
        logger.error("[IMAGE EXTRACTION] JSON parse failed: %s\nRaw: %s", e, raw_text[:500])
        raise ValueError(f"Image extraction returned invalid JSON: {e}") from e

    extraction = ImageExtractionOutput.model_validate(data)

    fragment = _build_fragment(image_id, extraction)
    logger.info(
        "[IMAGE EXTRACTION] Fragment built  image_id=%s  image_type=%s  confidence=%s",
        image_id,
        extraction.image_type,
        extraction.confidence,
    )
    return fragment
