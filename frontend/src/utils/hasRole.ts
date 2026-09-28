import { store } from "@/store";
import type { UserRole } from "@/types/auth";

/**
 * Plain (non-hook) role check for use inside event handlers, callbacks, or
 * other non-render functions where hooks can't be called. Reads the current
 * user's roles directly from the store.
 *
 * Note: when `allow` is an array, this returns true if the user holds AT
 * LEAST ONE of the given roles (ANY-of), not all of them.
 */
export function hasRole(allow: UserRole | UserRole[]): boolean {
  const roles = (store.getState().auth.user?.roles ?? []) as UserRole[];
  const required = Array.isArray(allow) ? allow : [allow];
  return required.some((r) => roles.includes(r));
}
