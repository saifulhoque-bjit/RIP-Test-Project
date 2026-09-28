"""Seed a default Super Admin user (MVP bootstrap).

Runs once per application startup — fully idempotent, safe to call on every
restart/redeploy:
  - Skips entirely if ``SEED_SUPER_ADMIN_ENABLED`` is false or
    ``SUPER_ADMIN_EMAIL``/``SUPER_ADMIN_PASSWORD`` are unset (no credentials
    are ever hardcoded in source — configure via environment/secrets only).
  - The Cognito user is only created if it doesn't already exist (checked via
    ``AdminGetUser``); an existing account's password is never reset here.
  - Role assignment (``super_admin``) is an idempotent no-op on repeat runs.
  - Super Admin is a platform-wide role, not tied to any tenant —
    ``User.tenant_id`` is left ``None`` for this account.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.constants import ROLE_MEMBER, ROLE_SUPER_ADMIN
from app.db.unit_of_work import UnitOfWork
from app.services.auth_service import CognitoAuthService
from app.services.user_service import UserService
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def seed_super_admin() -> None:
    """Idempotently ensure the Super Admin user exists."""
    if not settings.SEED_SUPER_ADMIN_ENABLED:
        logger.info("Super Admin seeding disabled (SEED_SUPER_ADMIN_ENABLED=false)")
        return
    if not settings.SUPER_ADMIN_EMAIL or not settings.SUPER_ADMIN_PASSWORD:
        logger.warning(
            "Skipping Super Admin seed: SUPER_ADMIN_EMAIL/SUPER_ADMIN_PASSWORD are not set"
        )
        return

    cognito = CognitoAuthService()
    sub = await cognito.admin_get_user_sub(email=settings.SUPER_ADMIN_EMAIL)
    if sub is None:
        sub = await cognito.admin_create_user(
            email=settings.SUPER_ADMIN_EMAIL,
            password=settings.SUPER_ADMIN_PASSWORD,
            name=settings.SUPER_ADMIN_NAME,
        )
        logger.info("Created Cognito Super Admin user")
    else:
        logger.info("Cognito Super Admin user already exists — skipping creation")

    with UnitOfWork() as uow:
        user_service = UserService(uow)
        user = user_service.get_or_create_by_cognito_sub(
            sub=sub,
            email=settings.SUPER_ADMIN_EMAIL,
            name=settings.SUPER_ADMIN_NAME,
            is_verified=True,
            assign_default_role=False,
        )
        user_service.assign_role(user.id, ROLE_SUPER_ADMIN)
        # Clean up the `member` role a prior run may have auto-assigned
        # before `assign_default_role=False` existed.
        user_service.revoke_role(user.id, ROLE_MEMBER)

    logger.info("Super Admin ready (user_id=%s)", user.id)
