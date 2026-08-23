# Mem0 CMS

Internal Bun + React + Vite admin UI for the mem0 server. The CMS lets an operator browse, inspect, edit, and copy memories through the server's admin API endpoints.

## Prerequisites

The mem0 server must be running before the CMS can load data. See the repository root `README.md` for server startup instructions. The canonical backend address is `http://localhost:8000`.

## Running

### Development

```bash
cd cms && bun install && bun run dev
```

The Vite dev server starts at `http://localhost:5173` and proxies these paths to the backend:

- `/admin` — admin memory CRUD and visits
- `/health` — server health check
- `/memories`, `/search`, `/retrieve`, `/query` — read-only memory and retrieval endpoints

Set `VITE_BACKEND_PROXY_TARGET` to point the proxy at a different backend origin. For example, to reproduce a bug against the remote backend while keeping the CMS on localhost:

```bash
VITE_BACKEND_PROXY_TARGET=http://192.168.0.160:18000 bun run dev
```

Vite forwards every proxied path (`/admin`, `/health`, etc.) to the target origin. The browser still talks to `http://localhost:5173`, so all requests remain same-origin. No CORS headers, preflight, or cookie-domain changes are needed.

### Production build and preview

```bash
cd cms && bun run build && bun run preview
```

The build outputs static assets to `cms/dist/`. The preview server starts at `http://localhost:4173`. Set `VITE_API_BASE_URL` at build time to override the API origin in the production bundle.

## Admin API routes

The CMS consumes these server endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/health` | Admin-scoped health check |
| GET | `/admin/memories` | Paginated memory list with scope, query, and page filters |
| POST | `/admin/memories` | Create a new memory |
| GET | `/admin/memories/{id}` | Single memory detail with popularity and freshness blocks |
| PUT | `/admin/memories/{id}` | Update memory content and metadata |
| DELETE | `/admin/memories/{id}` | Delete a memory |
| POST | `/admin/memories/{id}/copy` | Copy a memory with provenance tracking |
| POST | `/admin/memories/{id}/visits` | Record a visit event (explicit, not implicit) |

## How visits work

Visits are explicit. Opening a memory detail in the CMS does not automatically record a visit. The CMS must call `POST /admin/memories/{id}/visits` with a reason:

- `detail_open` — operator opened the detail view
- `edit_save` — operator saved an edit
- `copy_source` — operator copied the memory

The backend persists `total_visits`, `last_visited_at`, and related aggregates. The CMS reads these raw fields and computes display values (decay scores, recency) client-side using the formulas from `~/.config/opencode/plugins/mem0-functional.ts` (`deriveHalfLifeDays`, `computeRecencyScore`).

## How decay works

The backend does not compute decay scores. It exposes raw fields: `created_at`, `decay_half_life_days`, `total_visits`, `last_visited_at`, and related TTL metadata. The CMS applies the plugin-authority decay formulas to produce the display values shown in the UI. Popularity (visit counts) and freshness (time-based decay) are separate signals. A memory that has never been visited stays cold regardless of how recently it was created.

## Environment variables

The CMS itself reads no server-side environment variables. The backend variables that affect CMS behavior:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MEM0_VISIT_DB_PATH` | `/var/lib/mem0/visits.db` | SQLite file for visit telemetry. Delete to reset visit data. |
| `MEM0_ADMIN_PAGE_SIZE_DEFAULT` | `20` | Default page size for `GET /admin/memories` |
| `MEM0_ADMIN_PAGE_SIZE_MAX` | `100` | Maximum allowed page size |

## v1 exclusions

- **No authentication.** Any client that reaches the admin endpoints can read and write memories.
- **No dashboards or analytics.** The CMS shows raw data, not charts or trends.
- **No bulk exports.** There is no download or backup facility.

## Commands reference

```bash
bun install       # Install dependencies
bun run dev       # Start dev server with hot reload (port 5173)
bun run build     # Type-check and build for production (outputs to dist/)
bun run preview   # Serve the production build locally (port 4173)
bun run lint      # Run ESLint
```
