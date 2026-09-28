"""Standardized error response envelope.

All error responses — regardless of origin (validation, auth, business logic,
unexpected crash) — are serialized using this model so API consumers can rely
on a consistent structure.

Example JSON
────────────
{
  "success": false,
  "error_code": "NOT_FOUND",
  "message": "Test abc123 not found.",
  "detail": null,
  "correlation_id": "5f3a1c2b-...",
  "path": "/api/v1/tests/abc123",
  "timestamp": "2026-04-20T10:30:00.123456+00:00"
}
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ValidationErrorDetail(BaseModel):
    """Shape of a single entry in the ``detail`` list for validation errors.

    Matches the structure produced by Pydantic v2 / FastAPI's
    RequestValidationError so Swagger renders the full object schema.
    """

    model_config = ConfigDict(extra="ignore")

    loc: list[str | int]
    msg: str
    type: str
    input: Any | None = None
    ctx: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """Envelope for every non-2xx response in the RIP platform."""

    success: bool = False
    error_code: str
    message: str
    detail: list[ValidationErrorDetail] | None = None
    correlation_id: str
    path: str
    timestamp: str

    @classmethod
    def build(
        cls,
        *,
        error_code: str,
        message: str,
        correlation_id: str,
        path: str,
        detail: Any | None = None,
    ) -> ErrorResponse:
        return cls(
            error_code=error_code,
            message=message,
            detail=detail,
            correlation_id=correlation_id,
            path=path,
            timestamp=datetime.now(UTC).isoformat(),
        )
