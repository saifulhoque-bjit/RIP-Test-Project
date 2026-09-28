import { useAppSelector } from "@/store/hooks";
import type { Permission } from "@/constants/permissions";

/** The current user's permission strings, as returned by the login API. */
export function usePermissions(): Permission[] {
  return useAppSelector(
    (s) => (s.auth.user?.permissions ?? []) as Permission[],
  );
}

/** True if the current user holds at least one of the given permissions. */
export function useHasPermission(
  permission: Permission | Permission[],
): boolean {
  const permissions = usePermissions();
  const required = Array.isArray(permission) ? permission : [permission];
  return required.some((p) => permissions.includes(p));
}

/** True only if the current user holds every one of the given permissions. */
export function useHasAllPermissions(required: Permission[]): boolean {
  const permissions = usePermissions();
  return required.every((p) => permissions.includes(p));
}
