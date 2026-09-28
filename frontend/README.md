# Requirement Intelligence Platform (RIP) — Frontend

AI-Powered Requirement Intelligence Platform. A Vite + React 19 SPA for managing, tracing, and governing project requirements end-to-end.

---

## Tech Stack

| Technology | Version | Purpose |
|---|---|---|
| React | 19.x | UI framework |
| TypeScript | 5.x | Type safety |
| Vite | 8.x | Build tool & dev server |
| React Router | 7.x | Client-side routing |
| Redux Toolkit | 2.x | Global state management |
| Axios | 1.x | HTTP client |
| Tailwind CSS | 4.x | Utility-first styling + design tokens |

---

## Getting Started

### Prerequisites

- Node.js **v24.14.1** or higher
- npm **v11** or higher

### Install Dependencies

```bash
npm install
```

### Environment Setup

Copy the example env file and configure:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `VITE_API_BASE_URL` | `/api` | Backend API base URL |

### Run Development Server

```bash
npm run dev
```

App will be available at **http://localhost:5173**

### Build for Production

```bash
npm run build
```

Output is in the `dist/` folder — serve via Nginx or any static file host.

### Preview Production Build

```bash
npm run preview
```

---

## Project Structure (Current)

```
src/
├── assets/                 # Icons, images, static resources
├── components/
│   ├── common/             # Shared building blocks (Modal, Loader, ProtectedRoute)
│   ├── layout/             # Layout shell (Sidebar, MainLayout, ContentLayout)
│   ├── temp/               # Existing reusable UI layer used by pages (Header, Table, etc.)
│   └── ui/                 # Primitive UI components (sidebar, switch, popover wrappers)
├── constants/              # App constants and validation/regex helpers
├── contexts/               # PageHeader context and provider
├── features/               # Domain modules (Projects, Pipelines, Review, etc.)
├── hooks/                  # Custom hooks (websocket, processing, pagination)
├── lib/                    # Core utility helpers (cn, class merge helpers)
├── pages/                  # Route-level screen components
├── routes/
│   ├── index.tsx           # Route objects (public + protected)
│   └── lazy.tsx            # Lazy import registry for route pages
├── services/
│   ├── apiClient.ts        # Axios base client
│   └── api/                # RTK Query API modules
├── store/
│   ├── index.ts            # Redux store setup
│   ├── hooks.ts            # Typed store hooks
│   └── slices/             # Feature/domain slices
├── styles/
│   ├── globals.css         # Tailwind v4 + theme tokens + global base layer
│   └── legacy styles       # Transitional styles under migration (do not add new styles here)
├── types/                  # Shared TS types/interfaces
├── utils/                  # Pure utility functions
├── App.tsx                 # RouterProvider + ToastContainer
└── main.tsx                # App bootstrap (StrictMode + Redux + PageHeaderProvider)
```

---

## Architecture Analysis (Current)

This frontend currently follows a **route-to-page shell pattern**:

- Route access control is centralized in `src/routes/index.tsx` via `ProtectedRoute`.
- The actual app shell (sidebar + top header) is applied at the **page level** using `MainLayout` and `Header` components.
- Router is created once in `src/App.tsx` using `createBrowserRouter(routes)`.
- Header metadata in route `handle` exists, but the visible page header is usually provided directly by `src/components/temp/Header.tsx`.

High-level flow:

```text
main.tsx
  -> Provider(store) + PageHeaderProvider
  -> App.tsx
    -> createBrowserRouter(routes)
    -> RouterProvider
      -> routes/index.tsx
        -> ProtectedRoute (auth gate)
          -> Page component
            -> MainLayout + Header + feature content
```

Current route map (from `src/routes/index.tsx`):

| Path | Access | Screen |
|---|---|---|
| /login | Public | LoginPage |
| / | Protected | DashboardPage |
| /projects | Protected | ProjectList |
| /projects/:id/project-sources | Protected | ProjectSources |
| /pipelines | Protected | PipelinesPage |
| /review/:projectId | Protected | ReviewPage |
| /requirements/:projectId | Protected | RequirementsPage |
| /settings | Protected | Settings |

---

## Change Guide 1: UI Design

Use this when updating visual style, page shell, or component-level UI.

### A. Global Theme and Design Tokens

- Edit `src/styles/globals.css` for global tokens, color system, spacing, and base typography.
- Prefer updating tokens first, then consume via utility classes/variables in components.
- For new UI work, use Tailwind utility classes and token variables from `globals.css`.

### B. App Shell (Sidebar + Top Header)

- Sidebar layout and navigation visuals: `src/components/layout/Sidebar.tsx`
- Shared page shell wrapper (sidebar + content area): `src/components/layout/MainLayout.tsx`
- Common top bar used by current pages: `src/components/temp/Header.tsx`

### C. Page-Level UI Composition

Each page composes the shell directly:

```tsx
return (
  <MainLayout header={<Header title="Page Title" />}>
    {/* page content */}
  </MainLayout>
)
```

Examples:
- `src/pages/DashboardPage.tsx`
- `src/pages/ProjectList/index.tsx`
- `src/pages/ProjectSources/index.tsx`

### UI Change Impact Checklist

1. Tokens changed in `globals.css`?
2. Sidebar spacing/width still valid in `MainLayout.tsx` and `Sidebar.tsx`?
3. Header height/border updates applied consistently via `Header.tsx`?
4. Key screens verified: Dashboard, Projects, Project Sources, Review, Requirements.

---

## Change Guide 2: Route Configuration

Use this when adding, removing, protecting, or reorganizing routes.

### A. Add/Update Route Definitions

- Route tree source of truth: `src/routes/index.tsx`
- Lazy import registry for route pages: `src/routes/lazy.tsx`

Example for adding a protected route:

```tsx
// src/routes/lazy.tsx
export const ReportsPage = lazy(() => import("@/pages/Reports"));

// src/routes/index.tsx (inside protected children)
{
  path: "/reports",
  handle: {
    title: "Reports",
    description: "Operational and analytical reports",
    icon: "▦",
  } satisfies PageHeaderData,
  element: (
    <SuspenseWrapper>
      <ReportsPage />
    </SuspenseWrapper>
  ),
}
```

### B. Authentication / Role Guarding

- Protected wrapper: `src/components/common/ProtectedRoute/index.tsx`
- Unauthenticated users redirect to `/login`.
- Optional role guard is supported through `allowedRoles`.

### C. Navigation Consistency

- Update sidebar links in `src/components/layout/Sidebar.tsx` when adding/removing top-level pages.
- For project-scoped routes, keep navigation path generation aligned (example: `/review/:projectId`, `/requirements/:projectId`).

### Route Change Impact Checklist

1. Route exists in `routes/index.tsx`?
2. Page is lazy-loaded from `routes/lazy.tsx` (if needed)?
3. Navigation entry updated in `Sidebar.tsx`?
4. Protected/public access verified with auth state?
5. URL params and downstream hooks/pages updated accordingly?

---

## Path Alias

`@/` is aliased to `src/`. Use it everywhere instead of relative paths:

```ts
// ✅ Good
import apiClient from '@/services/apiClient'
import { useAppSelector } from '@/store/hooks'

// ❌ Avoid
import apiClient from '../../services/apiClient'
```

---

## State Management (Redux Toolkit)

The store is configured in `src/store/index.ts`. Always use the typed hooks:

```ts
import { useAppDispatch, useAppSelector } from '@/store/hooks'
```

Add feature slices under `src/store/slices/` and register them in `src/store/index.ts`.

---

## API Client (Axios)

The configured Axios instance is at `src/services/apiClient.ts`:

- **Base URL** — read from `VITE_API_BASE_URL`
- **Auth** — sends browser credentials so HttpOnly auth cookies are included automatically
- **401 handler** — API callers should treat 401 as an unauthenticated session

Usage:

```ts
import apiClient from '@/services/apiClient'

const response = await apiClient.get('/sources')
const data = await apiClient.post('/sources', payload)
```

---

## Styling System (Current Direction)

- Primary styling system is Tailwind CSS + CSS variables defined in `src/styles/globals.css`.
- New screens/components should avoid introducing new SCSS module usage.
- During migration, existing legacy styles may remain in the repository, but they are not the target architecture for new implementation.

---

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```
