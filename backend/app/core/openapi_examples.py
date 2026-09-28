"""Shared OpenAPI example payloads.

Keep request/response examples centralized so schema modules remain focused on
validation and typing.
"""

# ── Auth ──────────────────────────────────────────────────────────────────

AUTH_LOGIN_REQUEST_EXAMPLE: dict[str, str] = {
    "email": "abdul.halim@bjitgroup.com",
    "password": "Secure1Pass",
}

AUTH_FORGOT_PASSWORD_REQUEST_EXAMPLE: dict[str, str] = {
    "email": "abdul.halim@bjitgroup.com",
}

AUTH_RESET_PASSWORD_REQUEST_EXAMPLE: dict[str, str] = {
    "email": "abdul.halim@bjitgroup.com",
    "code": "123456",
    "new_password": "Secure1Pass",
}

# ── Project ───────────────────────────────────────────────────────────────

PROJECT_CREATE_EXAMPLE: dict[str, str | None] = {
    "name": "RFP Analysis Q4-2025",
    "description": "Extract and track requirements from the Q4 2025 procurement document.",
}

PROJECT_UPDATE_EXAMPLE: dict[str, str | None] = {
    "name": "RFP Analysis Q4-2025 (Final)",
    "description": None,
}
