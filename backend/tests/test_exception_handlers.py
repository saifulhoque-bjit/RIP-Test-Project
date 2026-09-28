"""Unit tests for global exception handlers in app.core.exception_handlers.

Strategy:
- Build a minimal FastAPI app with ``register_exception_handlers`` applied.
- Use ``httpx.AsyncClient`` with ``ASGITransport`` to send test requests.
- Each handler branch is exercised via a dedicated route that raises the
  target exception type.
- The ``ErrorResponse`` envelope structure is verified for every case.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi import Body, FastAPI
import httpx
from pydantic import BaseModel, field_validator
import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import (
    CognitoError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError as AppValidationError,
)

# ── Module-level models (locally-defined models break ForwardRef resolution) ──


class _PasswordBody(BaseModel):  # noqa: N801
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        return v


# ── Fixture: minimal app ───────────────────────────────────────────────────


def _build_test_app() -> FastAPI:
    """Return a FastAPI app with all exception handlers registered and the
    CorrelationIdMiddleware applied (needed so correlation_id ContextVar is
    populated before handlers fire)."""
    from app.utils.correlation import CorrelationIdMiddleware

    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)

    class _Body(BaseModel):
        name: str  # required — omitting it triggers RequestValidationError

    # _PasswordBody is defined at module level to avoid Pydantic ForwardRef issues

    @app.get("/not-found")
    async def _not_found():
        raise NotFoundError("Resource not found.")

    @app.get("/conflict")
    async def _conflict():
        raise ConflictError("Duplicate entry.")

    @app.get("/unauthorized")
    async def _unauthorized():
        raise UnauthorizedError("Not authenticated.")

    @app.get("/forbidden")
    async def _forbidden():
        raise ForbiddenError("Access denied.")

    @app.get("/validation-error")
    async def _validation():
        raise AppValidationError("Bad input.")

    @app.get("/cognito-error")
    async def _cognito():
        raise CognitoError("Cognito service error.")

    @app.post("/validation-body")
    async def _body(_body: _Body):
        return {"ok": True}

    @app.post("/validation-field-validator")
    async def _field_validator_body(body: _PasswordBody = Body(...)):
        return {"ok": True}

    @app.get("/http-exception")
    async def _http():
        raise StarletteHTTPException(status_code=418, detail="I'm a teapot")

    @app.get("/unhandled")
    async def _unhandled():
        raise RuntimeError("Unexpected crash")

    return app


@pytest.fixture
async def client():
    app = _build_test_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def lenient_client():
    """Client that does not raise on 5xx; used to test the 500 catch-all handler."""
    app = _build_test_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


# ── RIPBaseException subclasses ────────────────────────────────────────────


class TestRIPExceptionHandlers:
    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, client) -> None:
        r = await client.get("/not-found")
        assert r.status_code == 404
        body = r.json()
        assert body["success"] is False
        assert body["error_code"] == "NOT_FOUND"
        assert "not found" in body["message"].lower()
        assert "correlation_id" in body
        assert "timestamp" in body
        assert "path" in body

    @pytest.mark.asyncio
    async def test_conflict_returns_409(self, client) -> None:
        r = await client.get("/conflict")
        assert r.status_code == 409
        assert r.json()["error_code"] == "CONFLICT"

    @pytest.mark.asyncio
    async def test_unauthorized_returns_401_with_www_authenticate(self, client) -> None:
        r = await client.get("/unauthorized")
        assert r.status_code == 401
        assert r.json()["error_code"] == "UNAUTHORIZED"
        assert "WWW-Authenticate" in r.headers
        assert r.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.asyncio
    async def test_forbidden_returns_403(self, client) -> None:
        r = await client.get("/forbidden")
        assert r.status_code == 403
        assert r.json()["error_code"] == "FORBIDDEN"

    @pytest.mark.asyncio
    async def test_app_validation_error_returns_400(self, client) -> None:
        r = await client.get("/validation-error")
        assert r.status_code == 400
        assert r.json()["error_code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_cognito_error_returns_502(self, client) -> None:
        r = await client.get("/cognito-error")
        assert r.status_code == 502
        assert r.json()["error_code"] == "AUTH_SERVICE_ERROR"


# ── RequestValidationError (Pydantic) ─────────────────────────────────────


class TestRequestValidationHandler:
    @pytest.mark.asyncio
    async def test_missing_required_field_returns_422(self, client) -> None:
        r = await client.post("/validation-body", json={})
        assert r.status_code == 422
        body = r.json()
        assert body["success"] is False
        assert body["error_code"] == "VALIDATION_ERROR"
        assert "detail" in body  # raw pydantic errors surfaced
        assert body["detail"] is not None

    @pytest.mark.asyncio
    async def test_field_validator_value_error_returns_422_not_500(self, client) -> None:
        """Pydantic v2 @field_validator embeds the raw ValueError object in
        ctx['error'].  Without sanitization this causes a TypeError in
        json.dumps and turns a 422 into a 500 INTERNAL_ERROR."""
        r = await client.post("/validation-field-validator", json={"password": "nouppercase"})
        assert r.status_code == 422
        body = r.json()
        assert body["error_code"] == "VALIDATION_ERROR"
        # ctx['error'] must be a string, not an exception object
        detail = body["detail"]
        assert detail is not None
        for err in detail:
            ctx = err.get("ctx") or {}
            for val in ctx.values():
                assert not isinstance(val, Exception)

    @pytest.mark.asyncio
    async def test_field_validator_error_message_preserved(self, client) -> None:
        r = await client.post("/validation-field-validator", json={"password": "nouppercase"})
        assert r.status_code == 422
        body = r.json()
        assert "uppercase" in body["message"].lower() or "uppercase" in str(body["detail"]).lower()


# ── HTTPException pass-through ─────────────────────────────────────────────


class TestHttpExceptionHandler:
    @pytest.mark.asyncio
    async def test_http_exception_preserves_status_code(self, client) -> None:
        r = await client.get("/http-exception")
        assert r.status_code == 418
        body = r.json()
        assert body["success"] is False
        assert "teapot" in body["message"].lower()

    @pytest.mark.asyncio
    async def test_http_exception_includes_error_code(self, client) -> None:
        r = await client.get("/http-exception")
        # 418 is not in the standard map; expect HTTP_418
        assert "418" in r.json()["error_code"] or r.json()["error_code"].startswith("HTTP_")


# ── Catch-all / 500 ───────────────────────────────────────────────────────


class TestUnhandledExceptionHandler:
    @pytest.mark.asyncio
    async def test_unhandled_exception_returns_500(self, lenient_client) -> None:
        with patch("app.core.exception_handlers.get_settings") as mock_settings:
            mock_settings.return_value.DEBUG = False
            mock_settings.return_value.APP_ENV = "production"
            r = await lenient_client.get("/unhandled")
        assert r.status_code == 500
        body = r.json()
        assert body["success"] is False
        assert body["error_code"] == "INTERNAL_ERROR"
        # Must not leak internal exception detail
        assert "Unexpected crash" not in body["message"]


# ── ErrorResponse envelope structure ──────────────────────────────────────


class TestErrorResponseStructure:
    @pytest.mark.asyncio
    async def test_all_required_fields_present(self, client) -> None:
        r = await client.get("/not-found")
        body = r.json()
        for field in ("success", "error_code", "message", "correlation_id", "path", "timestamp"):
            assert field in body, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_path_reflects_request_url(self, client) -> None:
        r = await client.get("/not-found")
        assert r.json()["path"] == "/not-found"

    @pytest.mark.asyncio
    async def test_correlation_id_echoed_when_provided(self, client) -> None:
        cid = "my-correlation-id-123"
        r = await client.get("/not-found", headers={"X-Correlation-ID": cid})
        assert r.json()["correlation_id"] == cid
