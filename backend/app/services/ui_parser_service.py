"""UI parser service.

Responsibilities
────────────────
- Accept a local file path.
- Validate the path and ensure the file exists and is non-empty.
- Read the file in binary mode.
- Convert the bytes to a base64-encoded UTF-8 string.
- Delegate parsing to ``omniparser_client.parse_image_base64``.
- Return a validated ``OmniParserResponse``.

This service contains NO HTTP calls — all external communication is
delegated to ``OmniParserClient``.  Stateless: instantiate at call-site.
"""

from __future__ import annotations

import base64
from pathlib import Path

from app.clients.omniparser_client import OmniParserResponse, omniparser_client
from app.core.exceptions import OmniParserClientError, ServiceError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class UIParserService:
    """Orchestrate local-file → base64 → OmniParser → structured response.

    Usage
    ─────
        service = UIParserService()
        result = await service.run(file_path="/tmp/screenshot.png", ctx=ctx)
    """

    async def run(self, file_path: str, ctx: dict) -> OmniParserResponse:
        """Parse UI elements from a local image file.

        Parameters
        ──────────
        file_path : Absolute or relative path to the image file on disk.
        ctx       : Request context dict — must contain ``request_id``,
                    ``user_id``, and ``project_id``.

        Returns
        ───────
        ``OmniParserResponse`` containing all detected UI elements.

        Raises
        ──────
        ``ServiceError`` on empty/invalid path, missing file, read failure,
        base64 conversion failure, or OmniParser client error.
        """
        logger.info(
            "UIParserService.run started",
            extra={
                "request_id": ctx.get("request_id"),
                "user_id": ctx.get("user_id"),
                "project_id": ctx.get("project_id"),
                "status": "running",
            },
        )

        # ── 1. Validate path ───────────────────────────────────────────────
        if not file_path or not file_path.strip():
            logger.error(
                "UIParserService.run received empty file path",
                extra={
                    "request_id": ctx.get("request_id"),
                    "user_id": ctx.get("user_id"),
                    "project_id": ctx.get("project_id"),
                    "status": "error",
                },
            )
            raise ServiceError("file_path must not be empty.")

        path = Path(file_path)

        if not path.exists():
            logger.error(
                "UIParserService.run file not found: %s",
                path,
                extra={
                    "request_id": ctx.get("request_id"),
                    "user_id": ctx.get("user_id"),
                    "project_id": ctx.get("project_id"),
                    "status": "error",
                },
            )
            raise ServiceError(f"File not found: {path}")

        if not path.is_file():
            logger.error(
                "UIParserService.run path is not a regular file: %s",
                path,
                extra={
                    "request_id": ctx.get("request_id"),
                    "user_id": ctx.get("user_id"),
                    "project_id": ctx.get("project_id"),
                    "status": "error",
                },
            )
            raise ServiceError(f"Path is not a regular file: {path}")

        # ── 2. Read file bytes ─────────────────────────────────────────────
        try:
            file_bytes: bytes = path.read_bytes()
        except OSError as exc:
            logger.error(
                "UIParserService.run failed to read file: %s — %s",
                path,
                exc,
                extra={
                    "request_id": ctx.get("request_id"),
                    "user_id": ctx.get("user_id"),
                    "project_id": ctx.get("project_id"),
                    "status": "error",
                },
                exc_info=True,
            )
            raise ServiceError(f"Could not read file '{path}': {exc}") from exc

        if not file_bytes:
            logger.error(
                "UIParserService.run file is empty: %s",
                path,
                extra={
                    "request_id": ctx.get("request_id"),
                    "user_id": ctx.get("user_id"),
                    "project_id": ctx.get("project_id"),
                    "status": "error",
                },
            )
            raise ServiceError(f"File is empty: {path}")

        logger.info(
            "UIParserService.run file read: %s bytes=%d",
            path.name,
            len(file_bytes),
            extra={
                "request_id": ctx.get("request_id"),
                "user_id": ctx.get("user_id"),
                "project_id": ctx.get("project_id"),
                "status": "running",
            },
        )

        # ── 3. Convert to base64 ───────────────────────────────────────────
        try:
            image_base64: str = base64.b64encode(file_bytes).decode("utf-8")
        except Exception as exc:
            logger.error(
                "UIParserService.run base64 conversion failed for: %s — %s",
                path,
                exc,
                extra={
                    "request_id": ctx.get("request_id"),
                    "user_id": ctx.get("user_id"),
                    "project_id": ctx.get("project_id"),
                    "status": "error",
                },
                exc_info=True,
            )
            raise ServiceError(f"Base64 encoding failed for '{path}': {exc}") from exc

        # ── 4. Delegate to OmniParser client ───────────────────────────────
        try:
            result: OmniParserResponse = await omniparser_client.parse_image_base64(
                base64_image=image_base64,
                ctx=ctx,
            )
        except OmniParserClientError as exc:
            logger.error(
                "UIParserService.run OmniParser client error for: %s — %s",
                path,
                exc,
                extra={
                    "request_id": ctx.get("request_id"),
                    "user_id": ctx.get("user_id"),
                    "project_id": ctx.get("project_id"),
                    "status": "error",
                },
                exc_info=True,
            )
            raise ServiceError(f"OmniParser failed to parse '{path.name}': {exc}") from exc

        logger.info(
            "UIParserService.run success: file=%s elements=%d",
            path.name,
            len(result.elements),
            extra={
                "request_id": ctx.get("request_id"),
                "user_id": ctx.get("user_id"),
                "project_id": ctx.get("project_id"),
                "status": "success",
            },
        )

        return result
