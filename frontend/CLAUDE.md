# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
npm run dev       # Vite dev server on http://localhost:5173
npm run build     # tsc -b && vite build  ← the real verification gate
npm run lint      # eslint .
npm run preview   # serve the production build from dist/
```

There is **no test runner** in this repo (no vitest/jest, no `*.test.*` files). `npm run build` is what catches regressions: `tsconfig.app.json` sets `strict`, `noUnusedLocals`, `noUnusedParameters`, and `erasableSyntaxOnly`, so an unused variable or an `enum` **fails the build**, not just lint. Always run `npm run build` before claiming a change is done.

Env (`.env`): `VITE_API_BASE_URL` (e.g. `http://localhost:8000/api/v1`), `VITE_CDN_URL`, optional `VITE_WS_BASE_URL`. WebSocket URLs are derived from `VITE_API_BASE_URL` in `src/lib/wsBaseUrl.ts` unless `VITE_WS_BASE_URL` overrides.

Default branch for work is `mvp_development` (not `master`). Commit subjects follow `[RIP][FE]: <summary>`.

## What this app is

Requirement Intelligence Platform frontend — a multi-tenant SPA where users upload requirement sources (docs, links, code), an AI backend runs long pipelines over them (module/feature/story generation and regeneration), and users review, approve, and sync the results to Jira/TAP. Two consequences shape the whole architecture: **almost every write kicks off an async backend task**, and **task progress arrives over a WebSocket, not polling**.

## Architecture

### Shell and routing

`main.tsx` (Provider + PageHeaderProvider) → `App.tsx` (`createBrowserRouter(routes)`) → `src/routes/index.tsx`.

All protected routes are children of a single `<ProtectedRoute><MainLayout /></ProtectedRoute>` element, so **Sidebar + GlobalTopBar mount once** and never remount on navigation. Pages are lazy-registered in `src/routes/lazy.tsx` and wrapped in `<SuspenseWrapper>`.

Each route carries `handle` metadata (`title`, `description`, `icon`, `backUrl`, `bodyClassName`) typed as `PageHeaderData`. `MainLayout` reads it via `useMatches` and pushes it into the page-header context — pages never set it themselves. **Page padding belongs in `handle.bodyClassName`**, not an ad-hoc wrapper div. Rich header content (like the project sub-header) is portaled up through `<PageHeaderPortal>` into a slot `MainLayout` renders below the top bar.

### The project workspace (the core screen)

`/projects/:id/{overview,sources,pipelines,review,requirements,settings,activity}` are **seven routes that all render the same `pages/Project/index.tsx`**. That component derives the active tab from `location.pathname` against `PROJECT_TABS` and renders one lazy feature from `src/features/ProjectWorkspace/*`.

Adding a workspace tab touches **three** places — miss one and you get a tab that 404s, doesn't highlight, or renders blank:
1. the route in `src/routes/index.tsx`
2. the entry in `src/constants/projectTabs.ts`
3. the render branch in `pages/Project/index.tsx`

Anything that must survive a tab switch lives in `ProjectPage`, not in a tab (tabs unmount). Existing precedent: `useStoryFeedbackRegenerationResolver` / `useFeatureFeedbackRegenerationResolver`, which resolve a pending regeneration even when the user has navigated away from Review.

### Data layer — RTK Query only

One `baseApi` (`src/services/api/baseApi.ts`); every domain module in `src/services/api/modules/*.ts` calls `baseApi.injectEndpoints({ …, overrideExisting: false })`. No axios/fetch in components (`src/services/apiClient.ts` exists but is unused). The one sanctioned exception is raw `XMLHttpRequest` for upload progress — see `features/ProjectWorkspace/Sources/useFileUpload.ts`.

- **Every URL literal lives in `src/services/api/endpoints.ts`** (`API_ENDPOINTS`). A path string anywhere else is a bug.
- `baseQueryWithReauth` owns auth: single-flight `POST /auth/refresh` on 401, retry once, `setUnauthenticated()` when refresh fails. Never hand-roll token refresh.
- **`baseApi` toasts on every error status.** A component that also toasts in `catch` double-toasts. If a status is a *normal* state for an endpoint, set `extraOptions: { suppressToastFor: [404] }` on the endpoint (see `createProject`), don't try/catch locally.
- Queries declare `providesTags`; mutations declare `invalidatesTags` for **every** list the write affects. Project-scoped lists use composed ids so one project's write doesn't evict another's cache: `{ type: "Module", id: \`LIST-${projectId}\` }`, `{ type: "Requirement", id: \`TREE-${projectId}\` }` / `SUMMARY-${projectId}`.
- **A subscription means the data is on screen right now.** On invalidation RTK Query refetches every *subscribed* cache entry and silently **drops** unsubscribed ones (`removeQueryResult`) — so deferral is already automatic for unmounted consumers, and the next mount fetches fresh. Never build a dirty-tag registry to "defer" fetching; RTK Query's invalidation state is the source of truth. The corollary is the part that's easy to get wrong: a component that subscribes to data it only shows *on demand* — behind a closed modal, drawer, or dropdown — pays a refetch for every invalidation to render nothing. Gate those with `skip` until they're needed: `{ skip: !isOpen }`. Precedent: `SyncTray`, `ExportModal`, `SubHeader`'s `ProjectSwitcher`, Review's Approve-architecture ingestion query. Watch for queries whose data is read only inside a click handler, and for list endpoints that tag every item (`getProjects` provides a tag per project id), which makes any one entity's invalidation refetch the whole list.
- Undefined-able args need `skip`: `useGetProjectQuery(projectId ?? "", { skip: !projectId })`.
- Use `currentData` (not `data`) where the arg changes on navigation, so the previous entity isn't rendered while the new one loads — precedent `pages/Project/index.tsx`.
- **No polling.** Zero `pollingInterval` / `setInterval + refetch` in the repo; the WebSocket is the live channel.

### Live task state — one socket per project

`useProjectTasksSocket(projectId)` is mounted **once**, in `ProjectPage`, and mirrors `/ws/projects/{id}` frames into the `projectTasks` slice. Tabs read status through `useProjectTasks` / `useLatestProjectTask` / `useIsProjectBusy` (`src/hooks/useProjectTaskStatus.ts`). A component opening its own project socket is a serious bug.

The socket also drives cross-tab freshness: on a terminal task status it calls `invalidateTagsForTaskType`, which maps a `task_type` to the RTK Query tags that data feeds. So **a new backend `task_type` must be added in two places** — `TASK_TYPE` in `src/types/projectTask.ts` *and* the `switch` in `useProjectTasksSocket.ts`. Missing the second means tables show stale data until remount; this is the most common miss. Likewise a new terminal status must go into `TERMINAL_STATUSES` or the UI spins forever.

The hook already handles the hard cases — reload (server replays `tasks.current`), dropped network (exponential back-off + `online` listener), and lying `readyState` after sleep/network-switch (heartbeat-staleness watchdog + `visibilitychange`). Don't reimplement these.

A **second, separate** socket exists for notifications (`useNotificationSocket` via `useNotifications`), mounted once in `GlobalTopBar`. That one is global, not project-scoped.

### Auth, roles, permissions, tenancy

- **Routes gate on roles**: `<ProtectedRoute allowedRoles={…} deniedRoles={…}>` with `USER_ROLE` from `src/types/auth.ts` (`super_admin` / `admin` / `member`).
- **Actions and UI gate on permissions**: `PERMISSION` constants from `src/constants/permissions.ts` via `useHasPermission` / `useHasAllPermissions` / `<PermissionGate allow={…}>`. Using a role to hide a button, or a permission to guard a route, is backwards.
- No raw `"story:approve"`-style strings outside `permissions.ts`; no raw role strings outside `types/auth.ts`.
- Storage split is deliberate: access/refresh token, cognito username, and **email** go to **sessionStorage**; only `{ id, displayName, roles, permissions }` go to the `authUser` localStorage blob. Adding anything else there is a regression.
- Client-side gating is UX only — the backend is the real guard.
- Tenancy: `useActiveTenant()` returns every tenant for `super_admin` and just their own for everyone else; the active id lives in `tenantSlice` and is surfaced by `TenantSwitcher`. **There is no default tenant** — a `super_admin` belongs to none, so `activeTenant` is `undefined` until they pick a client (header switcher, or the Clients table on `/settings` where that switcher is hidden). Every consumer must handle the undefined case; only single-tenant users get their one tenant resolved automatically.

### State

`src/store/index.ts` registers `baseApi.reducer` plus ~16 feature slices. Server data belongs in RTK Query, **not** a slice. Slices hold cross-component client state: live task state (`projectTasksSlice`), in-flight feedback/regeneration state per domain, viewer/selection UI state, auth/tenant, and the toast queue. State two sibling tabs both need belongs in a slice, not lifted through props.

Toasts go through `@/lib/toast` (which dispatches into `toastSlice` and dedupes by `toastId`) — never `react-toastify` directly.

## Conventions

- `@/` → `src/` everywhere; no relative climbing.
- **Zero `any` / `as any` in the repo today.** No `enum`, no `namespace` (`erasableSyntaxOnly`); enum-like values are `as const` objects plus a derived union — the `TASK_TYPE`/`TaskType`, `PERMISSION`/`Permission`, `USER_ROLE`/`UserRole` pattern. Type-only imports use `import type` (`verbatimModuleSyntax`).
- API request/response shapes go in `src/types/`, one file per domain. Responses are frequently optional (`result?.data?.items`) — a `!` chain on API data is a crash waiting for an empty list.
- Tailwind v4 with tokens in the `@theme` block of `src/styles/globals.css` (`--color-*`, `--spacing-sidebar`, `--spacing-header`, brand scales). Prefer a token over a new hex. No SCSS modules. Conditional classes go through `cn()` from `@/lib/cn`.
- Reuse the primitives in `src/components/common` (`Table`, `Pagination`, `Modal`, `Button`, `StatusChip`, `EmptyState`, `Loader`, `TreePanel`, `PermissionGate`) before writing a new one. Paginated lists use `useUrlPagination` so the page survives reload/deep-link.
- Markdown rendering must keep `rehypeSanitize` alongside `rehypeRaw`. User-supplied URLs go through `src/utils/isSupportedLinkUrl.ts` before being rendered or opened.
- Every new data view needs loading, empty, and error states.

## Feature-specific contracts

- **Review evidence highlighting** is a written spec: `docs/CODE_BASE_MD_FILE_VIEW_GUIDE.md`. Read it before touching `MarkdownViewer` / `MdEvidencePreview` / `SrcEvidence`; it defines the four `highlight_type` paths, the `line_number = 0` fallback, and the `GAP::` case.
- **Review stage** (`initial` → `first` → `second`) is derived from tree content by `deriveReviewStage` in `features/ProjectWorkspace/Review/stage.ts`. Don't infer stage locally — it will drift.

## Reviewing changes

`.claude/skills/frontend-code-review/SKILL.md` holds the full convention checklist with severity calibration and known pre-existing exceptions (~148 raw hex colors, ~12 existing `exhaustive-deps` disables — only flag *new* ones). Use it when asked to review a diff, branch, or PR here.
