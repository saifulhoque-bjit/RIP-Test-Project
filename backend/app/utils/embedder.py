"""Embedding utility.

Embeds the given content string using the OpenAI embedding model
configured via ``settings.OPENAI_EMBEDDING_MODEL``.
"""

from __future__ import annotations

from openai import OpenAI

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_opneAI_client: OpenAI | None = None

MAX_CHARS = 8_000


def _get_openAI_client() -> OpenAI:
    global _opneAI_client
    if _opneAI_client is None:
        _opneAI_client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _opneAI_client


def embed_content(text_to_embed: str) -> list[float]:
    """Embed a single content string using OpenAI text-embedding-3-small.

    Args:
        text_to_embed: The plain-text content to embed.

    Returns:
        A list of floats representing the embedding vector.
    """
    client = _get_openAI_client()

    model = settings.OPENAI_EMBEDDING_MODEL
    logger.debug("Embedding request: model=%s chars=%d", model, len(text_to_embed))
    response = client.embeddings.create(
        input=text_to_embed,
        model=model,
    )
    embedded_content = response.data[0].embedding

    logger.info(
        "Embedding complete: model=%s chars=%d dimensions=%d",
        model,
        len(text_to_embed),
        len(embedded_content),
    )
    return embedded_content
