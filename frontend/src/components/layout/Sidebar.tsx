import { NavLink } from "react-router-dom";
import { useAppSelector } from "@/store/hooks";
import { selectCurrentUser } from "@/store/slices/authSlice";
import { cn } from "@/lib/utils";
import { Logo } from "@/components/common/Logo";
import { AdminIcon } from "@/assets/icons/AdminIcon";
import { ProjectsIcon } from "@/assets/icons/ProjectsIcon";
import { PermissionGate } from "../common/PermissionGate";
import { PERMISSION } from "@/constants/permissions";
import { USER_ROLE } from "@/types/auth";

type IconName = "dashboard" | "projects" | "settings" | "admin";

interface RailItem {
  to: string;
  icon: IconName;
  label: string;
  /** Full name when `label` is abbreviated to fit the rail — tooltip + a11y. */
  title?: string;
  end?: boolean;
}

const ITEMS: RailItem[] = [
  { to: "/", icon: "dashboard", label: "Dashboard", end: true },
  { to: "/projects", icon: "projects", label: "Projects" },
];

export default function Sidebar() {
  const user = useAppSelector(selectCurrentUser);

  // A super admin administers every client from this screen, so it reads as
  // "Administration" rather than the tenant-scoped "Settings". Projects are
  // tenant-scoped work a super admin has no access to (the route denies them),
  // so that rail item is dropped rather than left to bounce off ProtectedRoute.
  const isSuperAdmin = (user?.roles ?? []).includes(USER_ROLE.SUPER_ADMIN);
  const items = isSuperAdmin
    ? ITEMS.filter((item) => item.to !== "/projects")
    : ITEMS;

  return (
    <nav className="flex w-[82px] shrink-0 flex-col bg-gradient-to-b from-[var(--navy-900)] to-[var(--navy-800)] px-2.5 py-3.5">
      <div className="flex justify-center pb-4">
        <Logo size={34} />
      </div>

      {items.map((item) => (
        <RailLink key={item.to} {...item} />
      ))}

      <PermissionGate allow={[PERMISSION.TENANT_VIEW]}>
        <RailLink
          to="/admin"
          icon="admin"
          // "Administration" is wider than the 82px rail at the caption size,
          // and it is one unbreakable word, so the rail shows "Admin" and the
          // full name lives in the tooltip / accessible name.
          label="Admin"
          title={isSuperAdmin ? "Administration" : undefined}
        />
      </PermissionGate>

    </nav>
  );
}

function RailLink({ to, icon, label, title, end }: RailItem) {
  return (
    <NavLink
      to={to}
      end={end}
      title={title ?? label}
      aria-label={title ?? label}
      className={({ isActive }) =>
        cn(
          "relative mb-1 flex flex-col items-center gap-[4px] rounded-[10px] py-[11px] transition-colors no-underline",
          isActive
            ? "bg-[var(--accent-50)] text-[var(--navy-900)]"
            : "text-[#9fb0d0] hover:bg-white/[0.07] hover:text-white",
        )
      }
    >
      <RailIcon name={icon} />
      <span className="max-w-full truncate px-1 text-center text-[10px] font-semibold leading-tight">
        {label}
      </span>
    </NavLink>
  );
}

function RailIcon({ name }: { name: IconName }) {
  const p = {
    width: 21,
    height: 21,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.75,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (name) {
    case "dashboard":
      return (
        <svg {...p}>
          <rect x="3" y="3" width="7.5" height="7.5" rx="1.6" />
          <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6" />
          <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6" />
          <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6" />
        </svg>
      );
    case "projects":
      return <ProjectsIcon />;
    case "admin":
      return <AdminIcon />;
    case "settings":
      return (
        <svg
          width={21}
          height={17.5}
          viewBox="0 0 30 25"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M19.5 16C20.4292 16 21.24 16.5069 21.6709 17.2593C21.8803 17.6249 22 18.0485 22 18.5C22 18.9514 21.8804 19.3749 21.671 19.7404C21.2402 20.493 20.4293 21 19.5 21M21.6709 17.2593L23 16.4998M21.671 19.7404L23 20.4998M19.5 16C18.5708 16 17.76 16.5069 17.3291 17.2593C17.1197 17.6249 17 18.0485 17 18.5C17 18.9514 17.1196 19.3749 17.329 19.7404C17.7598 20.493 18.5707 21 19.5 21M19.5 21V22.5M17.3291 17.2593L16 16.4998M17.329 19.7404L16 20.4998M19.5 16V14.5" />
          <path d="M7.5 19.5V17.4704C7.5 16.2281 8.05927 15.0099 9.18968 14.4946C10.5685 13.8661 12.2221 13.5 14 13.5C15.0541 13.5 16.0646 13.6287 17 13.8645" />
          <path d="M14 10.5C15.933 10.5 17.5 8.933 17.5 7C17.5 5.067 15.933 3.5 14 3.5C12.067 3.5 10.5 5.067 10.5 7C10.5 8.933 12.067 10.5 14 10.5Z" />
        </svg>
      );
  }
}
