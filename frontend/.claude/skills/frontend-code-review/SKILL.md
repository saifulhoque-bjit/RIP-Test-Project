---
name: frontend-code-review
description: Review RIP frontend changes against this repo's conventions - RTK Query data layer, project WebSocket task state, routing/tab shell, RBAC, React hook correctness, TypeScript strictness, and Tailwind tokens. Use when asked to review frontend code, review a diff/branch/PR in this repo, or check whether a change follows RIP frontend conventions before commit.
---

# RIP Frontend Code Review

Convention- and correctness-focused review for this codebase. This is **not** a general bug hunt — for that, use `/code-review`. This skill checks the things that only break in *this* app: cache tags that don't invalidate, a second WebSocket, a tab that forgets to register, an action gated by role when it should be gated by permission.

## Procedure

1. **Scope the diff.** Unless the user names a target, review the working tree plus commits ahead of `mvp_development`:
   - `git status --short` and `git diff` for uncommitted work
   - `git diff mvp_development...HEAD` when reviewing a branch
   - a PR number/branch/path if the user gave one
2. **Read the full file around each change**, not just the hunk. Most findings below depend on context the diff omits (which tags an endpoint provides, whether an effect has cleanup, whether a tab is registered in all three places).
3. **Check `CLAUDE.md`** for architecture context before judging whether something is a deviation.
4. **Verify before reporting.** Grep for the thing you think is missing. "This mutation doesn't invalidate X" is only a finding once you've confirmed X is what the affected view reads.
5. **Report** grouped by severity, most severe first, each finding as:
   - `path/to/file.tsx:123` — one-sentence defect
   - the concrete failure: what a user does, what they see that's wrong
   - the fix, in one or two lines

Report nothing rather than pad. A clean diff gets "no findings" plus a one-line note on what you checked.

## Severity calibration

- **Blocker** — build/type failure, broken auth or access control, stale data a user will actually see, a second WebSocket connection, an unhandled terminal task state.
- **Should fix** — convention deviation that will cause a bug later or spread by copy-paste (URL literal outside `endpoints.ts`, raw permission string, missing `skip` guard).
- **Nit** — style, naming, a hard-coded color where a token exists. Cap these; don't bury the real findings.

Do not flag pre-existing patterns the diff merely sits next to. In particular: ~148 raw hex colors already exist across `src/features` and `src/components`, and there are ~12 existing `eslint-disable react-hooks/exhaustive-deps` comments. Only flag **new** instances, and only as nits unless they cause a real bug.

## Checklist

### Data layer (RTK Query)

- New data fetching goes through `baseApi.injectEndpoints` in `src/services/api/modules/*.ts` — never `fetch`/axios in a component. The one sanctioned exception is raw `XMLHttpRequest` for upload progress (precedent: `src/features/ProjectWorkspace/Sources/useFileUpload.ts`); note `src/services/apiClient.ts` is currently unused, so don't recommend it as the multipart path without checking.
- Every URL literal lives in `src/services/api/endpoints.ts` (`API_ENDPOINTS`). A path string inside a module or component is a finding.
- `injectEndpoints({ …, overrideExisting: false })`.
- Queries declare `providesTags`; mutations declare `invalidatesTags`. Project-scoped lists must use the composed id shape already in use — `{ type: "Module", id: \`LIST-${projectId}\` }`, `{ type: "Requirement", id: \`TREE-${projectId}\` }` / `SUMMARY-${projectId}` — so one project's write doesn't evict another's cache.
- **A mutation must invalidate every list its write affects, not just the obvious one.** Precedent: `cancelTask` in `modules/tasks.ts` invalidates `IngestionJob LIST` *and* the `Project` detail tag, because the project detail feeds the button-gating file count. Trace what the write changes server-side and which views read it.
- No `pollingInterval` and no manual `setInterval` + `refetch()` loops — the project WebSocket is the live channel. The repo has zero polling today; adding some needs a stated reason.
- Query args that can be undefined need `skip` (`useGetProjectQuery(projectId ?? "", { skip: !projectId })`).
- Use `currentData` instead of `data` when the query arg identity changes on navigation, so the previous entity isn't rendered while the new one loads (precedent: `pages/Project/index.tsx`).
- `baseApi` already toasts on error. A component that also calls `toast.error` in a `catch` double-toasts — flag it. If an error status is a *normal* state for that endpoint, the fix is `extraOptions: { suppressToastFor: [404] }` on the endpoint, not a local try/catch.
- Don't hand-roll 401 / token-refresh handling; `baseQueryWithReauth` owns it, single-flight.

### Live task state (WebSocket)

- Exactly one project socket, mounted in `pages/Project/index.tsx` via `useProjectTasksSocket`. A component/tab opening its own connection is a **blocker** — read status through `useProjectTasks` / `useLatestProjectTask` / `useIsProjectBusy` from `src/hooks/useProjectTaskStatus.ts`.
- A new backend `task_type` must be added in **all** the places it's consumed: `TASK_TYPE` in `src/types/projectTask.ts`, and the `switch` in `invalidateTagsForTaskType` in `useProjectTasksSocket.ts`. Missing the second means tables show stale data until remount — check it explicitly, it is the most common miss.
- A new terminal status string must be added to `TERMINAL_STATUSES`, or the UI will spin forever on a finished task.
- Socket/timer/listener effects need cleanup on unmount and must not `setState` after unmount.

### Routing and the workspace shell

- Adding a project workspace tab touches **three** places: the route in `src/routes/index.tsx`, the entry in `src/constants/projectTabs.ts`, and the render branch in `pages/Project/index.tsx`. Missing any one gives a tab that 404s, doesn't highlight, or renders blank.
- Route-level pages are lazy-registered in `src/routes/lazy.tsx` and wrapped in `<SuspenseWrapper>`.
- Routes carry `handle` metadata (`title`, `description`, `icon`, `backUrl`, `bodyClassName`). Page padding belongs in `bodyClassName`, not an ad-hoc wrapper div.
- Work that must survive tab switches belongs in `ProjectPage`, not in a tab that unmounts. Precedent: `useStoryFeedbackRegenerationResolver` / `useFeatureFeedbackRegenerationResolver` — a pending regeneration would stall if these lived in the Review tab.
- Rich header UI goes through `<PageHeaderPortal>`, not lifted into context state.

### Auth and access control

- **Routes gate on roles** — `<ProtectedRoute allowedRoles={…} deniedRoles={…}>` with `USER_ROLE`. **Actions and UI gate on permissions** — `PERMISSION` constants via `useHasPermission` / `useHasAllPermissions` / `<PermissionGate allow={…}>`. Using a role check to hide a button, or a permission check to guard a route, is a finding.
- No raw `"story:approve"`-style strings anywhere outside `src/constants/permissions.ts`; no raw role strings outside `src/types/auth.ts`.
- Tokens and email go to **sessionStorage** only. The `authUser` localStorage blob carries `id`, `displayName`, `roles`, `permissions` — anything else added there is a **blocker**.
- Client-side gating is UX, not enforcement. Don't accept "the button is hidden" as the reason a privileged call is safe; note when the backend check is the real guard.

### React correctness

- A **new** `eslint-disable react-hooks/exhaustive-deps` needs a comment saying why the omitted dep is intentional. Without one, flag it and state which dep goes stale.
- Values derived from props/state belong in render or `useMemo` — an effect that only calls `setState` from other state is a finding (extra render, stale-render flash).
- Object/array/function literals in dependency arrays re-fire the effect every render; check for it in new effects.
- List `key` must be a stable id, not the array index, wherever items can reorder, filter, or be deleted — the tree/table views in Review and Sources all reorder.
- Cleanup for `setTimeout`/`setInterval`/`addEventListener`/`WebSocket`/`AbortController`.
- New state that two sibling tabs both need probably belongs in a slice under `src/store/slices/`, not lifted through props.

### TypeScript

- **The repo currently has zero `any` and zero `as any`.** Any new one is a finding; say what the correct type is.
- No `enum`, no `namespace` — `erasableSyntaxOnly` is on. Enum-like values are `as const` objects plus a derived union (`TASK_TYPE`/`TaskType`, `PERMISSION`/`Permission`).
- Type-only imports use `import type` (`verbatimModuleSyntax`).
- Unused locals and parameters **fail `npm run build`**, not just lint — flag them as blockers, since CI won't get past `tsc -b`.
- API request/response shapes belong in `src/types/`, matching the existing per-domain file layout.
- Watch non-null assertions (`!`) on API data — responses are frequently optional (`result?.data?.items`); a `!` chain on freshly added fields is a runtime crash waiting for an empty list.

### Styling

- Tailwind v4 utilities plus tokens defined in the `@theme` block of `src/styles/globals.css`. If a matching token exists (`--color-ink`, `--color-surface`, `--color-canvas`, `--spacing-sidebar`, the brand scales), prefer it over a new hard-coded hex — nit severity, per the calibration note above.
- No SCSS modules; don't let one in.
- Conditional class strings go through `cn()` from `@/lib/cn`.
- Sidebar/header dimensions come from the spacing tokens, not magic pixel values.

### Shared components and UX

- Prefer the existing primitives in `src/components/common` — `Table`, `Pagination`, `Modal`, `Button`, `StatusChip`, `EmptyState`, `Loader`, `TreePanel` — over a bespoke reimplementation. A new component that duplicates one of these is a finding.
- Toasts go through `@/lib/toast` (never `react-toastify` directly) and pass a `toastId` so repeats dedupe.
- Any new data view needs loading, empty, and error states — not just the happy path.
- Paginated lists use `useUrlPagination` so the page survives reload and deep links.
- Markdown rendering must keep `rehypeSanitize` alongside `rehypeRaw` (precedent: `Review/components/right-panel/MarkdownViewer.tsx:946`). Dropping sanitize while keeping raw HTML is a **blocker**.
- User-supplied URLs are validated with `src/utils/isSupportedLinkUrl.ts` before being rendered as links or opened.

### Review feature specifics

- The evidence-highlighting behaviour in the markdown viewer is a written contract: `docs/CODE_BASE_MD_FILE_VIEW_GUIDE.md`. Read it before accepting changes to `MarkdownViewer` / `MdEvidencePreview` / `SrcEvidence`, and check the four `highlight_type` paths, the `line_number = 0` fallback, and the `GAP::` case are all still honoured.
- Review stage (`initial` → `first` → `second`) is derived from tree content in `Review/stage.ts`. Gating UI on a locally-inferred stage instead of `deriveReviewStage` will drift.

## Verifying

When a finding is mechanical, confirm it rather than asserting it:

```bash
npm run build     # tsc -b — catches unused vars, type errors, enum/import-type violations
npm run lint
```

Say plainly in the report whether you ran them and what they returned.
