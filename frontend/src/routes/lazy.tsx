import { lazy } from "react";

export const LoginPage = lazy(() => import("@/pages/LoginPage"));
export const AcceptInvitationPage = lazy(
  () => import("@/pages/AcceptInvitationPage"),
);
export const ResetPasswordPage = lazy(
  () => import("@/pages/ResetPasswordPage"),
);
export const DashboardPage = lazy(() => import("@/pages/Dashboard"));
export const ProjectList = lazy(() => import("@/pages/ProjectList"));
export const ProjectPage = lazy(() => import("@/pages/Project"));
export const Settings = lazy(() => import("@/pages/SettingPage"));
