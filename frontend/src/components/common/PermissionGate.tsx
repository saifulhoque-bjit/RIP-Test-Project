import type { ReactNode } from "react";
import type { Permission } from "@/constants/permissions";
import { useHasPermission } from "@/hooks/usePermission";

/**
 * Renders children only if the current user holds at least one of the
 * required permissions. Use for gating actions/sections (e.g. "+ New client").
 */
export function PermissionGate({
  allow,
  children,
  fallback = null,
}: {
  allow: Permission | Permission[];
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const isAllowed = useHasPermission(allow);

  if (!isAllowed) return <>{fallback}</>;
  return <>{children}</>;
}
