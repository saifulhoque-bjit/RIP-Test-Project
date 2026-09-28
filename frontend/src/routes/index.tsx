import { type RouteObject } from "react-router-dom";
import ProtectedRoute from "@/components/common/ProtectedRoute";
import MainLayout from "@/components/layout/MainLayout";
import SuspenseWrapper from "@/components/common/SuspenseWrapper";
import type { PageHeaderData } from "@/contexts/PageHeaderContext";
import { USER_ROLE } from "@/types/auth";
import {
  LoginPage,
  AcceptInvitationPage,
  ResetPasswordPage,
  DashboardPage,
  ProjectList,
  ProjectPage,
  Settings,
} from "./lazy";

const routes: RouteObject[] = [
  // ── Public routes ──────────────────────────────────────────────────────────
  {
    path: "/login",
    element: (
      <SuspenseWrapper>
        <LoginPage />
      </SuspenseWrapper>
    ),
  },
  {
    path: "/invitations/accept",
    element: (
      <SuspenseWrapper>
        <AcceptInvitationPage />
      </SuspenseWrapper>
    ),
  },
  {
    path: "/reset-password",
    element: (
      <SuspenseWrapper>
        <ResetPasswordPage />
      </SuspenseWrapper>
    ),
  },

  // ── Protected routes ───────────────────────────────────────────────────────
  // All children below are guarded by ProtectedRoute and wrapped in MainLayout
  // (Sidebar + GlobalTopBar), mounted once here so it never remounts when
  // navigating between pages. Adding a new protected page = add a child here.
  {
    element: (
      <ProtectedRoute>
        <MainLayout />
      </ProtectedRoute>
    ),
    children: [
      // Module 1: Dashboard
      {
        path: "/",
        handle: {
          title: "Dashboard",
          description: "Overview of system activity and key metrics",
          bodyClassName: "p-5.5",
        } satisfies PageHeaderData,
        element: (
          <SuspenseWrapper>
            <DashboardPage />
          </SuspenseWrapper>
        ),
      },

      // Module 2: Project list
      {
        path: "/projects",
        handle: {
          title: "Projects",
          description: "A new project for developing a web application.",
          icon: "▤",
          bodyClassName: "p-5.5",
        } satisfies PageHeaderData,
        element: (
          <SuspenseWrapper>
            <ProjectList />
          </SuspenseWrapper>
        ),
      },

      // Module 3: Project workspace — unified tab-based layout
      {
        path: "/projects/:id/overview",
        handle: {
          title: "Projects",
          icon: "▤",
          bodyClassName: "!px-0",
        } satisfies PageHeaderData,
        element: (
          <ProtectedRoute deniedRoles={[USER_ROLE.SUPER_ADMIN]}>
            <SuspenseWrapper>
              <ProjectPage />
            </SuspenseWrapper>
          </ProtectedRoute>
        ),
      },
      {
        path: "/projects/:id/sources",
        handle: {
          title: "Projects",
          icon: "▤",
          bodyClassName: "!px-0",
        } satisfies PageHeaderData,
        element: (
          <ProtectedRoute deniedRoles={[USER_ROLE.SUPER_ADMIN]}>
            <SuspenseWrapper>
              <ProjectPage />
            </SuspenseWrapper>
          </ProtectedRoute>
        ),
      },
      {
        path: "/projects/:id/pipelines",
        handle: {
          title: "Projects",
          icon: "▤",
          bodyClassName: "!px-0",
        } satisfies PageHeaderData,
        element: (
          <ProtectedRoute deniedRoles={[USER_ROLE.SUPER_ADMIN]}>
            <SuspenseWrapper>
              <ProjectPage />
            </SuspenseWrapper>
          </ProtectedRoute>
        ),
      },
      {
        path: "/projects/:id/requirements",
        handle: {
          title: "Projects",
          icon: "▤",
          bodyClassName: "!px-0",
        } satisfies PageHeaderData,
        element: (
          <ProtectedRoute deniedRoles={[USER_ROLE.SUPER_ADMIN]}>
            <SuspenseWrapper>
              <ProjectPage />
            </SuspenseWrapper>
          </ProtectedRoute>
        ),
      },
      {
        path: "/projects/:id/review",
        handle: {
          title: "Projects",
          icon: "▤",
          bodyClassName: "!px-0",
        } satisfies PageHeaderData,
        element: (
          <ProtectedRoute deniedRoles={[USER_ROLE.SUPER_ADMIN]}>
            <SuspenseWrapper>
              <ProjectPage />
            </SuspenseWrapper>
          </ProtectedRoute>
        ),
      },
      {
        path: "/projects/:id/settings",
        handle: {
          title: "Projects",
          icon: "▤",
          bodyClassName: "!px-0",
        } satisfies PageHeaderData,
        element: (
          <ProtectedRoute deniedRoles={[USER_ROLE.SUPER_ADMIN]}>
            <SuspenseWrapper>
              <ProjectPage />
            </SuspenseWrapper>
          </ProtectedRoute>
        ),
      },
      {
        path: "/projects/:id/activity",
        handle: {
          title: "Projects",
          icon: "▤",
          bodyClassName: "!px-0",
        } satisfies PageHeaderData,
        element: (
          <ProtectedRoute deniedRoles={[USER_ROLE.SUPER_ADMIN]}>
            <SuspenseWrapper>
              <ProjectPage />
            </SuspenseWrapper>
          </ProtectedRoute>
        ),
      },

      // Module 4: Settings
      {
        path: "/admin",
        handle: {
          title: "Administration Settings",
          description: "Manage and organize your settings",
          backUrl: "/",
          icon: "⚙",
          bodyClassName: "py-6",
        } satisfies PageHeaderData,
        element: (
          <ProtectedRoute
            allowedRoles={[USER_ROLE.SUPER_ADMIN, USER_ROLE.CLIENT_ADMIN]}
          >
            <SuspenseWrapper>
              <Settings />
            </SuspenseWrapper>
          </ProtectedRoute>
        ),
      },
    ],
  },
];

export default routes;
