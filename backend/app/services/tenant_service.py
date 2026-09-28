"""Tenant lifecycle service.

All methods are synchronous and operate through an open
:class:`~app.db.unit_of_work.UnitOfWork` passed at construction time, matching
the pattern used by :class:`~app.services.user_service.UserService`.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.core.constants import (
    ROLE_SUPER_ADMIN,
    TENANT_CODE_MAX_GENERATION_ATTEMPTS,
    TENANT_CODE_MAX_INSERT_RETRIES,
)
from app.core.enums.llm_provider import LLMProvider
from app.core.enums.tenant_status import TenantStatus
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ServiceError
from app.core.messages import (
    MSG_TENANT_CODE_GENERATION_FAILED,
    MSG_TENANT_EMAIL_CONFLICT,
    MSG_TENANT_FORBIDDEN_OTHER_TENANT,
    MSG_TENANT_NAME_CONFLICT,
    MSG_TENANT_NOT_FOUND,
    MSG_USER_TENANT_NOT_ASSIGNED,
)
from app.db.unit_of_work import UnitOfWork
from app.models.postgres.tenant_llm_provider_model import TenantLLMProvider
from app.models.postgres.tenant_model import Tenant
from app.models.postgres.user_model import User
from app.schemas.tenant_schema import TenantStatsResponse
from app.services.email_service import EmailService
from app.utils.code_generator import bump_code_suffix, generate_tenant_code_candidate
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TenantService:
    """Tenant CRUD and lookups, scoped through an open :class:`UnitOfWork`."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # ── Queries ────────────────────────────────────────────────────────────

    def get_for_user(self, tenant_id: uuid.UUID | None) -> Tenant:
        """Return the tenant referenced by *tenant_id*.

        Raises :class:`NotFoundError` when the caller has no tenant assigned
        (``tenant_id is None`` — expected for MVP single-tenant deployments)
        or when the referenced tenant row no longer exists.
        """
        if tenant_id is None:
            raise NotFoundError(MSG_USER_TENANT_NOT_ASSIGNED)
        return self.get_by_id(tenant_id)

    def get_by_id(self, tenant_id: uuid.UUID) -> Tenant:
        """Return a tenant by primary key or raise :class:`NotFoundError`."""
        tenant = self._uow.tenants.get(tenant_id)
        if tenant is None:
            raise NotFoundError(MSG_TENANT_NOT_FOUND.format(tenant_id=tenant_id))
        return tenant

    def list_tenants(self, skip: int = 0, limit: int = 20) -> tuple[list[Tenant], int]:
        """Return a paginated list of all tenants and the total count."""
        return self._uow.tenants.get_paginated(skip=skip, limit=limit)

    def get_stats(self) -> TenantStatsResponse:
        """Return platform-wide tenant/project counts (super_admin only — see route).

        ``pending_invitation_tenants`` is deprecated (kept only so existing
        clients don't break) in favor of ``pending_invitation_client_admin``
        — a direct count of pending Client Admin invitations rather than a
        proxy via tenant status. See ``TenantStatsResponse`` for details.
        """
        return TenantStatsResponse(
            total_tenants=self._uow.tenant_stats.count_total_tenants(),
            active_tenants=self._uow.tenant_stats.count_by_status(TenantStatus.ACTIVE.value),
            pending_invitation_tenants=self._uow.tenant_stats.count_by_status(
                TenantStatus.PENDING_INVITATION.value
            ),
            pending_invitation_client_admin=(
                self._uow.tenant_stats.count_pending_client_admin_invitations()
            ),
            deactivated_tenants=self._uow.tenant_stats.count_by_status(TenantStatus.INACTIVE.value),
            total_projects=self._uow.project_stats.count_total_projects(),
        )

    # ── Requester-scoped queries (route-facing) ─────────────────────────────

    @staticmethod
    def _is_super_admin(requester: User) -> bool:
        return any(role.name == ROLE_SUPER_ADMIN for role in requester.roles)

    def get_scoped(self, tenant_id: uuid.UUID, *, requester: User) -> Tenant:
        """Return a tenant by ID, enforcing tenant-scoped view access.

        ``super_admin`` may fetch any tenant; every other role (Client Admin)
        may only fetch their **own** tenant. Raises :class:`ForbiddenError`
        for a cross-tenant read and :class:`NotFoundError` for an unknown ID.
        """
        if not self._is_super_admin(requester) and requester.tenant_id != tenant_id:
            raise ForbiddenError(MSG_TENANT_FORBIDDEN_OTHER_TENANT)
        return self.get_by_id(tenant_id)

    def list_for_requester(
        self, *, requester: User, skip: int = 0, limit: int = 20
    ) -> tuple[list[Tenant], int]:
        """Return the tenants visible to *requester* and the total count.

        ``super_admin`` sees every tenant (paginated); a Client Admin sees
        only their own tenant — a single-item list, or an empty one when no
        tenant is assigned. ``skip``/``limit`` are honoured for super_admin;
        for the own-tenant case the single row is returned as-is (pagination
        past it yields an empty page).
        """
        if self._is_super_admin(requester):
            return self._uow.tenants.get_paginated(skip=skip, limit=limit)
        if requester.tenant_id is None:
            return [], 0
        tenant = self._uow.tenants.get(requester.tenant_id)
        if tenant is None:
            return [], 0
        return ([] if skip > 0 else [tenant]), 1

    def get_project_counts_by_tenant_ids(self, tenant_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Return a per-tenant project count for *tenant_ids* (0 when absent)."""
        return self._uow.project_stats.count_projects_by_tenant_ids(tenant_ids)

    # ── Mutations ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_provider_rows(providers: list[LLMProvider]) -> list[TenantLLMProvider]:
        """Dedupe while preserving order — the DB unique constraint would
        otherwise reject a caller-supplied duplicate at flush time."""
        unique_values = dict.fromkeys(p.value for p in providers)
        return [TenantLLMProvider(provider=value, is_active=False) for value in unique_values]

    @staticmethod
    def _reconcile_provider_rows(tenant: Tenant, providers: list[LLMProvider]) -> None:
        """Replace *tenant*'s enabled-provider set with *providers* in place.

        Unlike a wholesale ``tenant.llm_providers = [...]`` reassignment, this
        only adds/removes the rows that actually changed:
          - A provider already configured that stays in the new set keeps its
            existing row untouched — preserving its stored ``api_key_encrypted``
            and verification state instead of silently wiping them.
          - Reusing the same (tenant_id, provider) pair also avoids a real
            ``IntegrityError`` on ``uq_tenant_llm_provider``: replacing the
            whole collection makes SQLAlchemy insert the new row for an
            unchanged provider before deleting the orphaned old one in the
            same flush, which the old unique constraint rejects.
        """
        desired = dict.fromkeys(p.value for p in providers)  # dedupe, preserve order
        # Soft-deleted rows (see TenantLLMProviderService.delete_provider) stay
        # in tenant.llm_providers but must not count as "already configured" —
        # otherwise re-adding a previously-deleted provider here would silently
        # no-op instead of creating a fresh row.
        existing_by_provider = {
            row.provider: row for row in tenant.llm_providers if row.deleted_at is None
        }

        for provider_value, row in existing_by_provider.items():
            if provider_value not in desired:
                tenant.llm_providers.remove(row)

        for provider_value in desired:
            if provider_value not in existing_by_provider:
                tenant.llm_providers.append(
                    TenantLLMProvider(provider=provider_value, is_active=False)
                )

    def create(
        self,
        *,
        name: str,
        contact_email: str,
        address: str | None = None,
        status: TenantStatus = TenantStatus.ACTIVE,
        providers: list[LLMProvider] | None = None,
    ) -> Tenant:
        """Create a new tenant and notify its contact email.

        Raises:
            ConflictError: If *name* or *contact_email* is already in use by
                another tenant.
        """
        if self._uow.tenants.get_by_name(name) is not None:
            raise ConflictError(MSG_TENANT_NAME_CONFLICT.format(name=name))
        if (
            contact_email is not None
            and self._uow.tenants.get_by_contact_email(contact_email) is not None
        ):
            raise ConflictError(MSG_TENANT_EMAIL_CONFLICT.format(email=contact_email))

        tenant = Tenant(
            name=name,
            contact_email=contact_email,
            address=address,
            status=status.value,
            code=self._generate_unique_tenant_code(name),
            llm_providers=self._build_provider_rows(providers or []),
        )
        self._flush_or_raise_conflict(tenant, name=name, contact_email=contact_email)
        EmailService.send_tenant_created(tenant)
        logger.info("Created tenant id=%s name=%s code=%s", tenant.id, name, tenant.code)
        return tenant

    def _generate_unique_tenant_code(self, name: str) -> str:
        """Return a short, human-readable code derived from *name* that isn't
        already taken by another tenant (bare candidate first, then
        numbered suffixes — see :mod:`app.utils.code_generator`)."""
        base = generate_tenant_code_candidate(name)
        for attempt in range(1, TENANT_CODE_MAX_GENERATION_ATTEMPTS + 1):
            candidate = base if attempt == 1 else bump_code_suffix(base, attempt)
            if self._uow.tenants.get_by_code(candidate) is None:
                return candidate
        raise ServiceError(MSG_TENANT_CODE_GENERATION_FAILED.format(name=name))

    def _flush_or_raise_conflict(
        self, tenant: Tenant, *, name: str, contact_email: str | None
    ) -> None:
        """Stage, flush, refresh and commit *tenant*, mapping any unique-constraint
        violation to the matching :class:`ConflictError` (or re-raising it
        unchanged if it isn't a known tenant-identity conflict).

        A collision on ``tenants.code`` is not a client-facing conflict (the
        caller never supplied a code) — it means another request generated
        the same candidate between :meth:`_generate_unique_tenant_code`'s
        precheck and this flush. That narrow race is retried a bounded
        number of times with a freshly regenerated code before giving up.
        """
        self._uow.add(tenant)
        code_retries_left = TENANT_CODE_MAX_INSERT_RETRIES
        while True:
            try:
                self._uow.flush()
            except IntegrityError as exc:
                self._uow.rollback()
                if "ix_tenants_code" in str(exc.orig) and code_retries_left > 0:
                    code_retries_left -= 1
                    tenant.code = self._generate_unique_tenant_code(name)
                    self._uow.add(tenant)
                    continue
                conflict = self._conflict_from_integrity_error(
                    exc, name=name, contact_email=contact_email
                )
                if conflict is not None:
                    raise conflict from exc
                raise
            break
        self._uow.refresh(tenant)
        self._uow.commit()

    @staticmethod
    def _conflict_from_integrity_error(
        exc: IntegrityError, *, name: str, contact_email: str | None
    ) -> ConflictError | None:
        """Map a unique-constraint violation on ``tenants`` to the matching message.

        Only a last-resort backstop for a create/rename racing another
        request between the service's pre-check and the DB flush — the
        pre-check above is what normal callers hit.

        Returns ``None`` when *exc* doesn't match a known tenant-identity
        constraint (``ix_tenants_name`` / ``ix_tenants_contact_email``) — the
        caller must re-raise the original error in that case rather than
        mislabel an unrelated failure (e.g. the provider-set unique
        constraint) as a name/email clash.
        """
        detail = str(exc.orig)
        if "ix_tenants_contact_email" in detail:
            return ConflictError(MSG_TENANT_EMAIL_CONFLICT.format(email=contact_email))
        if "ix_tenants_name" in detail:
            return ConflictError(MSG_TENANT_NAME_CONFLICT.format(name=name))
        return None

    def _raise_if_name_conflict(self, tenant_id: uuid.UUID, name: str) -> None:
        clash = self._uow.tenants.get_by_name(name)
        if clash is not None and clash.id != tenant_id:
            raise ConflictError(MSG_TENANT_NAME_CONFLICT.format(name=name))

    def _raise_if_email_conflict(self, tenant_id: uuid.UUID, contact_email: str) -> None:
        clash = self._uow.tenants.get_by_contact_email(contact_email)
        if clash is not None and clash.id != tenant_id:
            raise ConflictError(MSG_TENANT_EMAIL_CONFLICT.format(email=contact_email))

    def update(
        self,
        tenant_id: uuid.UUID,
        *,
        name: str | None = None,
        contact_email: str | None = None,
        address: str | None = None,
        status: TenantStatus | None = None,
        providers: list[LLMProvider] | None = None,
    ) -> Tenant:
        """Update only the supplied fields of a tenant (``None`` = leave unchanged).

        ``providers=None`` leaves the enabled-provider set untouched;
        supplying a list (including an empty one) replaces it in full.
        PATCH semantics — see :meth:`replace` for PUT's full-replacement
        semantics, where every field (including ``address``) is set as-is.

        Raises:
            ConflictError: If the new *name* or *contact_email* is already
                taken by another tenant.
        """
        tenant = self.get_by_id(tenant_id)

        if name is not None and name != tenant.name:
            self._raise_if_name_conflict(tenant_id, name)
            tenant.name = name
        if contact_email is not None and contact_email != tenant.contact_email:
            self._raise_if_email_conflict(tenant_id, contact_email)
            tenant.contact_email = contact_email
        if address is not None:
            tenant.address = address
        if status is not None:
            tenant.status = status.value
        if providers is not None:
            self._reconcile_provider_rows(tenant, providers)

        self._flush_or_raise_conflict(tenant, name=tenant.name, contact_email=tenant.contact_email)
        logger.info("Updated tenant id=%s", tenant_id)
        return tenant

    def replace(
        self,
        tenant_id: uuid.UUID,
        *,
        name: str,
        contact_email: str,
        address: str | None,
        status: TenantStatus,
        providers: list[LLMProvider],
    ) -> Tenant:
        """Fully replace a tenant's fields (PUT semantics).

        Unlike :meth:`update`, every field reflects the complete desired
        state: ``address=None`` clears any existing address (``update``
        can't distinguish "not provided" from "explicitly cleared" for a
        scalar field), and an empty ``providers`` list clears every
        configured provider.

        Raises:
            ConflictError: If the new *name* or *contact_email* is already
                taken by another tenant.
        """
        tenant = self.get_by_id(tenant_id)

        if name != tenant.name:
            self._raise_if_name_conflict(tenant_id, name)
        if contact_email != tenant.contact_email:
            self._raise_if_email_conflict(tenant_id, contact_email)

        tenant.name = name
        tenant.contact_email = contact_email
        tenant.address = address
        tenant.status = status.value
        self._reconcile_provider_rows(tenant, providers)

        self._flush_or_raise_conflict(tenant, name=name, contact_email=contact_email)
        logger.info("Replaced tenant id=%s", tenant_id)
        return tenant

    def deactivate(self, tenant_id: uuid.UUID) -> Tenant:
        """Soft-delete a tenant by setting ``status = inactive``."""
        tenant = self.get_by_id(tenant_id)
        tenant.status = TenantStatus.INACTIVE.value
        self._uow.add(tenant)
        self._uow.flush()
        self._uow.refresh(tenant)
        self._uow.commit()
        logger.info("Deactivated tenant id=%s", tenant_id)
        return tenant
