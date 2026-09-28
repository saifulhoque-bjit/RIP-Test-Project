"""LlamaParse SDK client.

Wraps AsyncLlamaCloud, handles SDK init, and exposes a single parse method.
No business logic — transport only.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from llama_cloud import AsyncLlamaCloud
from llama_cloud.types.parsing_create_params import AgenticOptions

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()


# Custom parsing instruction for the agentic tier — externalized to a file so
# prompt wording can be reviewed/tuned without touching client code.
#
# The file's ENTIRE contents are sent verbatim to LlamaParse, and the agentic
# tier processes documents page by page — so this instruction is re-sent for
# every page of every parsed document. Cost scales as len(prompt) x page_count.
# Keep it to the 2-3 sentences LlamaParse's docs recommend; do not add banners,
# section headers, or explanatory comments to the file.
_DIAGRAM_PARSE_INSTRUCTION = _load_prompt("diagram_transcription_instruction.md")


class LlamaParserClient:
    """Wraps LlamaParse SDK. Uploads file bytes, triggers parse, returns raw dict."""

    async def parse(self, uuid: UUID, content: bytes) -> dict:
        # A fresh SDK client per call, never a cached module-level singleton:
        # every caller reaches this through `_run_async` (`asyncio.run(...)`),
        # which tears down its event loop after each call. A cached
        # AsyncLlamaCloud keeps an httpx connection pool bound to whichever
        # loop created it, so reusing it from a later call's different loop
        # mixes event loops unsafely. Constructing the client is cheap next to
        # the parse call itself, so there is no reuse benefit worth the risk.
        async with AsyncLlamaCloud(api_key=settings.LLAMA_CLOUD_API_KEY) as client:
            file_obj = await client.files.create(file=content, purpose="parse")

            result = await client.parsing.parse(
                file_id=file_obj.id,
                tier="agentic",
                version="latest",
                expand=["items"],
                disable_cache=True,
                agentic_options=AgenticOptions(custom_prompt=_DIAGRAM_PARSE_INSTRUCTION),
            )

        logger.info("LlamaParse parsed", extra={"file_id": uuid})
        return result.model_dump()
