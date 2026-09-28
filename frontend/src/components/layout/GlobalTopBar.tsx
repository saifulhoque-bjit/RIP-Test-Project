import { useEffect, useMemo, useRef, useState } from "react";
import { usePageHeader } from "@/contexts/usePageHeader";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import { setUnauthenticated } from "@/store/slices/authSlice";
import { setActiveTenantId } from "@/store/slices/tenantSlice";
import { baseApi } from "@/services/api/baseApi";
import { useLogoutMutation } from "@/services/api/modules/auth";
import { useNotifications } from "@/hooks/useNotifications";
import TenantSwitcher from "@/components/layout/TenantSwitcher";
import { toast } from "@/lib/toast";
import { BellIcon } from "@/assets/icons/notifications/BellIcon";
import { USER_ROLE, USER_ROLE_LABEL, type UserRole } from "@/types/auth";
import Modal from "@/components/common/Modal";
import Button from "@/components/common/Button/Button";

/** Persistent global top bar: tenant badge, context label, health, bell, avatar. */
export default function GlobalTopBar() {
  const { header } = usePageHeader();
  const dispatch = useAppDispatch();
  const user = useAppSelector((s) => s.auth.user);

  // A super admin has no single active client, so the tenant switcher and
  // per-route page title never apply to them — every page they visit shows
  // a fixed "Admin Page" label instead.
  const isSuperAdmin = (user?.roles ?? []).includes(USER_ROLE.SUPER_ADMIN);
  const [bellOpen, setBellOpen] = useState(false);
  const [avatarOpen, setAvatarOpen] = useState(false);
  const bellRef = useRef<HTMLDivElement>(null);
  const avatarRef = useRef<HTMLDivElement>(null);
  const [logout, { isLoading: isLoggingOut }] = useLogoutMutation();

  const {
    notifications,
    handleMarkAllRead,
    handleNotificationClick,
    pendingExplanation,
    handleAcknowledgeExplanation,
  } = useNotifications();

  const unreadCount = useMemo(
    () => notifications.filter((n) => n.unread).length,
    [notifications],
  );

  const initials = useMemo(() => {
    if (!user?.displayName) return "?";
    return user.displayName
      .split(" ")
      .map((w) => w[0])
      .join("")
      .slice(0, 2)
      .toUpperCase();
  }, [user]);

  useEffect(() => {
    const handleOutsideClick = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!bellRef.current?.contains(target)) {
        setBellOpen(false);
      }
      if (!avatarRef.current?.contains(target)) {
        setAvatarOpen(false);
      }
    };
    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, []);

  const handleLogout = async (): Promise<void> => {
    try {
      await logout().unwrap();
      dispatch(setUnauthenticated());
      dispatch(setActiveTenantId(null));
      dispatch(baseApi.util.resetApiState());
      toast.success("Logout successful! See you next time.");
    } catch (error) {
      const status = (error as { status?: unknown }).status;

      if (status === 401) {
        dispatch(setUnauthenticated());
        dispatch(setActiveTenantId(null));
        dispatch(baseApi.util.resetApiState());
        toast.success("Logout successful! See you next time.");
      }
    }
  };

  return (
    <>
    <div className="relative flex h-[58px] shrink-0 items-center gap-3.5 border-b border-[var(--border-primary)] bg-white px-5">
      {isSuperAdmin ? (
        <div className="flex items-center gap-2.5 text-lg font-extrabold tracking-tight text-[var(--navy-900)]">
          RIP
          <span className="ml-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-quaternary)]">
            Requirement Intelligence
          </span>
        </div>
      ) : (
        <>
          <TenantSwitcher />

          <span
            className="h-5 w-[1px] flex-shrink-0 bg-[var(--mut)]"
            aria-hidden="true"
          />

          <span className="text-[13px] text-[var(--mut)]">{header?.title}</span>
        </>
      )}
      <div className="flex-1" />

      {/* Health indicator */}
      {/* <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border-primary)] px-2.5 py-1 text-[11.5px] font-semibold text-[var(--success)]">
        <span className="inline-block h-[7px] w-[7px] rounded-full bg-[var(--success)]" />
        All providers healthy
      </span> */}

      {/* Notification bell */}
      <div ref={bellRef}>
        <button
          type="button"
          onClick={() => {
            setBellOpen((o) => !o);
            setAvatarOpen(false);
          }}
          className="relative rounded p-1 text-lg text-[var(--text-secondary)]"
          aria-label="Notifications"
          aria-expanded={bellOpen}
        >
          <BellIcon aria-hidden="true" />
          {unreadCount > 0 && (
            <span className="w-5 absolute -right-[5px] -top-[5px] min-w-[14px] rounded-[8px] bg-[var(--error)] px-1 text-center text-[9px] font-bold leading-[14px] text-white">
              {unreadCount > 9 ? "9+" : unreadCount}
            </span>
          )}
        </button>

        {/* Notification dropdown */}
        {bellOpen && (
          <div className="absolute right-[18px] top-[52px] z-[75] w-[360px] overflow-hidden rounded-xl border border-[var(--border-primary)] bg-white shadow-[0_16px_40px_rgba(8,21,52,.24)]">
            <div className="flex items-center justify-between border-b border-[var(--border-primary)] px-[14px] py-3 text-[13px] font-bold text-[var(--text-primary)]">
              <span>Notifications</span>
              <button
                type="button"
                onClick={handleMarkAllRead}
                className="border-none bg-transparent p-0 text-xs font-semibold text-[var(--accent-600)]"
              >
                Mark all read
              </button>
            </div>

            <div className="max-h-[360px] overflow-y-auto">
              {notifications.length === 0 ? (
                <div className="px-[14px] py-3 text-xs text-[var(--text-tertiary)]">
                  No notifications
                </div>
              ) : (
                notifications.map((n) => (
                  <button
                    key={n.id}
                    type="button"
                    onClick={() => handleNotificationClick(n)}
                    className="flex w-full gap-2.5 border-b border-[var(--border-primary)] bg-transparent px-[14px] py-[11px] text-left transition-colors hover:bg-[#fafbfd]"
                  >
                    <span
                      className={`flex-shrink-0 ${
                        n.unread
                          ? "text-[var(--accent-600)]"
                          : "text-[var(--text-quaternary)]"
                      }`}
                    >
                      {n.icon ?? (
                        <BellIcon className="h-4 w-4" aria-hidden="true" />
                      )}
                    </span>
                    <span className="flex-1 min-w-0">
                      <span
                        className={`block text-[12.5px] text-[var(--text-primary)] ${
                          n.unread ? "font-bold" : "font-normal"
                        }`}
                      >
                        {n.title}
                      </span>
                      {n.message && (
                        <span
                          className={`mt-0.5 block text-[11px] font-normal ${
                            n.unread
                              ? "text-[var(--text-secondary)]"
                              : "text-[var(--text-tertiary)]"
                          }`}
                        >
                          {n.message}
                        </span>
                      )}
                      {n.createdAt && (
                        <span
                          className={`mt-1 block text-[10px] text-[var(--text-quaternary)] ${
                            n.unread ? "font-bold" : "font-normal"
                          }`}
                        >
                          {n.createdAt}
                        </span>
                      )}
                    </span>
                    <span
                      aria-hidden="true"
                      className={`mt-1.5 h-2 w-2 shrink-0 rounded-full bg-[var(--accent)] ${
                        n.unread ? "" : "invisible"
                      }`}
                    />
                  </button>
                ))
              )}
            </div>
          </div>
        )}
      </div>

      {/* User avatar */}
      <div ref={avatarRef}>
        <button
          type="button"
          onClick={() => {
            setAvatarOpen((o) => !o);
            setBellOpen(false);
          }}
          className="grid h-8 w-8 place-items-center rounded-full border-none bg-[var(--accent)] p-0 text-xs font-bold text-white cursor-pointer"
          aria-label="Account menu"
          aria-expanded={avatarOpen}
        >
          {initials}
        </button>

        {avatarOpen && (
          <div className="absolute right-[18px] top-[52px] z-[75] w-[220px] overflow-hidden rounded-[12px] border border-[var(--border-primary)] bg-white shadow-[0_16px_40px_rgba(8,21,52,.24)]">
            <div className="space-y-1 border-b border-[var(--border-primary)] px-3.5 py-3">
              <div className="text-[13px] font-bold text-[var(--text-primary)]">
                {user?.displayName ?? "User"}
              </div>
              <div className="text-[11px] text-[var(--text-quaternary)]">
                {user?.email || "-"}
              </div>
              {user?.roles && user.roles.length > 0 && (
                <div className="font-semibold text-[11px] text-[var(--text-quaternary)] uppercase">
                  {user.roles
                    .map((r) => USER_ROLE_LABEL[r as UserRole] ?? r.replace(/_/g, " "))
                    .join(", ")}
                </div>
              )}
            </div>
            {/* <button
              type="button"
              onClick={() => {
                setAvatarOpen(false);
                toast.info("Account settings are managed by your administrator.", {
                  toastId: "profile-preferences",
                });
              }}
              className="block w-full border-none bg-transparent px-3.5 py-2.5 text-left text-[12.5px] text-[var(--text-primary)] cursor-pointer hover:bg-[#fafbfd]"
            >
              Profile &amp; preferences
            </button> */}
            <button
              type="button"
              onClick={() => {
                setAvatarOpen(false);
                void handleLogout();
              }}
              disabled={isLoggingOut}
              className="block w-full border-x-0 border-b-0 border-t border-[var(--border-primary)] bg-transparent px-3.5 py-2.5 text-left text-[12.5px] font-semibold text-[var(--error)] cursor-pointer hover:bg-[var(--error-50)] disabled:cursor-not-allowed disabled:opacity-70"
            >
              Log out
            </button>
          </div>
        )}
      </div>
    </div>

    <Modal
      isOpen={!!pendingExplanation}
      onClose={handleAcknowledgeExplanation}
      showCloseButton={false}
      title={pendingExplanation?.title}
      footer={<Button onClick={handleAcknowledgeExplanation}>Ok</Button>}
    >
      {pendingExplanation?.explanation}
    </Modal>
    </>
  );
}
