# Learnings

## [2026-05-19] Session Start
- Plan: `.sisyphus/plans/rust-cli-and-summary-indexing.md`
- Binary name: `m0grep`, crate at `cli/`
- Rust stack: clap + reqwest + serde + anyhow
- Python server routes: /query, /index/sync, /index/watch/start, /index/watch/stop, /index/status, /index/reset
- Watch = server-managed polling loop; CLI only sends start/stop HTTP calls
- Summary generation = per-request opt-in only
- Query-time summary retrieval gated by USE_CHUNK_MEMORY env var
- VERSION = trimmed semver without v prefix (e.g. 1.2.3)
- Release runners: ubuntu-latest, macos-13 (Intel), macos-14 (ARM)
- NO agent commits or pushes; developer only
- Default server URL: http://localhost:8000 or MEM0_SERVER_URL env var; 30s timeout

## Task 6 — Summary integration into IndexingService

- `IndexingService.__init__` accepts optional `memory_instance=None`; passed through to `SummaryService` lazily inside `sync()` when `generate_summaries=True`
- Summary service instantiated fresh per file (inside the file loop, once per `sync` call) to avoid stale references
- Empty/whitespace `summary_text` guarded with `if text and text.strip()` — matches requirement to treat whitespace as failure
- `WatchService._watchers` changed from `dict[str, tuple[Thread, Event]]` to `dict[str, tuple[Thread, Event, bool]]` to carry the `generate_summaries` flag per-root
- `_watch_loop` signature extended with `generate_summaries: bool = False` param — all existing watch tests still pass since they use positional thread args
- No changes to `server.py` needed for basic wiring; server can call `app.state.indexing_service._memory = app.state.memory` after startup if desired

## Task 5 – Summary-backed query retrieval (chunk_memory_enabled)

### Merging policy
Lexical-first: run lexical query, then run semantic (cosine similarity against `summary_embedding`) only when `chunk_memory_enabled=True` and `query_embedding` is provided. Merge by chunk ID; where a chunk qualifies under both, keep the higher score. Chunks without `summary_embedding` are skipped silently on the semantic path.

### Fallback contract
If `query_with_summaries` raises, `FileCorpusService.query()` logs a warning and returns the lexical result unchanged — no degradation flag set at corpus level.  Upper degradation (in `QueryService`) is only set if the entire `_query_file_corpus()` call raises.

### Threading the flag
`chunk_memory_enabled` flows: `server.py` (`is_chunk_memory_enabled()`) → `QueryService.query(chunk_memory_enabled=...)` → `QueryService._query_file_corpus(chunk_memory_enabled=...)` → `FileCorpusService.query(chunk_memory_enabled=...)`.  `query_embedding` is `None` at server level for now (future task will add embedding call).

### Baseline unchanged
When `chunk_memory_enabled=False` (default), `FileCorpusService.query()` returns exactly what `_lexical_query()` returns — no code-path difference.

### Test count
5 new tests added to `tests/test_query_api.py`; total suite 194 passed.

## release.yml workflow (2026-05-19)

- Use `gh release view vX.Y.Z --repo "$GITHUB_REPOSITORY"` to check existence; suppress stderr with `> /dev/null 2>&1` so the step always exits 0 and communicates via output variable.
- `macos-13` = Intel (x86_64-apple-darwin), `macos-14` = Apple Silicon (aarch64-apple-darwin) — always comment these in YAML since the numeric naming is non-obvious.
- `softprops/action-gh-release@v1` requires `permissions: contents: write` on the job.
- `actions/download-artifact@v4` places each artifact in a subdirectory named after the artifact; paths become `artifacts/<name>/<file>`.
- Matrix `if:` guard on the `build` job plus the same guard on `publish` is the correct pattern; the `publish` job guard is redundant but required because `needs: [check-release, build]` alone won't skip if `build` was skipped without an explicit condition.

## LICENSE file creation (Tue May 19 15:15:42 CEST 2026)
- Added canonical Apache-2.0 LICENSE file to repo root
- Fetched text directly from https://www.apache.org/licenses/LICENSE-2.0.txt
- Copyright: 2024 mem0server contributors
- File is 201 lines, passes grep -i 'apache' check


## Docker version propagation pattern (2026-05-19)
- `build.sh`: `VERSION=$(cat VERSION)` then pass as `--build-arg VERSION="${VERSION}"`
- `Dockerfile`: Declare `ARG VERSION=dev` before each FROM stage that needs it (ARGs don't cross stage boundaries); use `LABEL org.opencontainers.image.version="${VERSION}"` in the final stage
- Default `=dev` fallback in ARG ensures the image still builds without build.sh

## Task 15 — README update (m0grep CLI, generate_summaries, USE_CHUNK_MEMORY)

- Binary name is `m0grep` (crate: `cli/`); `mgrep_cli.py` is the legacy Python client kept for now (deleted in Task 16)
- `generate_summaries` bool field lives on both `IndexSyncRequest` and `WatchStartRequest` in `api_models.py`; defaults to `False`
- `USE_CHUNK_MEMORY` is read by `services/runtime.py::is_chunk_memory_enabled()` — truthy values: `"1"`, `"true"`, `"yes"` (case-insensitive)
- README section replaced wholesale under `## m0grep CLI` — old `mgrep_cli.py` examples are gone, replaced with `m0grep` binary examples
- All 194 Python tests still pass after doc-only change

## CLI file removal (2026-05-19)

When removing a Python CLI module (`mgrep_cli.py`), check not only `tests/test_cli.py` but also
parity/integration test files that may import the CLI inside individual test methods. In this
repo, `tests/test_e2e_parity.py` had 4 test methods (`test_cli_status_returns_expected_shape`,
`test_both_succeed_against_same_app`, `test_cli_sync_indexes_fixture_root`,
`test_http_and_cli_sync_produce_equivalent_counts`) and the `CliRunner` import that all depended
on `mgrep_cli`. Those were removed together with the source file to keep the test suite green.

Final count: 157 tests passing (down from 194 due to removed test_cli.py + 4 parity tests).
