"""Multi-provider LLM factory.

Returns a LangChain ``BaseChatModel`` configured for the requested provider
and model.  The four AI-workflow agents (Skeleton Builder, Architect, Critic,
Global Escalation) each resolve their own model at call-site by reading the
per-agent config values.

Supported providers
───────────────────
- ``openai``   → ``langchain_openai.ChatOpenAI``
- ``anthropic`` → ``langchain_anthropic.ChatAnthropic``
- ``google``   → ``langchain_google_genai.ChatGoogleGenerativeAI``

API keys are read from the application settings (never passed directly).
"""

from __future__ import annotations

from typing import NamedTuple

import httpx
from langchain_core.language_models.chat_models import BaseChatModel

from app.core.exceptions import AIServiceError, AIWorkflowError
from app.utils.logger import get_logger

logger = get_logger(__name__)

_SUPPORTED_PROVIDERS = frozenset({"openai", "anthropic", "google", "deepseek"})

_DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"

# Only DeepSeek exposes a documented account-balance endpoint reachable with a
# plain API key (``GET /user/balance``). Anthropic, OpenAI, and Google require
# org/console-level billing access, not a per-key call — there is no real
# endpoint to wire up for them.
BALANCE_SUPPORTED_PROVIDERS = frozenset({"deepseek"})

_DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"
_DEEPSEEK_BALANCE_TIMEOUT_SECONDS = 10.0


class LLMBalance(NamedTuple):
    balance: float
    currency: str


async def get_balance(provider: str, api_key: str) -> LLMBalance:
    """Fetch the remaining account balance for *provider* using a caller-supplied *api_key*.

    Parameters
    ----------
    provider:
        Must be a member of :data:`BALANCE_SUPPORTED_PROVIDERS` — callers
        should check membership themselves for a clean business error;
        this function raises :class:`AIWorkflowError` otherwise as a
        defense-in-depth guard.
    api_key:
        The key to use — used directly, never read from settings.

    Raises:
        AIWorkflowError: If *provider* has no supported balance endpoint.
        AIServiceError: If the provider's API call fails, times out, or
            returns an unexpected response.
    """
    provider = provider.lower().strip()

    if provider not in BALANCE_SUPPORTED_PROVIDERS:
        raise AIWorkflowError(f"Balance lookup is not supported for provider '{provider}'.")

    try:
        async with httpx.AsyncClient(timeout=_DEEPSEEK_BALANCE_TIMEOUT_SECONDS) as client:
            response = await client.get(
                _DEEPSEEK_BALANCE_URL,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        raise AIServiceError("DeepSeek balance endpoint timed out or is unreachable.") from exc

    if response.status_code != 200:
        raise AIServiceError(
            f"DeepSeek balance endpoint returned {response.status_code}: {response.text[:500]}"
        )

    balance_infos = response.json().get("balance_infos") or []
    if not balance_infos:
        raise AIServiceError("DeepSeek balance endpoint returned no balance information.")

    info = balance_infos[0]
    try:
        return LLMBalance(balance=float(info["total_balance"]), currency=info["currency"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AIServiceError("DeepSeek balance endpoint returned an unexpected payload.") from exc


async def probe_api_key(provider: str, api_key: str) -> None:
    """Make the cheapest authenticated call to *provider* using a caller-supplied *api_key*.

    Used to verify a tenant-supplied API key is live without spending on a
    real generation call — each branch hits a metadata/list endpoint. Raises
    whatever the provider SDK raises on an invalid key or network failure;
    returns ``None`` on success.

    Parameters
    ----------
    provider:
        One of ``"openai"``, ``"anthropic"``, ``"google"``, ``"deepseek"``.
    api_key:
        The key to test — used directly, never read from settings.
    """
    provider = provider.lower().strip()

    if provider not in _SUPPORTED_PROVIDERS:
        raise AIWorkflowError(
            f"Unsupported LLM provider '{provider}'. Supported: {sorted(_SUPPORTED_PROVIDERS)}"
        )

    if provider == "anthropic":
        from anthropic import AsyncAnthropic  # noqa: PLC0415

        await AsyncAnthropic(api_key=api_key).models.list(limit=1)
        return

    if provider == "openai":
        from openai import AsyncOpenAI  # noqa: PLC0415

        await AsyncOpenAI(api_key=api_key).models.list()
        return

    if provider == "google":
        from google import genai  # noqa: PLC0415
        from google.genai.types import ListModelsConfig  # noqa: PLC0415

        await genai.Client(api_key=api_key).aio.models.list(config=ListModelsConfig(page_size=1))
        return

    if provider == "deepseek":
        from openai import AsyncOpenAI  # noqa: PLC0415

        await AsyncOpenAI(api_key=api_key, base_url=_DEEPSEEK_API_BASE).models.list()


def get_llm(
    provider: str,
    model_name: str,
    temperature: float = 0.0,
    max_tokens: int = 384000,
    api_key: str = None,
) -> BaseChatModel:
    """Return a configured chat model for *provider*.

    Parameters
    ----------
    provider:
        One of ``"openai"``, ``"anthropic"``, ``"google"``.
    model_name:
        Model identifier as accepted by the provider SDK
        (e.g. ``"gpt-4o"``, ``"claude-sonnet-4-20250514"``, ``"gemini-2.5-pro"``).
    temperature:
        Sampling temperature; typically ``0.0`` for deterministic structured output.

    Raises
    ------
    AIWorkflowError
        If *provider* is not recognised or the required API key is missing.
    """
    provider = provider.lower().strip()

    if provider not in _SUPPORTED_PROVIDERS:
        raise AIWorkflowError(
            f"Unsupported LLM provider '{provider}'. Supported: {sorted(_SUPPORTED_PROVIDERS)}"
        )

    if provider == "openai":
        if not api_key:
            raise AIWorkflowError(
                "OPENAI_API_KEY is not set. Please provide it to use the OpenAI provider."
            )
        from langchain_openai import ChatOpenAI  # noqa: PLC0415

        logger.debug("LLM factory: openai / %s (temp=%.1f)", model_name, temperature)
        logger.info("LLM factory: openai / %s (temp=%.1f)", model_name, temperature)
        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            api_key=api_key,
            max_tokens=128000,
        )

    if provider == "anthropic":
        if not api_key:
            raise AIWorkflowError(
                "ANTHROPIC_API_KEY is not set. Please provide it to use the Anthropic provider."
            )
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415

        logger.debug("LLM factory: anthropic / %s (temp=%.1f)", model_name, temperature)
        logger.info("LLM factory: anthropic / %s (temp=%.1f)", model_name, temperature)
        anthropic_kwargs = {
            "model": model_name,
            "api_key": api_key,
            "max_tokens": 128000,
        }
        if "claude-sonnet-5" not in model_name.lower():
            anthropic_kwargs["temperature"] = temperature
        return ChatAnthropic(**anthropic_kwargs)

    if provider == "google":
        if not api_key:
            raise AIWorkflowError(
                "GOOGLE_API_KEY is not set. Please provide it to use the Google provider."
            )
        from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: PLC0415

        logger.debug("LLM factory: google / %s (temp=%.1f)", model_name, temperature)
        logger.info("LLM factory: google / %s (temp=%.1f)", model_name, temperature)
        return ChatGoogleGenerativeAI(
            model=model_name,
            temperature=temperature,
            google_api_key=api_key,
        )

    if provider == "deepseek":
        if not api_key:
            raise AIWorkflowError(
                "DEEPSEEK_API_KEY is not set. Please provide it to use the DeepSeek provider."
            )
        from langchain_deepseek import ChatDeepSeek  # noqa: PLC0415

        logger.debug("LLM factory: deepseek / %s (temp=%.1f)", model_name, temperature)
        logger.info("LLM factory: deepseek / %s (temp=%.1f)", model_name, temperature)
        return ChatDeepSeek(
            model=model_name,
            temperature=temperature,
            api_key=api_key,
            max_tokens=max_tokens,
            # ChatDeepSeek subclasses langchain_openai's BaseChatOpenAI, which since
            # langchain-openai 1.2 kills an async stream after 120s of genuine content
            # silence (StreamChunkTimeoutError) — see cancellable_llm.py, which streams
            # every call so cancellation works. deepseek-v4-pro's reasoning/"thinking"
            # mode can legitimately stay silent well past 120s on large RFP inputs
            # before its first token, which was tripping this as a false-positive
            # dead-connection timeout. Widen it rather than disable it outright, so an
            # actually-dead connection still gets caught.
            stream_chunk_timeout=600,
            **{"reasoning_effort": "max"},
            # model_kwargs={
            #     "reasoning_effort": "max",
            #     "thinking": {"type": "enabled"}
            # }
        )


# ── Image extraction clients ───────────────────────────────────────────────────

from abc import ABC, abstractmethod


class _ImageClient(ABC):
    """Thin uniform wrapper around raw provider SDKs for image extraction.
    Caller loads image bytes; this client handles only the API call.
    """

    @abstractmethod
    def process_image(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        """Send image bytes + prompt to the model and return raw text output."""
        raise NotImplementedError


class _GeminiImageClient(_ImageClient):
    def __init__(self, model: str, api_key: str):
        from google import genai

        self.model = model
        self.client = genai.Client(api_key=api_key)

    def process_image(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        from google.genai import types

        response = self.client.models.generate_content(
            model=self.model,
            contents=[
                prompt,
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            ],
        )
        return response.text or ""


class _OpenAIImageClient(_ImageClient):
    def __init__(self, model: str, api_key: str):
        from openai import OpenAI

        self.model = model
        self.client = OpenAI(api_key=api_key)

    def process_image(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        import base64

        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        resp = self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime_type};base64,{image_b64}",
                        },
                    ],
                }
            ],
        )
        return resp.output_text or ""


class _AnthropicImageClient(_ImageClient):
    def __init__(self, model: str, max_tokens: int, api_key: str):
        from anthropic import Anthropic

        self.model = model
        self.max_tokens = max_tokens
        self.client = Anthropic(api_key=api_key)

    def process_image(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        import base64

        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": image_b64,
                            },
                        },
                    ],
                }
            ],
        ) as stream:
            return stream.get_final_text()


def get_image_llm(
    provider: str,
    model_name: str,
    max_tokens: int = 4096,
    api_key: str = None,
) -> _ImageClient:
    """Return a raw-SDK image client for *provider*.

    Always uses temperature=0.0 (deterministic extraction).
    Supports: ``"google"``, ``"openai"``, ``"anthropic"``.

    Parameters
    ----------
    provider:
        One of ``"google"``, ``"openai"``, ``"anthropic"``.
    model_name:
        Provider-specific model identifier.
    max_tokens:
        Max tokens for the response (used by Anthropic; Gemini/OpenAI ignore it).
    """
    provider = provider.lower().strip()
    logger.debug("Image LLM factory: %s / %s", provider, model_name)

    # Uncomment the following part to enable API key validation for image extraction clients when turing off env keys
    if not api_key:
        raise AIWorkflowError(
            "Api Key is not set. Please provide it to use the Image Extraction provider."
        )

    if provider == "google":
        return _GeminiImageClient(model=model_name, api_key=api_key)
    if provider == "openai":
        return _OpenAIImageClient(model=model_name, api_key=api_key)
    if provider == "anthropic":
        return _AnthropicImageClient(model=model_name, max_tokens=max_tokens, api_key=api_key)

    raise ValueError(
        f"Unsupported image extraction provider: '{provider}'. "
        f"Supported: 'google', 'openai', 'anthropic'."
    )
