"""OmniParser REST API client.

Responsibilities
────────────────
- Call the OmniParser microservice via its REST API (AGPL isolation — no
  direct library import is ever permitted).
- Validate every inbound and outbound payload with Pydantic v2.
- Handle timeouts, connection failures, non-200 responses, and schema errors
  by raising ``OmniParserClientError``.
- Retry transient failures up to ``_MAX_RETRIES`` times with exponential
  back-off before giving up.

Usage
─────
    from app.clients.omniparser_client import omniparser_client

    result: OmniParserResponse = await omniparser_client.parse_image(
        image_url="https://...",
        ctx=ctx,
    )
"""

from __future__ import annotations

import asyncio

import httpx
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.exceptions import OmniParserClientError
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
_PARSE_BASE64_ENDPOINT = "/parse/"
_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 0.5  # wait 0.5 s, 1.0 s, 2.0 s between attempts


# ── Pydantic models ────────────────────────────────────────────────────────────
class OmniParserRequest(BaseModel):
    """Payload sent to POST /parse."""

    image_url: str = Field(
        ..., min_length=1, description="Publicly reachable URL of the image to parse."
    )


class OmniParserBase64Request(BaseModel):
    """Payload sent to POST /parse/base64."""

    base64_image: str = Field(..., min_length=1, description="Base64-encoded image bytes.")


class OmniParserElement(BaseModel):
    """A single UI element detected by OmniParser."""

    type: str = Field(..., description="Element type e.g. 'text', 'icon'.")
    bbox: list[float] = Field(..., description="Bounding box as [x1, y1, x2, y2].")
    interactivity: bool = Field(default=False)
    content: str = Field(default="")
    source: str = Field(default="")


class OmniParserResponse(BaseModel):
    """Validated response from POST /parse/."""

    elements: list[OmniParserElement] = Field(default_factory=list)

    @classmethod
    def from_api_response(cls, body: dict) -> OmniParserResponse:
        """Map parsed_content_list → elements."""
        raw_list = body.get("parsed_content_list", [])
        return cls(elements=[OmniParserElement.model_validate(item) for item in raw_list])


# ── Client ─────────────────────────────────────────────────────────────────────


class OmniParserClient:
    """Async HTTP client for the OmniParser microservice.

    All OmniParser interaction must go through this class — never call
    ``httpx`` (or any HTTP library) directly from agents or services.
    """

    def __init__(self) -> None:
        self._base_url: str = settings.OMNIPARSER_BASE_URL.rstrip("/")
        self._timeout: int = settings.OMNIPARSER_TIMEOUT_SECONDS

    # ── Public API ─────────────────────────────────────────────────────────
    async def parse_image_base64(self, base64_image: str, ctx: dict) -> OmniParserResponse:
        """Send a base64-encoded image to OmniParser and return validated elements.

        Parameters
        ──────────
        image_base64 : Base64-encoded image bytes (UTF-8 string, no data-URI prefix).
        ctx          : Request context dict — must contain ``request_id``,
                       ``user_id``, and ``project_id``.

        Raises
        ──────
        ``OmniParserClientError`` on timeout, connection failure, non-200
        response, or response that fails schema validation.
        """
        logger.info(
            "OmniParserClient.parse_image_base64 started",
            extra={
                "request_id": ctx.get("request_id"),
                "user_id": ctx.get("user_id"),
                "project_id": ctx.get("project_id"),
                "status": "running",
            },
        )

        request_payload = OmniParserBase64Request(base64_image=base64_image)
        url = f"{self._base_url}{_PARSE_BASE64_ENDPOINT}"
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await self._post_raw(url, request_payload.model_dump())
                parsed = self._validate_response(response, ctx)

                logger.info(
                    "OmniParserClient.parse_image_base64 success",
                    extra={
                        "request_id": ctx.get("request_id"),
                        "user_id": ctx.get("user_id"),
                        "project_id": ctx.get("project_id"),
                        "status": "success",
                        "elements_count": len(parsed.elements),
                        "attempt": attempt,
                    },
                )
                return parsed

            except OmniParserClientError:
                raise

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                logger.warning(
                    "OmniParserClient.parse_image_base64 transient error (attempt %d/%d): %s",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                    extra={
                        "request_id": ctx.get("request_id"),
                        "user_id": ctx.get("user_id"),
                        "project_id": ctx.get("project_id"),
                        "status": "error",
                    },
                )
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
                continue

        logger.error(
            "OmniParserClient.parse_image_base64 failed after %d attempts",
            _MAX_RETRIES,
            extra={
                "request_id": ctx.get("request_id"),
                "user_id": ctx.get("user_id"),
                "project_id": ctx.get("project_id"),
                "status": "error",
            },
            exc_info=last_exc,
        )
        raise OmniParserClientError(
            f"OmniParser base64 request failed after {_MAX_RETRIES} attempts: {last_exc}"
        )

    # ── Private helpers ────────────────────────────────────────────────────
    async def _post(
        self,
        url: str,
        payload: OmniParserRequest,
    ) -> httpx.Response:
        """Execute the POST request and raise ``OmniParserClientError`` on
        non-2xx responses.  Timeout and connection errors are intentionally
        **not** caught here so the retry loop in ``parse_image`` can handle
        them.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                url,
                json=payload.model_dump(),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )

        if not response.is_success:
            raise OmniParserClientError(
                f"OmniParser returned HTTP {response.status_code}: {response.text[:200]}"
            )

        return response

    async def _post_raw(self, url: str, payload: dict) -> httpx.Response:
        """Execute the POST request with raw JSON payload.

        Unlike `_post`, this method does not expect a specific response schema
        and is used for low-level communication with the OmniParser API.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )

        if not response.is_success:
            raise OmniParserClientError(
                f"OmniParser returned HTTP {response.status_code}: {response.text[:200]}"
            )

        return response

    @staticmethod
    def _validate_response(response: httpx.Response, ctx: dict) -> OmniParserResponse:
        try:
            body = response.json()
        except Exception as exc:
            logger.error("OmniParserClient received non-JSON response", ...)
            raise OmniParserClientError(f"OmniParser response is not valid JSON: {exc}") from exc

        try:
            return OmniParserResponse.from_api_response(body)
        except Exception as exc:
            logger.error("OmniParserClient response failed schema validation", ...)
            raise OmniParserClientError(
                f"OmniParser response schema validation failed: {exc}"
            ) from exc


# ── Singleton ──────────────────────────────────────────────────────────────────

omniparser_client = OmniParserClient()
