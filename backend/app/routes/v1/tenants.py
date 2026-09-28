"""Route handlers for /tenants — v1.

Endpoint summary
────────────────
Self-service (any authenticated user):
    GET   /tenants/me            — the tenant assigned to the caller

Read access — Super-Admin or Client Admin (``super_admin`` or ``admin`` role);
super_admin sees every tenant, a Client Admin only their own (tenant-scoped in
:class:`TenantService`):
    GET    /tenants/                        — tenant list (own-tenant-scoped for admin)
    GET    /tenants/{tenant_id}             — get a tenant by ID (own tenant only for admin)

Super-Admin-only (requires ``super_admin`` role):
    GET    /tenants/stats                   — platform-wide tenant/project counts
    POST   /tenants/                        — create a tenant
    PATCH  /tenants/{tenant_id}             — partially update a tenant (only
                                               supplied fields change)
    PUT    /tenants/{tenant_id}             — fully replace a tenant (every
                                               field reflects the complete
                                               desired state; an omitted
                                               address/providers is cleared)
    DELETE /tenants/{tenant_id}             — deactivate a tenant (soft-delete, status=inactive)

Super-Admin, Client Admin, or Member (``super_admin``, ``admin``, or ``member`` role):
    GET    /tenants/{tenant_id}/llm-providers               — list this tenant's
                                                                LLM provider config
                                               super_admin → any tenant; Client
                                               Admin/Member → own tenant only.

Super-Admin or Client Admin (``super_admin`` or ``admin`` role):
    PATCH  /tenants/{tenant_id}/llm-providers/{provider}     — enable/disable
                                                                and/or set its API key
    POST   /tenants/{tenant_id}/llm-providers/{provider}/test — test the stored
                                                                 API key
    GET    /tenants/{tenant_id}/llm-providers/{provider}/balance — fetch the
                                                                 provider's
                                                                 remaining
                                                                 account balance
    DELETE /tenants/{tenant_id}/llm-providers/{provider}     — soft-delete a
                                                                provider's configuration
                                               super_admin → any tenant; Client
                                               Admin → own tenant only.

Tenant invitation management (invite/list/resend/revoke) lives in a separate
module, app/routes/v1/tenant_invitations.py, mounted at the same
``/tenants/{tenant_id}/invitations`` path — see that file's docstring.

Design rules
────────────
- Zero business logic here — all decisions live in TenantService.
- Annotated aliases declared once at module level; reused across handlers.
- ``/me`` and ``/stats`` are declared before ``/{tenant_id}`` so they are
  matched first.
- Exception handling is centralised in app/core/exception_handlers.py.
"""

from __future__ import annotations

from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.constants import ROLE_ADMIN, ROLE_MEMBER, ROLE_SUPER_ADMIN
from app.core.enums.llm_provider import LLMProvider
from app.core.messages import (
    MSG_TENANT_DEACTIVATED_SUCCESS,
    MSG_TENANT_LLM_PROVIDER_DELETED,
    MSG_TENANT_LLM_PROVIDER_UPDATED,
    MSG_TENANT_STATS_FETCHED,
    MSG_TENANT_TOTAL,
    SUMMARY_TENANT_CREATE,
    SUMMARY_TENANT_DEACTIVATE,
    SUMMARY_TENANT_GET,
    SUMMARY_TENANT_GET_ME,
    SUMMARY_TENANT_LIST,
    SUMMARY_TENANT_LLM_PROVIDER_BALANCE,
    SUMMARY_TENANT_LLM_PROVIDER_DELETE,
    SUMMARY_TENANT_LLM_PROVIDER_LIST,
    SUMMARY_TENANT_LLM_PROVIDER_TEST,
    SUMMARY_TENANT_LLM_PROVIDER_UPDATE,
    SUMMARY_TENANT_REPLACE,
    SUMMARY_TENANT_STATS,
    SUMMARY_TENANT_UPDATE,
)
from app.core.rate_limiter import (
    limiter,
    tenant_llm_provider_balance_limit,
    tenant_llm_provider_config_limit,
    tenant_llm_provider_test_limit,
)
from app.db.unit_of_work import UnitOfWork
from app.deps import get_current_db_user, get_uow, require_roles
from app.models.postgres.user_model import User
from app.schemas.tenant_schema import (
    TenantCreateRequest,
    TenantListItem,
    TenantListResponse,
    TenantLLMProviderBalanceResponse,
    TenantLLMProviderListResponse,
    TenantLLMProviderOut,
    TenantLLMProviderTestResponse,
    TenantLLMProviderUpdateRequest,
    TenantOut,
    TenantStatsResponse,
    TenantUpdateRequest,
)
from app.services.tenant_llm_provider_service import TenantLLMProviderService
from app.services.tenant_service import TenantService
from app.utils.response import ApiResponse

router = APIRouter(prefix="/tenants", tags=["Tenants"])

# ── Annotated dependency aliases ────────────────────────────────────────────

CurrentUser = Annotated[User, Depends(get_current_db_user)]
CurrentUow = Annotated[UnitOfWork, Depends(get_uow)]
CurrentAdmin = Annotated[User, Depends(require_roles(ROLE_SUPER_ADMIN))]
CurrentAdminOrSuperAdmin = Annotated[User, Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_ADMIN))]
CurrentMemberOrAdminOrSuperAdmin = Annotated[
    User, Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_MEMBER))
]

SkipQuery = Annotated[int, Query(ge=0)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]


# ── Self-service endpoints ───────────────────────────────────────────────────


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TENANT_GET_ME,
)
def get_my_tenant(
    current_user: CurrentUser,
    uow: CurrentUow,
) -> ApiResponse[TenantOut]:
    """GET /tenants/me — return the tenant assigned to the authenticated user."""
    tenant = TenantService(uow).get_for_user(current_user.tenant_id)
    return ApiResponse.ok(data=TenantOut.model_validate(tenant))


# ── Admin endpoints ──────────────────────────────────────────────────────────


@router.get(
    "/",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TENANT_LIST,
)
def list_tenants(
    uow: CurrentUow,
    current_user: CurrentAdminOrSuperAdmin,
    skip: SkipQuery = 0,
    limit: LimitQuery = 20,
) -> ApiResponse[TenantListResponse]:
    """GET /tenants/ — list tenants visible to the caller.

    super_admin sees all tenants (paginated); a Client Admin sees only their
    own tenant (see :meth:`TenantService.list_for_requester`).
    """
    tenant_service = TenantService(uow)
    tenants, total = tenant_service.list_for_requester(
        requester=current_user, skip=skip, limit=limit
    )
    project_counts = tenant_service.get_project_counts_by_tenant_ids([t.id for t in tenants])
    data = TenantListResponse(
        items=[
            TenantListItem(
                **TenantOut.model_validate(t).model_dump(),
                total_number_of_project=project_counts.get(t.id, 0),
            )
            for t in tenants
        ],
        total=total,
        skip=skip,
        limit=limit,
    )
    return ApiResponse.ok(data=data, message=MSG_TENANT_TOTAL.format(total=total))


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    summary=SUMMARY_TENANT_CREATE,
)
def create_tenant(
    body: TenantCreateRequest,
    uow: CurrentUow,
    _admin: CurrentAdmin,
) -> ApiResponse[TenantOut]:
    """POST /tenants/ — create a new tenant (super_admin only).

    Notifying the tenant's contact email is handled inside
    :meth:`TenantService.create`, not here.
    """
    tenant = TenantService(uow).create(
        name=body.name,
        contact_email=body.contact_email,
        address=body.address,
        status=body.status,
        providers=body.providers,
    )
    return ApiResponse.ok(data=TenantOut.model_validate(tenant))


@router.get(
    "/stats",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TENANT_STATS,
)
def get_tenant_stats(
    uow: CurrentUow,
    _admin: CurrentAdmin,
) -> ApiResponse[TenantStatsResponse]:
    """GET /tenants/stats — platform-wide tenant/project counts (super_admin only).

    Declared before ``/{tenant_id}`` so ``stats`` isn't matched as a
    ``tenant_id`` path parameter.
    """
    result = TenantService(uow).get_stats()
    return ApiResponse.ok(data=result, message=MSG_TENANT_STATS_FETCHED)


@router.get(
    "/{tenant_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TENANT_GET,
)
def get_tenant(
    tenant_id: uuid.UUID,
    uow: CurrentUow,
    current_user: CurrentAdminOrSuperAdmin,
) -> ApiResponse[TenantOut]:
    """GET /tenants/{tenant_id} — return a tenant by ID.

    super_admin may fetch any tenant; a Client Admin may fetch only their
    own (enforced in :meth:`TenantService.get_scoped`).
    """
    tenant = TenantService(uow).get_scoped(tenant_id, requester=current_user)
    return ApiResponse.ok(data=TenantOut.model_validate(tenant))


@router.patch(
    "/{tenant_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TENANT_UPDATE,
)
def update_tenant(
    tenant_id: uuid.UUID,
    body: TenantUpdateRequest,
    uow: CurrentUow,
    _admin: CurrentAdmin,
) -> ApiResponse[TenantOut]:
    """PATCH /tenants/{tenant_id} — update a tenant's fields (super_admin only)."""
    tenant = TenantService(uow).update(
        tenant_id,
        name=body.name,
        contact_email=body.contact_email,
        address=body.address,
        status=body.status,
        providers=body.providers,
    )
    return ApiResponse.ok(data=TenantOut.model_validate(tenant))


@router.put(
    "/{tenant_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TENANT_REPLACE,
)
def replace_tenant(
    tenant_id: uuid.UUID,
    body: TenantCreateRequest,
    uow: CurrentUow,
    _admin: CurrentAdmin,
) -> ApiResponse[TenantOut]:
    """PUT /tenants/{tenant_id} — fully replace a tenant (super_admin only).

    Full-replacement semantics: every field in *body* is applied as given,
    unlike PATCH's "only supplied fields change" — an omitted ``address``
    clears it and an omitted/empty ``providers`` clears all configured
    providers. Reuses ``TenantCreateRequest`` as the body schema since a
    full desired-state representation is identical in shape to a create
    payload (required ``name``/``contact_email``, ``status`` defaulting to
    ``active``, ``providers`` defaulting to empty).
    """
    tenant = TenantService(uow).replace(
        tenant_id,
        name=body.name,
        contact_email=body.contact_email,
        address=body.address,
        status=body.status,
        providers=body.providers,
    )
    return ApiResponse.ok(data=TenantOut.model_validate(tenant))


@router.delete(
    "/{tenant_id}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TENANT_DEACTIVATE,
)
def deactivate_tenant(
    tenant_id: uuid.UUID,
    uow: CurrentUow,
    _admin: CurrentAdmin,
) -> ApiResponse[None]:
    """DELETE /tenants/{tenant_id} — soft-deactivate a tenant (sets ``is_active = False``, super_admin only)."""
    TenantService(uow).deactivate(tenant_id)
    return ApiResponse.ok(message=MSG_TENANT_DEACTIVATED_SUCCESS)


# ── LLM provider configuration ───────────────────────────────────────────────


@router.get(
    "/{tenant_id}/llm-providers",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TENANT_LLM_PROVIDER_LIST,
)
def list_tenant_llm_providers(
    tenant_id: uuid.UUID,
    uow: CurrentUow,
    current_user: CurrentMemberOrAdminOrSuperAdmin,
) -> ApiResponse[TenantLLMProviderListResponse]:
    """GET /tenants/{tenant_id}/llm-providers — list this tenant's LLM provider configuration.

    Only returns providers the tenant has actually configured (one entry
    per configured provider — empty if none yet). super_admin may inspect
    any tenant; a Client Admin or Member only their own (enforced in
    :class:`TenantLLMProviderService`).
    """
    items = TenantLLMProviderService(uow).list_providers(tenant_id, requester=current_user)
    return ApiResponse.ok(data=TenantLLMProviderListResponse(items=items))


@router.patch(
    "/{tenant_id}/llm-providers/{provider}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TENANT_LLM_PROVIDER_UPDATE,
)
@limiter.limit(tenant_llm_provider_config_limit)
async def update_tenant_llm_provider(
    request: Request,
    tenant_id: uuid.UUID,
    provider: LLMProvider,
    body: TenantLLMProviderUpdateRequest,
    uow: CurrentUow,
    current_user: CurrentAdminOrSuperAdmin,
) -> ApiResponse[TenantLLMProviderOut]:
    """PATCH /tenants/{tenant_id}/llm-providers/{provider} — enable/disable and/or set the API key.

    The API key is encrypted at rest and never returned in full — only a
    masked hint. Setting a new key resets any prior connection-test result.
    Rejects activating a provider that has no API key configured at all.
    """
    result = TenantLLMProviderService(uow).update_provider(
        tenant_id,
        provider,
        api_key=body.api_key,
        is_active=body.is_active,
        requester=current_user,
    )
    return ApiResponse.ok(data=result, message=MSG_TENANT_LLM_PROVIDER_UPDATED)


@router.post(
    "/{tenant_id}/llm-providers/{provider}/test",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TENANT_LLM_PROVIDER_TEST,
)
@limiter.limit(tenant_llm_provider_test_limit)
async def test_tenant_llm_provider(
    request: Request,
    tenant_id: uuid.UUID,
    provider: LLMProvider,
    uow: CurrentUow,
    current_user: CurrentAdminOrSuperAdmin,
) -> ApiResponse[TenantLLMProviderTestResponse]:
    """POST /tenants/{tenant_id}/llm-providers/{provider}/test — test the stored API key.

    Makes a real, cheap authenticated call to the provider (e.g. a
    list-models call) and persists the outcome.
    """
    result = await TenantLLMProviderService(uow).test_provider(
        tenant_id, provider, requester=current_user
    )
    return ApiResponse.ok(data=result)


@router.get(
    "/{tenant_id}/llm-providers/{provider}/balance",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TENANT_LLM_PROVIDER_BALANCE,
)
@limiter.limit(tenant_llm_provider_balance_limit)
async def get_tenant_llm_provider_balance(
    request: Request,
    tenant_id: uuid.UUID,
    provider: LLMProvider,
    uow: CurrentUow,
    current_user: CurrentAdminOrSuperAdmin,
) -> ApiResponse[TenantLLMProviderBalanceResponse]:
    """GET /tenants/{tenant_id}/llm-providers/{provider}/balance — fetch the provider's remaining balance.

    Makes a real outbound call to the provider's billing API using the
    stored, decrypted API key. Only providers with a documented per-key
    balance endpoint are supported (currently DeepSeek) — others raise a 400
    explaining balance lookup isn't available for that provider.
    """
    result = await TenantLLMProviderService(uow).get_provider_balance(
        tenant_id, provider, requester=current_user
    )
    return ApiResponse.ok(data=result)


@router.delete(
    "/{tenant_id}/llm-providers/{provider}",
    status_code=status.HTTP_200_OK,
    summary=SUMMARY_TENANT_LLM_PROVIDER_DELETE,
)
@limiter.limit(tenant_llm_provider_config_limit)
async def delete_tenant_llm_provider(
    request: Request,
    tenant_id: uuid.UUID,
    provider: LLMProvider,
    uow: CurrentUow,
    current_user: CurrentAdminOrSuperAdmin,
) -> ApiResponse[None]:
    """DELETE /tenants/{tenant_id}/llm-providers/{provider} — soft-delete a provider's configuration.

    Sets ``deleted_at``/``is_active=False`` rather than removing the row —
    the stored key/verification history is preserved and the provider can be
    configured again afterwards via PATCH.
    """
    TenantLLMProviderService(uow).delete_provider(tenant_id, provider, requester=current_user)
    return ApiResponse.ok(message=MSG_TENANT_LLM_PROVIDER_DELETED)
