# Learnings

## Project Structure
- Python/FastAPI/Pydantic stack
- `api_models.py`: SearchRequest, RetrieveRequest, MemoryCreate (Pydantic models)
- `server.py`: FastAPI routes using `_execute_service_call` wrapper pattern
- `services/`: memory_service.py, retrieval_service.py, anchor_service.py, runtime.py, tracing.py
- `tests/test_server.py`: single test file using TestClient + fake backends

## Task 5: Code + Markdown Chunking (2026-05-18)

- `ast.iter_child_nodes(tree)` only iterates direct children of the module, giving top-level symbols. `ast.walk()` would go deeper and pick up nested functions/classes — use `iter_child_nodes` for top-level-only semantics.
- `node.end_lineno` is available on Python 3.8+ AST nodes; type-ignore needed since stubs don't declare it.
- ATX heading regex `^#{1,6} ` correctly ignores `#!` shebang lines and inline code since `re.MULTILINE` anchors `^` to line starts.
- The `rtk pytest` wrapper suppresses raw pytest output; use `python3 -m pytest` directly when full output is needed for evidence files.
- `keepends=True` on `splitlines` preserves \n in chunk content and keeps line counts accurate.

## Task 5 follow-up: Chunkers module + Go/JS/TS (2026-05-18)

- Binary search (`_offset_to_line`) is cleaner than linear scan for converting byte offsets to 1-based line numbers when line list is available.
- `ast.iter_child_nodes(tree)` for module gives top-level nodes; used consistently to avoid nested symbol bleed.
- Go regex `^func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(` handles both bare functions and methods (with receiver).
- JS/TS regex: three alternating groups — group(1) = named function, group(2) = arrow assigned to var, group(3) = class. `_js_symbol_kind` picks first non-None group.
- When adding language support, update existing tests that used the new language as a "fallback" stand-in — the test `test_fallback_chunker_activates_on_unsupported_language` used Go content, which became valid after Go support was added; switched to Rust.
- `cat >> file << 'EOF'` appends without heredoc interpretation issues when test methods have backtick content.

## Task 6 — IndexingService

- `FileScanner._fingerprint()` returns plain `hexdigest()` (no prefix). Don't compute your own sha256 — use the fingerprint from `scan()` result directly to avoid mismatch in diff detection.
- `scanner.diff(root, prev_fingerprints)` expects `{file_path: fingerprint}` where fingerprints must match scanner format exactly.
- `IndexManifestService._files` keys are `"{root}\x00{file_path}"` — access directly to build prev fingerprint map.
- Chunkers: pass `file_path, content` for MarkdownChunker; `file_path, content, language` for CodeChunker.
- Language detection: use `_EXT_TO_LANG` from `services.file_scanner` directly.

## Task 10 — Integration tests + fixture files (2026-05-18)

- `create_app(memory_factory=FakeMemory, startup_enabled=False)` creates all services; can replace `app.state.indexing_service`, `.query_service`, `.watch_service` after creation for targeted faking
- `IndexingService.status()` returns `roots` list with items using key `root_path` (not `root`)
- `FileCorpusService.query()` uses case-insensitive substring match; be careful that a term in a query might appear in multiple fixture files (e.g. architecture.md mentions "TokenParser")
- `node_modules/` is in `_IGNORED_DIRS` in `file_scanner.py` and is reliably excluded from sync
- For degraded-path tests, instantiate `QueryService` directly with `BrokenCorpus` + `StubRetrieval` and assign to `app.state.query_service` after `create_app()`
- Fixture files in `tests/fixtures/mgrep_repo/`: `src/parser.py` (Python class + functions), `docs/architecture.md` (Markdown headings), `node_modules/some_dep/index.js` (excluded by scanner)

## Task 11 — CLI entrypoint (mgrep_cli.py)

- Project has no local venv; dependencies installed into pyenv's Python. `typer` and `requests` are in requirements.txt and available after `pip install`.
- Used `typer` (already in requirements.txt) over `argparse` — richer help output with no extra deps.
- Typer command docstrings double as `--help` text — they ARE the public API documentation, not cosmetic comments.
- `--url ""` default trick: empty string sentinel lets `_base_url()` fall through to env var or hardcoded default, so explicit `--url` always wins over `MEM0_SERVER_URL`.
- `watch --stop` flag handles both start/stop on a single command vs. two separate subcommands — keeps command surface minimal.
- `reset` requires `--yes` or interactive confirm; sends `{"confirm": true}` per `IndexResetRequest` model validator requirement.

## Task 13: MCP Bridge (services/mcp_bridge.py)

- Implemented minimal JSON-RPC 2.0 over stdio without external MCP SDK
- `handle_request()` is the pure dispatch function; `run()` is the I/O loop — keeping them separate allows unit testing without subprocess overhead
- `httpx` was already in requirements.txt (as a transitive dep via openai/httpcore) — no new deps needed
- MCP notifications (no `id` field, e.g. `initialized`) must return `None` — caller must not write a response
- Tool schemas: `mgrep_status` takes no required args; `mgrep_reset` requires `confirm` in `required` array even though it has a default — ensures callers are explicit about destructive intent
- Test pattern: mock `httpx.post`/`httpx.get` at module level (`services.mcp_bridge.httpx.post`) rather than patching globally
- `run()` accepts injectable `stdin`/`stdout` for clean unit tests via `io.StringIO` — no subprocess needed for protocol tests

## Task 12 — CLI test coverage (test_cli.py)

- `typer.testing.CliRunner` works without any extra install; `typer` is already in requirements.
- `unittest.mock.patch("mgrep_cli.requests.post", ...)` patches at the module level where the name is used — this is the correct patch target (not `requests.post` globally).
- For `requests.exceptions.HTTPError` to behave like a real HTTP error, the mock response must have `.response` set; construct via `HTTPError(response=mock_resp)` and assign to `raise_for_status.side_effect`.
- `CliRunner.invoke(..., input="n\n")` simulates stdin for `typer.confirm` prompts — no extra mocking needed.
- Typer exit code for `raise typer.Exit(code=1)` maps to `result.exit_code == 1`; `raise typer.Exit(code=0)` maps to 0.
- Two inline comments were kept (non-obvious behavior: degraded backend fallback, confirm stdin behavior) — all other section separators and class docstrings were removed to keep code self-documenting.

## Task 15 — E2E Parity Harness

- **Routing CLI through TestClient**: patch `mgrep_cli.requests.post/get` with closures that call `client.post/get(urlparse(url).path, ...)` — returns a MagicMock shaped like a `requests.Response`.
- **Routing MCP through TestClient**: same pattern but patch `services.mcp_bridge.httpx.post/get`; the MagicMock must raise `httpx.HTTPStatusError` (not `requests.HTTPError`) on 4xx/5xx.
- **`monkeypatch.context()`** is essential when patching inside a `with TestClient(app) as client:` block — it ensures the patch is scoped to that inner block without interfering with the outer TestClient lifecycle.
- **Parity assertion strategy**: assert structural equivalence (same keys, same corpus labels, overlapping paths) rather than byte-for-byte equality — ordering and timestamps will naturally differ.
- Two fresh app instances are needed for the "compare counts" sync test because `_make_app` reloads the server module and creates a new in-memory store each time.

## Task — Query/Search Call-Chain Audit (2026-05-20)

- Runtime ingress for unified querying is `POST /query` only; both CLI and MCP bridge route to this endpoint (`cli/src/commands/query.rs` and `services/mcp_bridge.py`).
- `UnifiedQueryRequest.corpora` defaults to `["all"]`, and `_expand_corpora` resolves `"all"` to both `memory_store` and `file_corpus`, so both branches are attempted unless caller restricts corpora.
- Memory branch can be silently skipped in `/query`: route catches `get_memory_instance` failure and passes `memory_instance=None`; `QueryService._query_memory_store` then returns `[]` without backend calls.
- File corpus query terminates in in-memory chunk index (`FileCorpusService._chunks`) via `_lexical_query`; chunk records are populated by `IndexingService.sync/ingest -> FileCorpusService.upsert_chunks`.
- Summary-embedding semantic path for file corpus exists in `QueryService`/`FileCorpusService`, but server ingress does not pass `query_embedding`, so external `/query` requests currently run lexical-only on file corpus.
- Legacy memory-only endpoint `POST /search` is still exposed in API, but local CLI and MCP bridge do not call it.
