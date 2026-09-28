import { useEffect } from "react";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  selectActiveTenantId,
  setActiveTenantId,
} from "@/store/slices/tenantSlice";
import {
  useGetTenantsQuery,
  useGetMyTenantQuery,
} from "@/services/api/modules/tenants";
import { USER_ROLE } from "@/types/auth";
import type { Tenant } from "@/types";
import { hasRole } from "@/utils/hasRole";

/**
 * The tenants a user can see — every tenant for super_admin, just their own
 * for everyone else — and whichever one is currently active.
 *
 * There is no default tenant. A super_admin belongs to no tenant, so nothing
 * is active until they pick a client and `activeTenant` stays undefined until
 * then; callers must handle that. Everyone else has exactly one tenant, which
 * resolves on its own because there is nothing to choose between.
 */
export function useActiveTenant(): {
  tenants: Tenant[];
  activeTenant: Tenant | undefined;
  isSuperAdmin: boolean;
  isLoading: boolean;
  hasError: boolean;
} {
  const dispatch = useAppDispatch();
  const activeTenantId = useAppSelector(selectActiveTenantId);
  const isSuperAdmin = hasRole(USER_ROLE.SUPER_ADMIN) ?? false;

  const {
    data: tenantsResponse,
    isLoading: isTenantsLoading,
    error: tenantsError,
  } = useGetTenantsQuery(undefined, { skip: !isSuperAdmin });
  const {
    data: myTenantResponse,
    isLoading: isMyTenantLoading,
    error: myTenantError,
  } = useGetMyTenantQuery(undefined, { skip: isSuperAdmin });

  const tenants: Tenant[] = isSuperAdmin
    ? (tenantsResponse?.data?.items ?? [])
    : myTenantResponse?.data
      ? [myTenantResponse.data]
      : [];

  const selectedTenant = tenants.find((t) => t.id === activeTenantId);
  const activeTenant = isSuperAdmin ? selectedTenant : (tenants[0] ?? undefined);

  // Adopt the single tenant a non-super-admin belongs to once it loads. A
  // super_admin is never defaulted into a client — they choose one explicitly.
  useEffect(() => {
    if (isSuperAdmin) return;
    if (!activeTenantId && tenants.length > 0) {
      dispatch(setActiveTenantId(tenants[0].id));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSuperAdmin, activeTenantId, tenantsResponse, myTenantResponse, dispatch]);

  return {
    tenants,
    activeTenant,
    isSuperAdmin,
    isLoading: isSuperAdmin ? isTenantsLoading : isMyTenantLoading,
    hasError: !!(isSuperAdmin ? tenantsError : myTenantError),
  };
}
