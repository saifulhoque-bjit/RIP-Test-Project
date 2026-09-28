"""Service for per-project TAP integration config."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.clients.tap_client import TapClient
from app.core.constants import TAP_APP_CLIENT_NAME
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.messages import MSG_TAP_INTEGRATION_CONFLICT, MSG_TAP_INTEGRATION_NOT_FOUND
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.tap_integration_model import TapIntegration
from app.schemas.tap_integration_schema import (
    TapIntegrationCreate,
    TapIntegrationResponse,
    TapIntegrationUpdate,
)
from app.utils.encryption import decrypt_tap_api_key, encrypt_tap_api_key
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TapIntegrationService:
    @staticmethod
    def _mask_key(key: str | None) -> str:
        if not key or len(key) <= 8:
            return "****"
        return f"{key[:4]}...{key[-4:]}"

    @staticmethod
    def _key_hint(ciphertext: str | None) -> str:
        """Masked hint for the stored key, e.g. ``abcd...wxyz``.

        Decryption here is purely cosmetic, so it must never be able to fail
        the request: a row written before encryption was introduced (NULL
        column) or with a key that no longer matches ``TAP_ENCRYPTION_KEY``
        would otherwise raise out of a plain GET and take the whole settings
        page down with it. Degrade to a generic mask instead — the caller
        only ever renders this.
        """
        if not ciphertext:
            return "****"
        try:
            return TapIntegrationService._mask_key(decrypt_tap_api_key(ciphertext))
        except Exception:
            logger.warning(
                "TAP api key could not be decrypted for display — returning a generic mask. "
                "The stored key may predate encryption or have been written with a different "
                "TAP_ENCRYPTION_KEY; reconnect the integration to re-store it.",
                exc_info=True,
            )
            return "****"

    @staticmethod
    def _to_response(row: TapIntegration) -> TapIntegrationResponse:
        return TapIntegrationResponse(
            id=row.id,
            project_id=row.project_id,
            base_url=row.base_url,
            app_client_name=TAP_APP_CLIENT_NAME,
            api_key_hint=TapIntegrationService._key_hint(row.api_key_encrypted),
            client_id=row.client_id,
            is_active=row.is_active,
            last_verified_at=row.last_verified_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    async def _verify_against_tap(*, base_url: str, api_key: str, client_id: str) -> None:
        """Call the TAP service to confirm the given credentials are valid.

        The app client name is not a parameter: RIP always verifies as
        ``TAP_APP_CLIENT_NAME``, so there is nothing for a caller to vary.
        """
        client = TapClient(
            base_url=base_url,
            auth_config={"api_key": api_key, "app_client_id": client_id},
        )
        try:
            await client.verify_credentials(TAP_APP_CLIENT_NAME)
        except Exception as exc:
            raise ValidationError(
                f"Could not authenticate with TAP using these credentials: {exc}"
            ) from exc

    # ── CRUD ───────────────────────────────────────────────────────────────

    async def create_integration(
        self,
        project_id: UUID,
        payload: TapIntegrationCreate,
        uow: UnitOfWork,
        user_id: UUID,
    ) -> TapIntegrationResponse:
        project = uow.projects.get_by_uuid(project_id)
        if not project:
            raise NotFoundError(f"Project {project_id} not found.")

        existing = uow.tap_integrations.get_by_project_id(project_id)
        if existing and existing.is_active:
            raise ConflictError(MSG_TAP_INTEGRATION_CONFLICT.format(project_id=project_id))

        await self._verify_against_tap(
            base_url=payload.base_url.rstrip("/"),
            api_key=payload.api_key,
            client_id=payload.client_id,
        )

        now = datetime.now(UTC)
        if existing and not existing.is_active:
            existing.is_active = True
            existing.base_url = payload.base_url.rstrip("/")
            existing.api_key_encrypted = encrypt_tap_api_key(payload.api_key)
            existing.client_id = payload.client_id
            existing.last_verified_at = now
            uow.flush()
            uow.refresh(existing)
            return self._to_response(existing)

        try:
            row = TapIntegration(
                project_id=project_id,
                created_by_id=user_id,
                base_url=payload.base_url.rstrip("/"),
                api_key_encrypted=encrypt_tap_api_key(payload.api_key),
                client_id=payload.client_id,
                last_verified_at=now,
            )
            uow.tap_integrations.add(row)
            uow.flush()
            uow.refresh(row)
        except IntegrityError as exc:
            raise ConflictError(MSG_TAP_INTEGRATION_CONFLICT.format(project_id=project_id)) from exc

        return self._to_response(row)

    def get_integration(self, project_id: UUID, uow: UnitOfWork) -> TapIntegrationResponse:
        row = uow.tap_integrations.get_active_by_project_id(project_id)
        if not row:
            raise NotFoundError(MSG_TAP_INTEGRATION_NOT_FOUND.format(project_id=project_id))
        return self._to_response(row)

    async def update_integration(
        self,
        project_id: UUID,
        payload: TapIntegrationUpdate,
        uow: UnitOfWork,
    ) -> TapIntegrationResponse:
        row = uow.tap_integrations.get_active_by_project_id(project_id)
        if not row:
            raise NotFoundError(MSG_TAP_INTEGRATION_NOT_FOUND.format(project_id=project_id))

        new_base_url = payload.base_url.rstrip("/") if payload.base_url else row.base_url
        new_api_key = payload.api_key if payload.api_key else None
        new_client_id = payload.client_id if payload.client_id else row.client_id

        # Re-verify only when any auth-relevant field changes.
        if new_api_key or payload.base_url or payload.client_id:
            plain_key = new_api_key or decrypt_tap_api_key(row.api_key_encrypted)
            await self._verify_against_tap(
                base_url=new_base_url,
                api_key=plain_key,
                client_id=new_client_id,
            )
            row.last_verified_at = datetime.now(UTC)

        if payload.base_url:
            row.base_url = new_base_url
        if new_api_key:
            row.api_key_encrypted = encrypt_tap_api_key(new_api_key)
        if payload.client_id:
            row.client_id = new_client_id

        uow.flush()
        uow.refresh(row)
        return self._to_response(row)

    def delete_integration(self, project_id: UUID, uow: UnitOfWork) -> None:
        row = uow.tap_integrations.get_active_by_project_id(project_id)
        if not row:
            raise NotFoundError(MSG_TAP_INTEGRATION_NOT_FOUND.format(project_id=project_id))
        row.is_active = False
        uow.flush()

