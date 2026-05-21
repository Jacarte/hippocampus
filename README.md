# mem0server

This repository contains two applications:

- **`server/`** — A FastAPI REST backend over [`mem0ai`](https://github.com/mem0ai/mem0) that exposes memory CRUD, semantic search, hybrid retrieval, file-corpus indexing, and MCP bridge endpoints.
- **`cli/`** — `m0grep`, a native Rust CLI that indexes a local file corpus and searches across it (and the memory store) via the server's REST API.

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
├── cli/                     # Rust CLI (m0grep)
│   ├── src/                 # Rust source
│   └── Cargo.toml
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

### Configuration

Configuration is driven by environment variables. Accepted defaults:

```bash
MEM0_HOST=0.0.0.0
MEM0_PORT=8000
MEM0_WORKERS=1
MEM0_LOG_LEVEL=info
MEM0_HISTORY_DB_PATH=/var/lib/mem0/history.db

MEM0_VECTOR_PROVIDER=pgvector
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=postgres
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_COLLECTION=mem0_memories

MEM0_LLM_PROVIDER=openai
MEM0_LLM_MODEL=gpt-5
MEM0_LLM_TEMPERATURE=0.7

MEM0_EMBEDDER_PROVIDER=openai
MEM0_EMBEDDER_MODEL=text-embedding-3-small
```

OpenAI-backed configurations require `OPENAI_API_KEY`.

`MEM0_WORKERS=1` is the safe local/dev default.

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

`mem0ai==1.0.3` is the compatibility baseline. Keep it pinned unless a dedicated compatibility task changes it. If the pin changes, update `server/requirements.txt`, run the smoke paths and `pytest tests/ -v`, then update this README.

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

### OpenCode integration

- Plugin: `~/.config/opencode/plugins/mem0-functional.ts`
- Backend env var: `MEM0_SERVER_URL`
- Canonical default: `http://localhost:8000`

---

## CLI (`m0grep`)

`m0grep` is the native Rust CLI for the mem0server backend. Source lives in `cli/`. Binary name: `m0grep`.

### Installation

Download the pre-built binary from the [GitHub Releases](https://github.com/your-org/mem0server/releases) page:

```bash
# example for macOS/Linux — adjust the filename for your platform
curl -L https://github.com/your-org/mem0server/releases/latest/download/m0grep-x86_64-apple-darwin \
  -o m0grep
chmod +x m0grep
mv m0grep /usr/local/bin/m0grep
m0grep --help
```

### Environment

```bash
export MEM0_SERVER_URL=http://localhost:8000  # default — can be omitted
```

Every command also accepts `--url` to override the target host.

### Commands

#### query — search the corpus

```bash
m0grep query "authentication middleware"
m0grep query "token refresh" --corpus files --language-filter python
m0grep query "database connection" --limit 5 --path-filter src/db
m0grep query "retry logic" --raw
```

**Corpus values:** `all` (default), `files`, `memory`. **Limit range:** 1–50, default 10.

#### sync — index a directory

```bash
m0grep sync /path/to/project
m0grep sync /path/to/project --generate-summaries
```

#### watch — watch for file changes

```bash
m0grep watch /path/to/project
m0grep watch /path/to/project --generate-summaries
m0grep watch /path/to/project --stop
```

#### status — check index state

```bash
m0grep status
```

#### reset — wipe the index

```bash
m0grep reset
m0grep reset --yes    # skip confirmation prompt
```

#### Custom backend URL

```bash
m0grep status --url http://localhost:9000
m0grep sync /path/to/project --url http://remote-host:8000
```

### Server-side flags

**`generate_summaries`** — When `true` in a `POST /index/sync` or `POST /index/watch/start` body, the indexing pipeline generates natural-language summaries per chunk. Disabled by default.

**`USE_CHUNK_MEMORY`** (server env var) — Gates chunk-level memory features. Truthy values: `1`, `true`, `yes`.

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

#### `mgrep_status`

```json
{}
```

#### `mgrep_reset`

```json
{ "confirm": true }
```

### v1 scope

- File corpus indexes **code and Markdown files only**.
- Results are **raw ranked hits** — no synthesis or question answering.
- `mgrep_reset` is **destructive and irreversible**.
- The bridge does not expose memory CRUD endpoints — use the REST API directly.

---

## License

Based on original source: https://code.m3ta.dev/m3tam3re/nixpkgs/src/branch/master/pkgs/mem0/server.py

## Support

For issues with the upstream mem0 library itself, see: https://github.com/mem0ai/mem0
