import { store } from "@/store";
import type { Permission } from "@/constants/permissions";

/**
 * Plain (non-hook) permission check for use inside event handlers, callbacks,
 * or other non-render functions where hooks like `useHasPermission` can't be
 * called. Reads the current permissions directly from the store.
 *
 * Note: when `allow` is an array, this returns true if the user holds AT
 * LEAST ONE of the given permissions (ANY-of), not all of them.
 */
export function hasPermission(allow: Permission | Permission[]): boolean {
  const permissions = (store.getState().auth.user?.permissions ??
    []) as Permission[];
  const required = Array.isArray(allow) ? allow : [allow];
  return required.some((p) => permissions.includes(p));
}
