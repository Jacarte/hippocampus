# Mem0 REST API Server

This repository is the current backend for the local OpenCode mem0 plugin. It exposes a small FastAPI surface over `mem0ai` for memory CRUD, semantic search, hybrid retrieval, configuration, and health checks.

The accepted local default plugin URL is:

- `http://localhost:8000`

Use that URL unless you are intentionally overriding the backend host or port.

## Current backend shape

The backend has already been split into a thin app layer plus service modules.

- `server.py` creates the FastAPI app, wires routes, initializes runtime state, and maps service errors to HTTP responses.
- `services/memory_service.py` owns request validation and the main memory operations used by the routes.
- `services/retrieval_service.py` owns retrieval behavior. Today that means semantic search through `mem0ai` plus an internal lexical helper that scans existing stored memories.

The public HTTP contract currently exposed by `server.py` is:

- `GET /` redirects to `/docs`
- `GET /health`
- `POST /configure`
- `POST /memories`
- `GET /memories`
- `GET /memories/{memory_id}`
- `POST /search`
- `POST /retrieve`
- `PUT /memories/{memory_id}`
- `GET /memories/{memory_id}/history`
- `DELETE /memories/{memory_id}`
- `DELETE /memories`
- `POST /reset`

There is no separate public lexical search endpoint today.

## OpenCode integration

This backend is the canonical local server used by the OpenCode memory plugin.

- Plugin: `~/.config/opencode/plugins/mem0-functional.ts`
- Backend env var: `MEM0_SERVER_URL`
- Canonical default: `http://localhost:8000`

## Runtime defaults

These are the accepted local and dev defaults that the README and plugin docs should stay aligned with:

```bash
MEM0_HOST=0.0.0.0
MEM0_PORT=8000
MEM0_WORKERS=1
MEM0_SERVER_URL=http://localhost:8000
```

`MEM0_WORKERS=1` is the safe local and dev default for this backend.

## Canonical mem0ai version

This backend currently treats `mem0ai==1.0.3` as the compatibility baseline for the local OpenCode memory stack.

Upgrade policy:

- Keep `mem0ai==1.0.3` pinned unless a dedicated compatibility task changes it.
- If the pin changes, update the exact version in `requirements.txt`, run the backend smoke paths and `pytest tests/ -v`, then update this README so it matches the new behavior.

## What `/search` does today

`POST /search` is the current public search endpoint. In `server.py` it delegates to `MemoryService.search(...)`, which in turn delegates to `RetrievalService.search(...)`.

As implemented today:

- the HTTP `/search` route performs semantic search through `memory_instance.search(...)`
- returned candidates are annotated with retrieval metadata showing `stage: semantic`, `source: memory_store`, and `strategy: semantic`
- filters are passed through when provided

Example shape from the existing tests:

```json
{
  "query": "stored",
  "params": {
    "user_id": "user-1",
    "filters": {"source": "chat"}
  },
  "results": [
    {
      "id": "memory-1",
      "_retrieval": {
        "stage": "semantic",
        "source": "memory_store",
        "strategy": "semantic"
      }
    }
  ]
}
```

## Current lexical retrieval behavior

`services/retrieval_service.py` also contains `lexical_search(...)`, but that helper is internal service-layer functionality today, not a documented public HTTP endpoint.

Its current behavior is intentionally narrow:

- it calls `memory_instance.get_all(...)` to fetch existing stored memories
- it scores matches by token overlap and exact substring boosts
- it can filter against record fields and `metadata`
- it annotates hits with `_retrieval.stage = lexical`, `_retrieval.source = memory_store`, and `_retrieval.strategy = keyword`
- it only searches the memory store already returned by mem0

This means the current lexical path is memory-store-only. It is not repo-file indexing, not file-system RAG, and not hybrid fusion or reranking.

## What `/retrieve` does today

`POST /retrieve` is now the canonical backend-owned hybrid retrieval endpoint.

As implemented today:

- lexical recall stays memory-store-only and scans `memory_instance.get_all(...)`
- semantic recall uses the existing `memory_instance.search(...)` seam
- candidates are fused in-process and keep truthful per-result retrieval metadata
- reranking is pluggable, with a simple deterministic in-process heuristic as the default
- degraded stage failures stay non-fatal when a safe partial result set exists

The retrieve response includes:

- ordered `results`
- `backend_capabilities.lexical`
- `backend_capabilities.semantic`
- `backend_capabilities.rerank`
- `backend_capabilities.anchors`
- `degraded` plus `degradation_reasons`
- `trace.request_id` and retrieval diagnostics

Current degradation behavior:

- semantic failure falls back to lexical-only results with `semantic=false`
- lexical failure falls back to semantic-only results with `lexical=false`
- rerank failure returns fused pre-rerank results with `rerank=false`

This remains memory-store-only retrieval. It does not introduce repo-file RAG, filesystem crawling, or an external reranker dependency.

## Identifiers and validation

The current service layer enforces identifier requirements in `services/memory_service.py`:

- `POST /memories` requires at least one of `user_id`, `agent_id`, or `run_id`
- `GET /memories` requires at least one identifier
- `DELETE /memories` requires at least one identifier

If no required identifier is provided, the backend raises a `400` response through the shared route error wrapper.

## Installation

```bash
git clone <repository-url>
cd mem0server
python -m venv mem
source mem/bin/activate
pip install -r requirements.txt
```

## Configuration

Configuration is driven by environment variables. The current README-approved defaults are:

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
MEM0_LLM_EXTRA_CONFIG='{"key":"value"}'

MEM0_EMBEDDER_PROVIDER=openai
MEM0_EMBEDDER_MODEL=text-embedding-3-small
```

OpenAI-backed configurations still require `OPENAI_API_KEY` when you are running against the real dependency stack.

## Running the server

Direct execution:

```bash
python server.py
```

Equivalent uvicorn invocation with the accepted defaults:

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1
```

With the default port mapping, the API and OpenAPI docs are available at:

- `http://localhost:8000`
- `http://localhost:8000/docs`

## Testing

The repository already includes a pytest harness in `tests/test_server.py`.

That test harness uses `fastapi.testclient.TestClient` and fake memory implementations to exercise the current backend without requiring live OpenAI calls or network access.

The existing tests cover:

- health and `/configure` smoke behavior
- CRUD, history, reset, and `/search` route delegation through the service layer
- lexical retrieval behavior in `RetrievalService.lexical_search(...)`
- `/retrieve` fused ranking behavior and truthful capability metadata
- degraded `/retrieve` behavior when semantic or rerank stages fail

Run it with:

```bash
pytest tests/ -v
```

## Minimal examples

Create a memory:

```bash
curl -X POST http://localhost:8000/memories \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "remember this"}],
    "user_id": "user-1",
    "metadata": {"source": "chat"}
  }'
```

Search memories through the current public endpoint:

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "stored",
    "user_id": "user-1",
    "filters": {"source": "chat"}
  }'
```

Health check:

```bash
curl http://localhost:8000/health
```

## What this README does not claim

To keep the docs aligned with the implemented backend, this README does not claim any of the following as current features:

- repo-file RAG or repository indexing
- a public lexical search endpoint
- any retrieval source beyond the existing memory store
- any reranker dependency outside the current in-process heuristic default

## m0grep CLI

`m0grep` is the native Rust CLI for the mem0server backend. It is the recommended client for indexing a local file corpus and searching across it (and the memory store). The source lives in `cli/` and the binary is named `m0grep`.

A legacy Python client (`mgrep_cli.py`) remains in the repository but `m0grep` is the canonical tool.

### Installation

Download the pre-built binary from the [GitHub Releases](https://github.com/your-org/mem0server/releases) page, then make it executable and place it on your `PATH`:

```bash
# example for macOS/Linux — adjust the filename for your platform
curl -L https://github.com/your-org/mem0server/releases/latest/download/m0grep-x86_64-apple-darwin \
  -o m0grep
chmod +x m0grep
mv m0grep /usr/local/bin/m0grep
```

Verify the installation:

```bash
m0grep --help
```

### Environment

`m0grep` reads the `MEM0_SERVER_URL` environment variable to determine the backend host. The default when the variable is unset is `http://localhost:8000`.

```bash
export MEM0_SERVER_URL=http://localhost:8000  # default — can be omitted
```

Every command also accepts `--url` to override the target host for that invocation.

### Commands

#### query — search the corpus

Search across all corpora (files + memory) with a keyword or phrase:

```bash
m0grep query "authentication middleware"
```

Restrict results to the file corpus only and filter by language:

```bash
m0grep query "token refresh" --corpus files --language-filter python
```

Limit results and narrow by path prefix:

```bash
m0grep query "database connection" --limit 5 --path-filter src/db
```

Get raw JSON output (useful for scripting or piping to `jq`):

```bash
m0grep query "retry logic" --raw
```

**Corpus values:** `all` (default), `files`, `memory`.  
**Limit range:** 1–50, default 10.

#### sync — index a directory

Walk a directory tree and push every file into the backend corpus:

```bash
m0grep sync /path/to/project
```

`sync` is a one-shot operation — run it after a bulk checkout or initial setup. For ongoing changes, use `watch` instead.

Pass `--generate-summaries` to have the server generate natural-language summaries for each indexed chunk. This is disabled by default to keep sync fast:

```bash
m0grep sync /path/to/project --generate-summaries
```

#### watch — watch for file changes

Start a background watcher that incrementally re-indexes files as they are saved:

```bash
m0grep watch /path/to/project
```

Enable chunk summaries for newly detected files (mirrors `sync --generate-summaries`):

```bash
m0grep watch /path/to/project --generate-summaries
```

Stop watching when you no longer need live re-indexing:

```bash
m0grep watch /path/to/project --stop
```

The watcher is useful during active development: edit a file, save it, and subsequent `query` calls reflect the change without a full `sync`.

#### status — check index state

Inspect how many documents are indexed and the current backend state:

```bash
m0grep status
```

The response shows counts per corpus and any active watcher information.

#### reset — wipe the index

Wipe all indexed data. Without `--yes`, the command prompts for interactive confirmation:

```bash
m0grep reset
```

Skip the confirmation prompt:

```bash
m0grep reset --yes
```

### Custom backend URL

All commands accept `--url` to target a non-default host or port:

```bash
m0grep status --url http://localhost:9000
m0grep sync /path/to/project --url http://remote-host:8000
m0grep query "search term" --url http://localhost:9000
```

### Server-side flags for sync and watch

#### generate_summaries

When `generate_summaries: true` is sent in a `POST /index/sync` or `POST /index/watch/start` request body, the indexing pipeline generates natural-language summaries for each indexed chunk. The flag is `false` by default to keep sync fast.

The `--generate-summaries` flag on the `m0grep sync` and `m0grep watch` commands maps to this request field.

#### USE_CHUNK_MEMORY (server env var)

The server reads the `USE_CHUNK_MEMORY` environment variable to gate chunk-level memory features. Accepted truthy values are `1`, `true`, and `yes` (case-insensitive). Any other value — including an unset variable — leaves the feature disabled.

```bash
export USE_CHUNK_MEMORY=true  # enable chunk-level memory on the server
```

This variable is evaluated server-side at startup; restarting the server is required for changes to take effect.

## MCP Integration (OpenCode)

The repository ships a JSON-RPC 2.0 MCP bridge that lets OpenCode agents search the file corpus and memory store without writing any HTTP calls directly.

### Architecture

```
OpenCode agent
    │  stdio (JSON-RPC 2.0)
    ▼
services/mcp_bridge.py   ← MCP bridge process
    │  HTTP
    ▼
server.py (FastAPI)      ← mem0 REST backend
```

### Start the backend

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1
```

The backend must be running before any MCP tool call is made.

### Start the MCP bridge

```bash
python3 -m services.mcp_bridge
```

The bridge reads JSON-RPC requests from stdin and writes responses to stdout. OpenCode manages this process automatically when the MCP server entry is present in `opencode.json`.

### Register in opencode.json

Add the following entry to the `"mcp"` section of your project or global `opencode.json`:

```json
{
  "mcp": {
    "mgrep": {
      "type": "local",
      "command": "python3",
      "args": ["-m", "services.mcp_bridge"],
      "environment": {
        "MEM0_SERVER_URL": "http://localhost:8000"
      }
    }
  }
}
```

`MEM0_SERVER_URL` defaults to `http://localhost:8000` when omitted. Set it explicitly if you run the backend on a non-default host or port.

### Available tools

#### `mgrep_query`

Search the memory store and/or file corpus with a natural-language or keyword query. Returns ranked hits.

```json
{
  "query": "how does retrieval ranking work",
  "corpora": ["file_corpus"],
  "limit": 10,
  "language_filter": "python"
}
```

- `query` — required. Natural-language or keyword query.
- `corpora` — optional. One or more of `"memory_store"`, `"file_corpus"`, `"all"`. Defaults to `["all"]`.
- `limit` — optional. 1–50 results. Defaults to 10.
- `path_filter` — optional. Prefix filter applied to file corpus results (e.g. `"services/"`).
- `language_filter` — optional. Language name (e.g. `"python"`, `"typescript"`).

#### `mgrep_sync`

Index a directory into the file corpus so it can be searched with `mgrep_query`.

```json
{
  "root": "/Users/javcab/mem0server"
}
```

- `root` — required. Absolute path to the directory to index.

#### `mgrep_status`

Return the current state of the file corpus index: indexed roots, file count, and chunk count.

```json
{}
```

No arguments required.

#### `mgrep_reset`

Wipe all indexed files and chunks from the file corpus. **This operation is irreversible.**

```json
{
  "confirm": true
}
```

- `confirm` — required. Must be `true` to proceed. The reset is a no-op when `false`.

### v1 scope

- File corpus indexes **code and Markdown files only**.
- Results are **ranked hits** — raw matching passages with relevance scores. The bridge does not synthesize, summarize, or answer questions.
- `mgrep_reset` is **destructive and irreversible**. The index must be rebuilt with `mgrep_sync` afterwards.
- The bridge does not expose the mem0 memory CRUD endpoints. Use the REST API directly for memory operations.

## License

Based on original source: https://code.m3ta.dev/m3tam3re/nixpkgs/src/branch/master/pkgs/mem0/server.py

## Support

For issues with the upstream mem0 library itself, see: https://github.com/mem0ai/mem0
