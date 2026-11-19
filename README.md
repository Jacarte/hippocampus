# Hippocampus

This repository contains two applications:

- **`server/`** — A FastAPI REST backend over [`mem0ai`](https://github.com/mem0ai/mem0) for memory CRUD, semantic search, hybrid retrieval, and memory-store queries. The directory also includes a standalone MCP stdio bridge.
- **`cms/`** — A Bun + React + Vite admin UI for operating the server's memory endpoints.

The accepted local default server URL is `http://localhost:8000`.

## Repository Structure

```
hippocampus/
├── server/                  # Python FastAPI server
│   ├── server.py            # App entry point — wires FastAPI routes
│   ├── api_models.py        # Pydantic request/response models
│   ├── services/            # Service layer (memory, retrieval, anchors, MCP bridge, …)
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
cd hippocampus
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
GET  /metrics
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
POST /query
GET  /query/capabilities
```

Additive admin endpoints (v1 — internal CMS):

```
GET    /admin/health
GET    /admin/scopes
GET    /admin/memories?scope=<user|agent|run>&scope_id=<id>&page=<n>&page_size=<n>&query=<optional>
POST   /admin/memories
GET    /admin/memories/{memory_id}
PUT    /admin/memories/{memory_id}
DELETE /admin/memories/empty
DELETE /admin/memories/{memory_id}
POST   /admin/memories/{memory_id}/copy
POST   /admin/memories/{memory_id}/visits
```

### Configuration

Configuration is driven by environment variables. When the Python server runs
directly, it uses these runtime fallbacks for unset variables:

```bash
MEM0_HOST=0.0.0.0
MEM0_PORT=8000
MEM0_WORKERS=1
MEM0_LOG_LEVEL=info
MEM0_HISTORY_DB_PATH=/var/lib/mem0/history.db
MEM0_VISIT_DB_PATH=/var/lib/mem0/visits.db
MEM0_ADMIN_PAGE_SIZE_DEFAULT=20
MEM0_ADMIN_PAGE_SIZE_MAX=100

MEM0_RETRIEVAL_DECAY_ENABLED=false
MEM0_RETRIEVAL_DECAY_WEIGHT=0.05

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
MEM0_EMBEDDER_PROVIDER=openai
```

Hippocampus supports two LLM setups: direct OpenAI and a LiteLLM
Proxy/Gateway, which exposes an OpenAI-compatible API. For direct OpenAI, set
`MEM0_LLM_PROVIDER=openai` and `OPENAI_API_KEY`, configure the model with
`MEM0_LLM_MODEL` and related model settings, and optionally set
`OPENAI_BASE_URL` to override the API base URL.

`MEM0_LLM_TEMPERATURE` and `MEM0_EMBEDDER_MODEL` have no Python runtime
fallback.

Docker Compose loads values from `.env` and passes them into the container,
overriding the Python runtime fallbacks. It also supplies its own fallback or
fixed values, including four workers, a `litellm` LLM, and an `ollama`
embedder when the corresponding `.env` values are absent. The values in
`.env.example` are starter examples rather than runtime defaults; inspect that
file before copying it to `.env`. For direct local execution,
`MEM0_WORKERS=1` is the Python runtime fallback.

For the LiteLLM setup, Docker Compose passes `LITELLM_API_BASE`; point it at
the configured proxy. If that proxy requires authentication, the supplied
`OPENAI_API_KEY` must be a key accepted by the proxy. LiteLLM deployments do
not share a universal key requirement or endpoint.

### Admin CMS contract (v1)

The admin endpoints define the contract for an internal CMS operator tool. Key design decisions:

- **Decay display values are NOT computed in the admin responses.** The backend exposes raw fields (`created_at`, `decay_half_life_days`, `total_visits`, `last_visited_at`, etc.), and the CMS uses them to compute display values such as decay and recency scores. Decay can separately influence `/retrieve` *ranking* when `MEM0_RETRIEVAL_DECAY_ENABLED` is on (see [Recency-decay ranking](#recency-decay-ranking-retrieve)); that flag does not change any admin response field.
- **Popularity and freshness are separate signals.** Popularity comes from persisted visit telemetry (`total_visits`, `visit_ratio`). Freshness comes from `last_visited_at`, `created_at`, `decay_half_life_days`, and optional TTL fields. Never-visited items remain cold regardless of creation time.
- **Visit recording is explicit.** Detail views do not implicitly record visits via GET. The CMS must call `POST /admin/memories/{memory_id}/visits` with the appropriate reason (`detail_open`, `edit_save`, `copy_source`).
- **Audit stamps are applied server-side.** All CMS-initiated writes include `impersonated_by=admin`. Copy operations also include `copied_from` provenance metadata.
- **Remove `MEM0_VISIT_DB_PATH`** to reset visit telemetry (data lives separately from memory metadata).

### Recency-decay ranking (`/retrieve`)

By default, `/retrieve` ranks on lexical score, semantic similarity, Reciprocal
Rank Fusion, and the heuristic reranker's overlap/phrase/type bonuses. Memory age
is not a ranking signal, so a stale memory can outrank a fresh one with the same
relevance.

Setting `MEM0_RETRIEVAL_DECAY_ENABLED=true` adds a recency term to the
`/retrieve` reranking stage:

```bash
MEM0_RETRIEVAL_DECAY_ENABLED=true   # default false (Docker Compose defaults it to true)
MEM0_RETRIEVAL_DECAY_WEIGHT=0.05    # default 0.05, must be >= 0
```

- The term is **additive**: `score += recency * MEM0_RETRIEVAL_DECAY_WEIGHT`.
  `recency` is `0.5 ** (age_days / half_life_days)`, matching the CMS formula in
  `cms/src/lib/decay.ts`. `MEM0_RETRIEVAL_DECAY_WEIGHT` bounds how much decay can
  move a result; the sibling bonuses are 0.005-0.02, so start small and raise it
  to make age more decisive.
- Age is measured from `created_at` (top-level, else `metadata.created_at`), never
  `updated_at`, so editing a memory does not reset its decay curve. A memory with
  no parseable `created_at` gets a neutral `0.5`. An expired `metadata.ttl_expires_at`
  multiplies `recency` by `0.25`. Unparseable timestamps are ignored rather than
  failing the request.
- Half-life comes from `metadata.decay_half_life_days` when it is a positive
  number; otherwise it is derived from the memory type (`decision` and
  `stable-fact` 30 days, `procedure` 7, `problem-fix` 4, default 4).
- The `/retrieve` response advertises the active ranking via
  `backend_capabilities.decay`, so clients can tell which ranking a deployment
  uses. Because decay is applied inside the rerank stage, `decay` is `false`
  whenever `rerank` is `false`.
- **`/search` and `/query` ranking are unaffected** by this flag, and
  `/query/capabilities` does not advertise decay.

With the flag unset, ranking is byte-for-byte identical to the non-decay behavior.

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

The dev server proxies `/admin`, `/health`, `/memories`, `/search`, `/retrieve`, and `/query` to `http://localhost:8000` by default. Set `VITE_BACKEND_PROXY_TARGET` to change the backend origin at dev time, or `VITE_API_BASE_URL` to override the runtime API origin in a production build.

The backend must be running before the CMS can load data.

### CMS v1 exclusions

The admin CMS is an internal operator tool. The following features are not implemented in v1:

- **No authentication or authorization.** Any network client that can reach the admin endpoints can read and write memories. Run the CMS and server on a trusted network only.
- **No dashboards or analytics views.** The CMS shows raw memory data. There are no charts, graphs, or trend views.
- **No memory exports.** There is no bulk export, download, or backup facility in the CMS or the admin API.

### Running locally (without Docker)

```bash
cd hippocampus
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

The offline suite uses `fastapi.testclient.TestClient`, fake providers, and the
installed SDK with stubbed storage/LLM clients. The optional persistence probes
in `test_mem0_compatibility.py` skip when a live backend is unavailable.

### Canonical mem0ai version

`mem0ai==2.0.7` is the compatibility baseline. Keep it pinned unless a dedicated compatibility task changes it. If the pin changes, update `server/requirements.txt`, run the smoke paths and `pytest tests/ -v`, then update this README.

The mem0 2.0 series introduced breaking changes to the `PGVector` constructor (new required positional parameters `embedding_model_dims`, `diskann`, `hnsw`) and to `get_all`/`search` (entity IDs must now be passed inside a `filters` dict rather than as top-level kwargs). The server's `runtime.py`, `retrieval_service.py`, and `admin_service.py` handle these adaptations for the pinned 2.0.7 release.

### Memory selection and reliable writes

`POST /memories` accepts `infer` (default `true`). With inference enabled, the
server supplies the versioned policy in `server/services/memory_policy.py` as
mem0's top-level `custom_instructions`. It asks the extractor to retain accepted
decisions, confirmed fixes, stable preferences/constraints, and reusable
procedures while excluding speculation, temporary progress, narration, and
whole conversation or compaction summaries. These instructions guide an LLM;
they are not a deterministic classifier or a requirement to save something at
every turn or session boundary.

Configuration precedence is an explicit `/configure` `custom_instructions`
value, then `MEM0_CUSTOM_INSTRUCTIONS`, then the checked-in policy. An explicit
empty string or JSON `null` uses the SDK's native extraction instructions
without the Hippocampus addition. Environment configuration survives restarts;
`/configure` updates the current process only. `MEM0_LLM_EXTRA_CONFIG` still
configures the LLM provider and is not the extraction-policy setting.

Use `infer: false` when a caller has already selected the exact memory text.
This bypasses extraction; callers remain responsible for selecting appropriate
content. Metadata such as `source`, `observed_at`, and source identifiers is
preserved. An observation date in metadata records provenance; it does not
enable the OSS SDK's unsupported native `timestamp` feature.

Clients that retry captures can supply a stable `capture_id`:

```bash
curl -X POST http://localhost:8000/memories \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "Project A uses Redis for its expiring cache."}],
    "user_id": "user-1",
    "infer": false,
    "capture_id": "session-42:decision-3",
    "metadata": {"project_id": "project-a", "source": "pepi", "observed_at": "2026-11-18T10:00:00Z"}
  }'
```

Successful writes include `write_status: "saved"`; a valid selection with no new
memories returns `write_status: "skipped"`, `skip_reason: "no_new_memories"`, and
an empty `results` list. The strict compatibility boundary detects extraction,
embedding, and primary-storage errors that the pinned SDK can otherwise hide.
These return HTTP `503` with `detail.code: "memory_write_failed"` and an explicit
`retryable` boolean. Optional entity enrichment and audit-history fallbacks
remain SDK behavior; a saved acknowledgement concerns the primary memory write.

Capture IDs are scoped by the storage target and the exact user/agent/run
identifiers. PostgreSQL connection-string overrides and schema/role options
are included in target identity; credentials and tuning are excluded. Opaque
clients/pools or unresolved targets return HTTP `400` when a capture ID is
supplied, rather than sharing an unsafe receipt namespace.
Completed captures replay their acknowledgement without another
write, including after restart. Opt-in capture responses contain `results`
entries with IDs and events, plus the outcome and capture ID. Receipts retain
only the acknowledgement, not memory text or the original request; read memory
details through the normal memory endpoints. A replay reports the original
operation and does not establish that a subsequently edited/deleted record is
still present. Reusing a capture ID with a changed request
returns HTTP `409` with `detail.code: "capture_conflict"`. Store the original
request in the client's outbox and resend it unchanged; generated anchor
timestamps do not affect the request fingerprint.

Receipts live in `MEM0_WRITE_DB_PATH` (default `/var/lib/mem0/writes.db`). The
Docker Compose history volume already persists that directory. Workers must
share this file; separate instances with separate files do not coordinate
idempotency. Keep the receipt database for as long as clients may retry captures.

SQLite receipts and the vector store do not share a transaction. If the process
stops after starting persistence but before recording the result, retries return
HTTP `409` with `detail.code: "capture_pending"` and state `pending` or `unknown`.
They are not automatically re-executed after a timeout. Reconcile those captures
against the scoped memories carrying `metadata.capture_id` before resolving a
receipt or issuing another write. Only failures known to precede primary
persistence are automatically retryable. This prevents duplicate writes without
claiming crash-atomic delivery across two databases.

Clients without a `capture_id` retain the ordinary write path and do not create
receipts. Pepi still needs to transmit completed evidence, capture IDs, and
provenance and to handle retryable failures; this backend change does not alter
its extension or enable compaction-summary storage.

### Eligibility for agent recall

`/search`, `/retrieve`, and `/query` exclude explicitly non-injectable memories
(`inject: false`), `type: "noise"`, and archives (`cold_context: true`,
`type: "compaction-archive"`, or `tier: "cold"`/`"archive"`) from ordinary
recall. Older records without these flags remain eligible. Filtering occurs
before final selection; candidate fetching can expand to 200 records when
excluded entries occupy the initial result slots.

For deliberate archive inspection, `/search` and `/retrieve` accept
`filters: {"include_cold_context": true}`. This allows archive entries even when
their archive writer set `inject: false`; it never admits noise or non-archive
records explicitly marked non-injectable. The control must be a JSON boolean
and is not forwarded as a metadata filter. `/query` uses ordinary eligibility
and has no archive opt-in. Direct memory reads and admin views remain available
for inspecting stored records.

### What `/search` does today

`POST /search` provides semantic memory search. It delegates to `MemoryService.search(...)` → `RetrievalService.search(...)` → `memory_instance.search(...)`, and annotates the returned results with `_retrieval.stage: semantic`. The service also performs lexical processing for diagnostics, but it does not fuse lexical candidates into the returned result set.

### What `/retrieve` does today

`POST /retrieve` provides the backend's hybrid ranked result contract. It:

- gathers lexical candidates by scanning `memory_instance.get_all(...)`
- gathers semantic candidates through `memory_instance.search(...)`
- fuses candidates and reranks them only when both candidate stages succeed
- reports stage support and failures through `backend_capabilities`, `degraded`, and `degradation_reasons`

If the semantic or lexical stage fails, the endpoint falls back to results from the available stage. If reranking fails after both candidate stages succeed, it returns the fused candidates without reranking. The capability and degradation metadata describes what happened for that request; it does not guarantee stage availability or ranking quality.

### What `/query` does today

`POST /query` searches the memory store and returns normalized, score-filtered memory hits. The optional `corpora` selector accepts `memory_store` and the compatibility alias `all`; both values query only the memory store. Requests that explicitly select `file_corpus` are rejected with HTTP `422`.

`GET /query/capabilities` reports only memory-store capabilities. It does not describe the separate `/search` and `/retrieve` response contracts.

### MCP query interface

Hippocampus ships a standalone MCP bridge that connects a local MCP client to the REST backend. Make the backend reachable first, then configure the client to launch this command from the repository root:

```bash
python server/services/mcp_bridge.py
```

The bridge reads newline-delimited JSON-RPC from stdin and writes responses to stdout, so the client must run it as a local stdio child process. It sends queries to `MEM0_SERVER_URL`, which defaults to `http://localhost:8000`; set that environment variable when the backend uses another URL. The bridge advertises protocol version `2024-11-05`.

`start.sh` and the Docker image start only the REST service; they do not launch the bridge. The REST service exposes neither an `/mcp` nor an `/sse` route.

The bridge exposes one tool, `mgrep_query`, with these inputs:

- `query` (required): the search text.
- `corpora` (optional, default `["all"]`): a selector list whose advertised values are `memory_store` and `all`. Both values query only the memory store.
- `limit` (optional, default `10`): the maximum number of results, from 1 through 50.

The MCP schema does not expose the HTTP-only `user_id` or `min_score_memory` inputs. Successful tool content contains the JSON-serialized `/query` response; how that text is rendered depends on the client.

If a raw bridge call supplies the unsupported `file_corpus` value, the backend rejects it with HTTP `422`, and the bridge returns JSON-RPC error code `-32000` with the exact message `Backend error 422`. Transport failures use the fixed message `Backend request failed`, and unexpected internal failures use `Bridge error`. These public errors do not reflect backend response bodies, submitted inputs, transport details, or internal exception details. Unknown tools return JSON-RPC code `-32601`. The bridge does not expose memory creation, indexing, or lifecycle controls.

### Identifier requirements

`POST /memories`, `GET /memories`, and `DELETE /memories` require at least one of `user_id`, `agent_id`, or `run_id`. Missing identifiers return `400`.

The retrieval routes have separate payload contracts:

- `POST /search` requires `query`.
- `POST /retrieve` requires `query` and `scopes`, where `scopes` is a non-empty list of strings. It also accepts an optional `limit` from 1 through 50.
- Both routes accept optional `user_id`, `agent_id`, `run_id`, and `filters` fields.

### Minimal examples

```bash
# Create a memory
curl -X POST http://localhost:8000/memories \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"remember this"}],"user_id":"user-1","metadata":{"source":"chat"}}'
```

```bash
# Search memories
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"stored","user_id":"user-1","filters":{"source":"chat"}}'
```

```bash
# Retrieve hybrid-ranked memories
curl -X POST http://localhost:8000/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query":"stored","scopes":["project"],"user_id":"user-1","limit":10,"filters":{"source":"chat"}}'
```

```bash
# Health check
curl http://localhost:8000/health
```


---

## License

Based on original source: https://code.m3ta.dev/m3tam3re/nixpkgs/src/branch/master/pkgs/mem0/server.py

## Support

For issues with the upstream mem0 library itself, see: https://github.com/mem0ai/mem0
