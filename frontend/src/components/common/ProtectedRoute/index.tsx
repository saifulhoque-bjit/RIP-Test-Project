import { Navigate, useLocation } from "react-router-dom";
import { useAppSelector } from "@/store/hooks";
import {
  selectCurrentUser,
  selectIsAuthenticated,
} from "@/store/slices/authSlice";
import type { Permission } from "@/constants/permissions";

interface ProtectedRouteProps {
  children: React.ReactNode;
  /** When provided, access is restricted to users with a matching role. */
  allowedRoles?: string[];
  /** When provided, users holding any of these roles are denied access — regardless of other roles they hold. */
  deniedRoles?: string[];
  /** When provided, access requires at least one of these permissions. */
  requiredPermissions?: Permission[];
}

/**
 * ProtectedRoute — redirects unauthenticated users to /login and enforces
 * role/permission-based access. Does not apply any layout; layout is handled
 * in routes.
 */
export default function ProtectedRoute({
  children,
  allowedRoles,
  deniedRoles,
  requiredPermissions,
}: ProtectedRouteProps) {
  const isAuthenticated = useAppSelector(selectIsAuthenticated);
  const user = useAppSelector(selectCurrentUser);
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  const roles = user?.roles ?? [];

  if (allowedRoles && allowedRoles.length > 0) {
    const hasAllowedRole = roles.some((r) => allowedRoles.includes(r));
    if (!hasAllowedRole) {
      return <Navigate to="/" replace />;
    }
  }

  if (deniedRoles && deniedRoles.length > 0) {
    const hasDeniedRole = roles.some((r) => deniedRoles.includes(r));
    if (hasDeniedRole) {
      return <Navigate to="/" replace />;
    }
  }

  if (requiredPermissions && requiredPermissions.length > 0) {
    const permissions = user?.permissions ?? [];
    const hasAccess = requiredPermissions.some((p) =>
      permissions.includes(p),
    );
    if (!hasAccess) {
      return <Navigate to="/" replace />;
    }
  }

  return <>{children}</>;
}
