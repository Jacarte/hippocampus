> The cli will be deprecated, even before using it, since there are other better approaches

# mem0server

This repository contains two applications:

- **`server/`** — A FastAPI REST backend over [`mem0ai`](https://github.com/mem0ai/mem0) that exposes memory CRUD, semantic search, hybrid retrieval, file-corpus indexing, and MCP bridge endpoints.
- **`cms/`** — A Bun + React + Vite admin UI for operating the server's memory and indexing endpoints.

The accepted local default server URL is `http://localhost:8000`. The OpenCode memory plugin connects to this address by default.

## Repository Structure

```
mem0server/
├── server/                  # Python FastAPI server
│   ├── server.py            # App entry point — wires FastAPI routes
│   ├── api_models.py        # Pydantic request/response models
│   ├── services/            # Service layer (memory, retrieval, indexing, MCP bridge, …)
│   ├── tests/               # pytest suite
│   └── requirements.txt     # Python dependencies
├── cms/                     # Bun + React + Vite admin UI
├── Dockerfile               # Multi-stage build for the server
├── docker-compose.yaml      # Server + PostgreSQL/pgvector stack
├── start.sh / stop.sh / status.sh / build.sh
├── .env.example
├── VERSION
└── README.md
```

---

## Quick Start (Docker)

```bash
git clone <repository-url>
cd mem0server
cp .env.example .env
# Edit .env: set OPENAI_API_KEY and any overrides you need
./start.sh
```

The server will be available at:

- `http://localhost:8000` — API root (redirects to `/docs`)
- `http://localhost:8000/docs` — OpenAPI UI
- `http://localhost:8000/health` — Health check

Stop and remove volumes:

```bash
./stop.sh
```

---

## Server

### Backend shape

`server/server.py` creates the FastAPI app, wires routes, initializes runtime state, and maps service errors to HTTP responses.

Service modules:

- `server/services/memory_service.py` — request validation and main memory operations
- `server/services/retrieval_service.py` — semantic + lexical retrieval behavior

Public HTTP endpoints:

```
GET  /              → redirect to /docs
GET  /health
POST /configure
POST /memories
GET  /memories
GET  /memories/{memory_id}
PUT  /memories/{memory_id}
GET  /memories/{memory_id}/history
DELETE /memories/{memory_id}
DELETE /memories
POST /search
POST /retrieve
POST /reset
```

Additive admin endpoints (v1 — internal CMS):

```
GET    /admin/health
GET    /admin/memories?scope=<user|agent|run>&scope_id=<id>&page=<n>&page_size=<n>&query=<optional>
POST   /admin/memories
GET    /admin/memories/{memory_id}
PUT    /admin/memories/{memory_id}
DELETE /admin/memories/{memory_id}
POST   /admin/memories/{memory_id}/copy
POST   /admin/memories/{memory_id}/visits
GET    /admin/index/overview
```

### Configuration

Configuration is driven by environment variables. Accepted defaults:

```bash
MEM0_HOST=0.0.0.0
MEM0_PORT=8000
MEM0_WORKERS=1
MEM0_LOG_LEVEL=info
MEM0_HISTORY_DB_PATH=/var/lib/mem0/history.db
MEM0_VISIT_DB_PATH=/var/lib/mem0/visits.db
MEM0_ADMIN_PAGE_SIZE_DEFAULT=20
MEM0_ADMIN_PAGE_SIZE_MAX=100

MEM0_VECTOR_PROVIDER=pgvector
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=postgres
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_COLLECTION=mem0_memories
MEM0_EMBEDDING_MODEL_DIMS=1536
MEM0_PGVECTOR_DISKANN=false
MEM0_PGVECTOR_HNSW=false

MEM0_LLM_PROVIDER=openai
MEM0_LLM_MODEL=gpt-5
MEM0_LLM_TEMPERATURE=0.7

MEM0_EMBEDDER_PROVIDER=openai
MEM0_EMBEDDER_MODEL=text-embedding-3-small
```

OpenAI-backed configurations require `OPENAI_API_KEY`.

`MEM0_WORKERS=1` is the safe local/dev default.

### Admin CMS contract (v1)

The admin endpoints define the contract for an internal CMS operator tool. Key design decisions:

- **Decay scores are NOT computed in the backend.** The backend exposes raw fields (`created_at`, `decay_half_life_days`, `total_visits`, `last_visited_at`, etc.) and the CMS computes display values using the plugin-authority formulas from `~/.config/opencode/plugins/mem0-functional.ts` (`deriveHalfLifeDays`, `computeRecencyScore`).
- **Popularity and freshness are separate signals.** Popularity comes from persisted visit telemetry (`total_visits`, `visit_ratio`). Freshness comes from `last_visited_at`, `created_at`, `decay_half_life_days`, and optional TTL fields. Never-visited items remain cold regardless of creation time.
- **Visit recording is explicit.** Detail views do not implicitly record visits via GET. The CMS must call `POST /admin/memories/{memory_id}/visits` with the appropriate reason (`detail_open`, `edit_save`, `copy_source`).
- **Audit stamps are applied server-side.** All CMS-initiated writes include `impersonated_by=admin`. Copy operations also include `copied_from` provenance metadata.
- **Remove `MEM0_VISIT_DB_PATH`** to reset visit telemetry (data lives separately from memory metadata).

### Running the CMS

The `cms/` directory holds a Bun + React + Vite admin UI that talks to the admin endpoints above.

```bash
# Development (hot reload, Vite proxies /admin and /health to the backend)
cd cms && bun install && bun run dev
# → http://localhost:5173

# Production build + preview
cd cms && bun run build && bun run preview
# → http://localhost:4173
```

The dev server proxies `/admin`, `/health`, `/memories`, `/search`, `/retrieve`, `/query`, and `/index` to `http://localhost:8000` by default. Set `VITE_BACKEND_PROXY_TARGET` to change the backend origin at dev time, or `VITE_API_BASE_URL` to override the runtime API origin in a production build.

The backend must be running before the CMS can load data.

### CMS v1 exclusions

The admin CMS is an internal operator tool. The following features are not implemented in v1:

- **No authentication or authorization.** Any network client that can reach the admin endpoints can read and write memories. Run the CMS and server on a trusted network only.
- **No dashboards or analytics views.** The CMS shows raw memory data and index state. There are no charts, graphs, or trend views.
- **No persistent index history.** The index overview (`GET /admin/index/overview`) reflects the current process state only. Restarting the server clears all index data. The response always sets `limits.current_process_state_only = true`.
- **No memory exports.** There is no bulk export, download, or backup facility in the CMS or the admin API.

### Running locally (without Docker)

```bash
cd mem0server
python -m venv mem && source mem/bin/activate
pip install -r server/requirements.txt

# Direct execution
python server/server.py

# Equivalent uvicorn invocation
uvicorn server.server:app --host 0.0.0.0 --port 8000 --workers 1 --app-dir .
```

### Testing

```bash
cd server && pytest tests/ -v
```

All tests use `fastapi.testclient.TestClient` with fake memory implementations — no live OpenAI calls or network access required.

### Canonical mem0ai version

`mem0ai==2.0.0` is the compatibility baseline. Keep it pinned unless a dedicated compatibility task changes it. If the pin changes, update `server/requirements.txt`, run the smoke paths and `pytest tests/ -v`, then update this README.

mem0 2.0.0 introduced breaking changes to the `PGVector` constructor (new required positional parameters `embedding_model_dims`, `diskann`, `hnsw`) and to `get_all`/`search` (entity IDs must now be passed inside a `filters` dict rather than as top-level kwargs). The server's `runtime.py`, `retrieval_service.py`, and `admin_service.py` already handle these adaptations.

### What `/search` does today

`POST /search` delegates to `MemoryService.search(...)` → `RetrievalService.search(...)` → semantic search through `memory_instance.search(...)`. Results are annotated with `_retrieval.stage: semantic`.

### What `/retrieve` does today

`POST /retrieve` is the backend-owned hybrid retrieval endpoint:

- lexical recall: memory-store-only, scans `memory_instance.get_all(...)`
- semantic recall: `memory_instance.search(...)`
- in-process candidate fusion + simple deterministic reranking
- response includes `backend_capabilities`, `degraded`, and `trace.request_id`

Degradation behavior:
- semantic failure → lexical-only with `semantic=false`
- lexical failure → semantic-only with `lexical=false`
- rerank failure → pre-rerank fused results with `rerank=false`

### Identifier requirements

`POST /memories`, `GET /memories`, and `DELETE /memories` require at least one of `user_id`, `agent_id`, or `run_id`. Missing identifiers return `400`.

### Minimal examples

```bash
# Create a memory
curl -X POST http://localhost:8000/memories \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"remember this"}],"user_id":"user-1","metadata":{"source":"chat"}}'

# Search memories
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"stored","user_id":"user-1","filters":{"source":"chat"}}'

# Health check
curl http://localhost:8000/health
```

### File indexing options

**`generate_summaries`** — When `true` in a `POST /index/sync` or `POST /index/watch/start` body, the indexing pipeline generates natural-language summaries per chunk. Disabled by default.

**`USE_CHUNK_MEMORY`** (server env var) — Gates chunk-level memory features. Truthy values: `1`, `true`, `yes`.

### Native command-line client removal

The former native command-line client is intentionally no longer distributed. No drop-in command-line replacement exists. The REST API and MCP integration remain supported under their own contracts; neither is a command-line-compatible replacement.

### OpenCode integration

- Plugin: `~/.config/opencode/plugins/mem0-functional.ts`
- Backend env var: `MEM0_SERVER_URL`
- Canonical default: `http://localhost:8000`

---

## MCP Integration (OpenCode)

The repository ships a JSON-RPC 2.0 MCP bridge that lets OpenCode agents search the file corpus and memory store.

### Architecture

```
OpenCode agent
    │  stdio (JSON-RPC 2.0)
    ▼
server/services/mcp_bridge.py   ← MCP bridge process
    │  HTTP
    ▼
server/server.py (FastAPI)      ← mem0 REST backend
```

### Start the MCP bridge

```bash
# The backend must be running first
python3 -m server.services.mcp_bridge
```

### Register in opencode.json

```json
{
  "mcp": {
    "mgrep": {
      "type": "local",
      "command": "python3",
      "args": ["-m", "server.services.mcp_bridge"],
      "environment": {
        "MEM0_SERVER_URL": "http://localhost:8000"
      }
    }
  }
}
```

### Available tools

#### `mgrep_query`

```json
{
  "query": "how does retrieval ranking work",
  "corpora": ["file_corpus"],
  "limit": 10,
  "language_filter": "python"
}
```

Fields: `query` (required), `corpora` (`"memory_store"` | `"file_corpus"` | `"all"`, default `["all"]`), `limit` (1–50, default 10), `path_filter`, `language_filter`.

#### `mgrep_sync`

```json
{ "root": "/Users/javcab/mem0server" }
```

The root path must be accessible to the server process. This operation queues a server-side sync. When the client and server do not share a filesystem, the REST `POST /index/ingest` endpoint accepts file contents instead.

#### `mgrep_status`

```json
{}
```

#### `mgrep_reset`

```json
{ "confirm": true }
```

### v1 scope

- File corpus indexes **supported code, Markdown, plain-text, and reStructuredText files**.
- Results are **raw ranked hits** — no synthesis or question answering.
- `mgrep_reset` clears the current index and manifest, so re-indexing is required. Data whose source files are no longer available cannot be recreated.
- The bridge does not expose memory CRUD endpoints — use the REST API directly.

---

## License

Based on original source: https://code.m3ta.dev/m3tam3re/nixpkgs/src/branch/master/pkgs/mem0/server.py

## Support

For issues with the upstream mem0 library itself, see: https://github.com/mem0ai/mem0
