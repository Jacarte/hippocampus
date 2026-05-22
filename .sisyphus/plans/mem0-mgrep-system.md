# Mem0 Personal mgrep System

## TL;DR

> Build an additive mgrep-like system on top of the current mem0server by adding a separate file/doc indexing pipeline, a unified cross-corpus query API, a thin CLI with HTTP parity, and an HTTP-backed MCP bridge for OpenCode.
>
> **Deliverables**:
> - New indexing and query HTTP endpoints for files/docs + memories
> - A Python CLI for query/sync/watch/status/reset
> - An HTTP-backed MCP bridge plus OpenCode configuration/docs
> - Regression-safe tests proving existing memory endpoints still behave the same
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 3 implementation waves + final verification
> **Critical Path**: T2 → T3/T4/T5 → T6 → T8 → T9 → T13/T14 → T15

---

## Context

### Original Request
Create a work plan to extend the current mem0server into a personal mgrep-like system with an API, a CLI tool, and OpenCode MCP configuration.

### Interview Summary
**Key Discussions**:
- Search target for v1 is **memories + files/docs**, not memories only.
- V1 should include **watch + query**, not query-only over pre-ingested content.
- Primary interfaces should have **HTTP + CLI parity**.
- Results in v1 should be **ranked hits only**; synthesized answers are explicitly deferred.
- The storage model should use **separate corpora** for memories and files/docs.
- V1 file scope is **code + Markdown/text docs**.
- MCP integration should be an **HTTP-backed bridge** for OpenCode.
- V1 should include an explicit **reset/drop operation** for the indexed file/doc corpus.

**Research Findings**:
- The current backend already supports semantic retrieval, lexical memory-store retrieval, hybrid fusion, reranking, degradation metadata, and provenance.
- The current backend does **not** support repo-file indexing, filesystem crawling, or a public lexical endpoint.
- External evidence supports a hybrid workflow: semantic search is best for discovery and onboarding, while grep/ripgrep remains best for exact literals and regex.
- For code files, the strongest chunking approach is **AST/symbol-aware chunking** with structured metadata and fallback text chunking only when parsing fails.

### Metis Review
**Identified Gaps** (addressed in this plan):
- Locked v1 file scope to code + Markdown so chunking/parser scope is bounded.
- Locked MCP transport to an HTTP-backed bridge so OpenCode integration has a concrete runtime model.
- Added explicit reset/drop support for the file/doc corpus to support development, recovery, and reindexing.
- Deferred synthesized answers from v1 to avoid model/config latency risk and keep the first system retrieval-first.
- Added regression-first work to preserve existing `/search`, `/retrieve`, and memory CRUD behavior.
- Added explicit guardrails for watch isolation, index excludes, corpus separation, and no implicit writes during query.

---

## Work Objectives

### Core Objective
Add a personal mgrep-like retrieval system to mem0server that can index and query code + Markdown files alongside existing memories, expose the capability via HTTP and CLI, and make it consumable from OpenCode through an HTTP-backed MCP bridge.

### Concrete Deliverables
- New request/response models for unified query and index lifecycle operations
- File/doc corpus persistence and indexing services with sync/watch/status/reset support
- Structure-aware code chunking and Markdown section chunking
- Unified query service that merges memory hits with file/doc hits while preserving provenance and degradation metadata
- FastAPI endpoints for query, sync, watch, status, reset, and capabilities
- A Python CLI with query, sync, watch, status, and reset commands
- An HTTP-backed MCP bridge exposing query/status/sync/reset tools to OpenCode
- OpenCode configuration documentation and example snippets
- Regression and end-to-end test coverage for API, CLI, and MCP parity

### Implementation File Map
- **Modify** `api_models.py` to add unified query + indexing lifecycle request/response models.
- **Modify** `server.py` to wire additive routes for query/sync/watch/status/reset/capabilities without altering existing route signatures.
- **Modify** `services/runtime.py` to extend runtime/config bootstrap for file-corpus settings and MCP bridge environment defaults where needed.
- **Modify** `README.md` to document new API/CLI/MCP flows and preserve current mem0 behavior descriptions.
- **Modify** `requirements.txt` to add only the minimal parser/watcher/CLI dependencies required for v1.
- **Create** `services/file_corpus_service.py` for separate file/doc corpus persistence and reset/drop behavior.
- **Create** `services/index_manifest_service.py` for indexed-root state, file fingerprints, and sync metadata.
- **Create** `services/file_scanner.py` for ignore-aware filesystem enumeration and change detection.
- **Create** `services/code_chunker.py` for AST/symbol-aware code chunking.
- **Create** `services/markdown_chunker.py` for heading/section-aware Markdown chunking.
- **Create** `services/indexing_service.py` for sync/status/reset orchestration over scan + chunk + persist.
- **Create** `services/watch_service.py` for watch start/stop lifecycle and incremental refresh.
- **Create** `services/query_service.py` for unified cross-corpus querying, normalization, fusion, and degradation reporting.
- **Create** `services/mcp_bridge.py` for the HTTP-backed MCP bridge used by OpenCode.
- **Create** `mgrep_cli.py` as the Python CLI entrypoint for query/sync/watch/status/reset.
- **Create** `tests/test_indexing.py` for scanner/chunker/sync/status/reset behavior.
- **Create** `tests/test_query_api.py` for additive query route coverage.
- **Create** `tests/test_cli.py` for CLI contract and failure-mode tests.
- **Create** `tests/test_mcp_bridge.py` for MCP tools/list and tool forwarding behavior.
- **Create** `tests/fixtures/mgrep_repo/` with code + Markdown fixtures for indexing/query parity tests.

### Definition of Done
- [ ] `pytest tests/ -v` passes with existing memory tests plus new indexing/query/CLI/MCP coverage
- [ ] `python -m mgrep_cli --help` exits 0 and lists query/sync/watch/status/reset commands
- [ ] `curl -s http://localhost:8000/query/capabilities` returns both `memory_store` and `file_corpus` capability data
- [ ] `curl -s -X POST http://localhost:8000/index/sync ...` indexes a fixture repo and `POST /query` returns file hits with line ranges and corpus provenance
- [ ] `python -m services.mcp_bridge` responds to MCP `tools/list` and exposes `mgrep_query`, `mgrep_sync`, `mgrep_status`, and `mgrep_reset`

### Must Have
- Preserve existing `/search`, `/retrieve`, and memory CRUD behavior without changing their public signatures
- Keep memory and file/doc corpora separate while exposing a unified query surface
- Respect ignore rules and safe defaults during indexing (`.git`, `node_modules`, hidden caches, large/binary files)
- Return truthful provenance, capability, and degradation metadata for unified queries
- Provide parity across HTTP API, CLI, and MCP bridge for core read/query workflows

### Must NOT Have (Guardrails)
- Must NOT replace or silently alter current memory endpoint semantics
- Must NOT index PDFs, images, audio, or video in v1
- Must NOT trigger indexing writes as a side effect of plain query requests
- Must NOT block the FastAPI request loop on long-running watch/sync operations without lifecycle control
- Must NOT require OpenCode to talk directly to the FastAPI server without the MCP bridge layer
- Must NOT add synthesized answer mode to v1 scope
- Must NOT commit or push changes unless the user explicitly commands it

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — all verification is agent-executed through tests, curl, CLI commands, or MCP protocol exchanges.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: Tests-after
- **Framework**: pytest
- **Agent-Executed QA**: REQUIRED for every task and final verification wave

### QA Policy
Evidence saved to `.sisyphus/evidence/` using task-specific filenames.

- **Backend/API**: Bash + `curl` against local FastAPI server
- **CLI**: Bash running `python -m mgrep_cli ...`
- **MCP bridge**: Bash or Python subprocesses sending JSON-RPC over stdio to `python -m services.mcp_bridge`
- **Service modules**: `pytest` focused test files with fake backends and fixture repos

### Defaults Applied
- V1 covers **code + Markdown/text docs** only
- V1 defers synthesized answer generation; only ranked hits/snippets are planned
- MCP bridge is **HTTP-backed** and uses the backend at `MEM0_SERVER_URL` / `http://localhost:8000`
- Reset/drop support is included for the file/doc corpus

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — contracts + indexing foundations):
├── Task 1: Lock current API contracts with regression tests [quick]
├── Task 2: Add unified query/index API models [quick]
├── Task 3: Add file corpus persistence + manifest config [unspecified-high]
├── Task 4: Add file scanning/ignore/fingerprint pipeline [quick]
└── Task 5: Add code + Markdown chunking services [unspecified-high]

Wave 2 (After Wave 1 — core backend capabilities):
├── Task 6: Add sync/status/reset indexing orchestration [deep]
├── Task 7: Add unified cross-corpus query service [deep]
├── Task 8: Add watch lifecycle service [unspecified-high]
├── Task 9: Wire new FastAPI routes and request handling [quick]
└── Task 10: Add backend API integration/regression coverage [unspecified-high]

Wave 3 (After Wave 2 — user-facing clients + integration):
├── Task 11: Add CLI HTTP client and core commands [quick]
├── Task 12: Add CLI test coverage and smoke fixtures [quick]
├── Task 13: Add HTTP-backed MCP bridge and tool schemas [unspecified-high]
├── Task 14: Add OpenCode MCP config docs + examples [writing]
└── Task 15: Add end-to-end API/CLI/MCP parity harness [deep]

Wave FINAL (After ALL tasks — 4 parallel reviews):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real runtime QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay

Critical Path: T2 → T3/T4/T5 → T6 → T8 → T9 → T13/T14 → T15
Parallel Speedup: ~60–70% faster than sequential
Max Concurrent: 5
```

### Dependency Matrix

- **1**: — — 10, FINAL, 1
- **2**: — — 6, 9, 10, 1
- **3**: — — 6, 7, 1
- **4**: — — 6, 8, 1
- **5**: — — 6, 7, 1
- **6**: 2, 3, 4, 5 — 8, 9, 10, 2
- **7**: 3, 5 — 9, 10, 13, 2
- **8**: 4, 6 — 9, 10, 2
- **9**: 2, 6, 7, 8 — 10, 11, 13, 2
- **10**: 1, 2, 6, 7, 8, 9 — 15, FINAL, 2
- **11**: 9 — 12, 14, 15, 3
- **12**: 11 — 15, FINAL, 3
- **13**: 7, 9 — 14, 15, FINAL, 3
- **14**: 11, 13 — 15, FINAL, 3
- **15**: 10, 12, 13, 14 — FINAL, 3

### Agent Dispatch Summary

- **Wave 1**: **5** — T1 `quick`, T2 `quick`, T3 `unspecified-high`, T4 `quick`, T5 `unspecified-high`
- **Wave 2**: **5** — T6 `deep`, T7 `deep`, T8 `unspecified-high`, T9 `quick`, T10 `unspecified-high`
- **Wave 3**: **5** — T11 `quick`, T12 `quick`, T13 `unspecified-high`, T14 `writing`, T15 `deep`
- **FINAL**: **4** — F1 `oracle`, F2 `unspecified-high`, F3 `unspecified-high`, F4 `deep`

---

## TODOs

> Implementation + verification = one task. Every task includes explicit references, parallelization, acceptance criteria, and agent-executed QA scenarios.

- [x] 1. Lock current memory endpoint behavior with regression baselines

  **What to do**:
  - Add regression tests that snapshot or assert the current HTTP behavior of `/search`, `/retrieve`, and memory CRUD endpoints before any new feature work lands.
  - Add focused assertions for retrieval metadata (`_retrieval`, `trace.retrieval`, capability flags, degradation reasons) so later cross-corpus work cannot accidentally alter them.
  - Ensure these tests can run with fake memory backends exactly like the existing harness.

  **Must NOT do**:
  - Do not change current endpoint signatures or response semantics to make the tests easier.
  - Do not rewrite existing test style away from `pytest` + `TestClient` + fake backends.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: This is a focused, test-only baseline task touching a small number of files.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `test-driven-development`: existing work is planning for tests-after, not a fresh TDD implementation flow.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4, 5)
  - **Blocks**: Task 10, Final verification
  - **Blocked By**: None

  **References**:
  - `README.md` - Documents the current public contract for `/search` and `/retrieve`, including semantic-only `/search` and hybrid memory-only `/retrieve` behavior.
  - `server.py` - Shows exactly which routes exist today and must remain additive-safe.
  - `tests/test_server.py:test_crud_search_history_routes_delegate_through_service_layer` - Existing CRUD and `/search` expectations to preserve.
  - `tests/test_server.py:test_search_propagates_correlation_id_and_structured_retrieval_trace` - Existing trace and correlation-id behavior that must not regress.
  - `tests/test_server.py:test_retrieve_returns_fused_ranked_results_with_capabilities` - Existing hybrid retrieval payload contract.
  - `tests/test_server.py:test_retrieve_degrades_to_lexical_results_when_semantic_fails` - Existing degraded fallback contract.

  **Acceptance Criteria**:
  - [ ] Regression tests for current `/search`, `/retrieve`, and CRUD routes exist.
  - [ ] `pytest tests/ -v -k "search or retrieve or crud"` passes with no response-contract regressions.
  - [ ] New tests explicitly assert existing `_retrieval`, `trace.retrieval`, and degradation fields.

  **QA Scenarios**:
  ```
  Scenario: Existing search and retrieve contracts remain stable
    Tool: Bash (pytest)
    Preconditions: Virtualenv/deps installed; no live OpenAI dependency needed
    Steps:
      1. Run `pytest tests/test_server.py -k "search or retrieve or crud" -v`
      2. Confirm tests covering `/search`, `/retrieve`, and CRUD all pass
      3. Inspect pytest output for zero failures and zero unexpected skips
    Expected Result: All baseline contract tests pass
    Failure Indicators: Any assertion diff in response fields, status codes, or retrieval metadata
    Evidence: .sisyphus/evidence/task-1-regression-pytest.txt

  Scenario: Search trace metadata still includes correlation + stage timings
    Tool: Bash (pytest)
    Preconditions: Same as above
    Steps:
      1. Run `pytest tests/test_server.py -k "structured_retrieval_trace" -v`
      2. Confirm `trace.request_id` and `trace.retrieval.latency_ms` assertions pass
    Expected Result: Correlation-id and stage timing structure remains intact
    Failure Indicators: Missing `trace` block, changed keys, or missing latency fields
    Evidence: .sisyphus/evidence/task-1-trace-metadata.txt
  ```

  **Commit**: NO

- [x] 2. Add unified query and index lifecycle API models

  **What to do**:
  - Extend the request/response modeling layer with Pydantic models for unified cross-corpus query, sync, watch, status, reset, and capabilities payloads.
  - Model corpus selection explicitly (`memory_store`, `file_corpus`, or both), plus v1-safe filters for path, language, scope, limits, and watch/sync targets.
  - Keep synthesized answer fields out of v1 request/response models.

  **Must NOT do**:
  - Do not mutate the existing `SearchRequest`, `RetrieveRequest`, or memory CRUD models in a breaking way.
  - Do not introduce placeholder fields for future PDF or answer-generation work.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: This is a focused contract-definition task centered on one models file.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `typescript-reviewer`: repo is Python/Pydantic, not TypeScript.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 6, 9, 10
  - **Blocked By**: None

  **References**:
  - `api_models.py` - Current location for request models and current style to extend.
  - `server.py` - Shows how request models are bound to route handlers today.
  - `README.md` - Documents current endpoint contract and can guide naming continuity.
  - `.sisyphus/drafts/mem0-mgrep.md` - Captures confirmed scope decisions: separate corpora, code+Markdown, no synthesized answers in v1.

  **Acceptance Criteria**:
  - [ ] New Pydantic request/response models exist for query/sync/watch/status/reset/capabilities.
  - [ ] Existing models remain backward-compatible.
  - [ ] Model validation rejects empty corpora requests and invalid limits/targets.

  **QA Scenarios**:
  ```
  Scenario: New API models validate expected v1 payloads
    Tool: Bash (pytest)
    Preconditions: New model tests added
    Steps:
      1. Run `pytest tests/ -k "api_models or query models or index models" -v`
      2. Confirm valid query/sync/watch/reset payloads instantiate successfully
      3. Confirm invalid corpus/limit combinations raise validation errors
    Expected Result: Model tests pass for valid and invalid payloads
    Failure Indicators: Validation accepts malformed corpus input or rejects confirmed v1 shapes
    Evidence: .sisyphus/evidence/task-2-model-validation.txt

  Scenario: Existing request models still accept current payloads
    Tool: Bash (pytest)
    Preconditions: Regression tests from Task 1 present
    Steps:
      1. Run `pytest tests/test_server.py -k "crud or search or retrieve" -v`
      2. Confirm no failures caused by model signature changes
    Expected Result: Existing route tests still pass unchanged
    Failure Indicators: 422 validation changes or broken request parsing for existing endpoints
    Evidence: .sisyphus/evidence/task-2-backward-compat.txt
  ```

  **Commit**: NO

- [x] 3. Add file corpus persistence and manifest/state handling

  **What to do**:
  - Introduce a separate persistence layer for the file/doc corpus with its own collection/namespace, chunk identifiers, and manifest/state tracking.
  - Track indexed roots, per-file fingerprints, chunk ids, last sync timestamps, and reset/drop bookkeeping without touching the existing memory collection.
  - Define storage boundaries clearly so memory resets and file-corpus resets can be controlled independently.

  **Must NOT do**:
  - Do not store file/doc chunks in the same logical collection as memories.
  - Do not couple file-corpus lifecycle operations to memory CRUD/reset behavior.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: This introduces a new persistence boundary and manifest model with correctness risk.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `systematic-debugging`: not a bugfix task yet.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 6, 7
  - **Blocked By**: None

  **References**:
  - `services/runtime.py` - Current config and memory initialization seam to extend with separate file-corpus config.
  - `README.md` - Documents current single memory-store retrieval limitation and current config defaults.
  - `services/anchor_service.py` - Existing metadata normalization patterns useful for chunk provenance metadata.
  - `.env.example` / runtime env conventions - Existing environment-driven configuration style to mirror.

  **Acceptance Criteria**:
  - [ ] File/doc corpus storage uses a separate namespace/collection from memories.
  - [ ] Manifest/state can represent indexed roots, file fingerprints, and last-sync metadata.
  - [ ] Reset/drop can target file/doc corpus state without affecting memory data.

  **QA Scenarios**:
  ```
  Scenario: File corpus state is isolated from memory corpus state
    Tool: Bash (pytest)
    Preconditions: New persistence tests with fake backends or temp stores
    Steps:
      1. Run `pytest tests/ -k "file corpus or manifest or reset isolation" -v`
      2. Seed memory data and file-corpus data in the test fixture
      3. Trigger file-corpus reset and assert memory records remain available
    Expected Result: File reset clears only file/doc state
    Failure Indicators: Memory records disappear or shared state bleeds across corpora
    Evidence: .sisyphus/evidence/task-3-corpus-isolation.txt

  Scenario: Manifest tracks file fingerprints and sync state
    Tool: Bash (pytest)
    Preconditions: Temp fixture repo exists in tests
    Steps:
      1. Run manifest-focused tests
      2. Assert file fingerprint, root id, and last-sync metadata are recorded
    Expected Result: Manifest/state entries exist for indexed files and roots
    Failure Indicators: Missing fingerprints, unstable ids, or no sync timestamps
    Evidence: .sisyphus/evidence/task-3-manifest-state.txt
  ```

  **Commit**: NO

- [x] 4. Add file scanning, ignore rules, and fingerprint pipeline

  **What to do**:
  - Implement repository/root scanning for v1-supported file types (code + Markdown/text docs).
  - Respect safe defaults for ignore rules (`.git`, `node_modules`, caches, hidden/generated directories) and support `.gitignore`-aligned behavior where feasible.
  - Compute stable file fingerprints and classify create/update/delete events for sync/watch orchestration.

  **Must NOT do**:
  - Do not index binary files, oversized files, or unsupported file types.
  - Do not follow symlink loops or index dangerous/system directories by default.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Focused filesystem traversal and filtering logic with bounded scope.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `using-git-worktrees`: execution concern, not planning concern.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 6, 8
  - **Blocked By**: None

  **References**:
  - `README.md` - Confirms current backend has no repo-file indexing and therefore needs a new scan path.
  - `.gitignore` - Existing project ignore conventions to respect when designing defaults.
  - Metis review notes - Explicitly call out hidden dirs, symlinks, binaries, and max file size as v1 execution risks.

  **Acceptance Criteria**:
  - [ ] Scanner enumerates only supported file types under allowed roots.
  - [ ] Ignore defaults exclude `.git`, `node_modules`, cache directories, binary/empty files, and oversize files.
  - [ ] Fingerprinting detects create/update/delete changes deterministically.

  **QA Scenarios**:
  ```
  Scenario: Scanner respects ignore rules and supported file filters
    Tool: Bash (pytest)
    Preconditions: Fixture repo includes code, markdown, ignored dirs, binaries, and oversized files
    Steps:
      1. Run `pytest tests/ -k "scanner or ignore or fingerprint" -v`
      2. Assert only supported code + markdown files are returned
      3. Assert `.git`, `node_modules`, binary, empty, and oversize files are skipped
    Expected Result: Scan output contains only intended v1 files
    Failure Indicators: Ignored or unsupported files appear in scan results
    Evidence: .sisyphus/evidence/task-4-scan-filters.txt

  Scenario: Fingerprints detect edits and deletions
    Tool: Bash (pytest)
    Preconditions: Temp fixture repo with mutable files
    Steps:
      1. Scan a root and capture baseline fingerprint state
      2. Modify one file and delete another inside the fixture
      3. Re-run scanner logic in test and assert one update + one delete event are detected
    Expected Result: Change classification is deterministic
    Failure Indicators: No change detected, false positives, or unstable hashes across identical content
    Evidence: .sisyphus/evidence/task-4-fingerprint-diff.txt
  ```

  **Commit**: NO

- [x] 5. Add code and Markdown chunking services

  **What to do**:
  - Add structure-aware chunking for code files using AST/symbol boundaries where parsing succeeds.
  - Add heading/section-aware chunking for Markdown/text docs.
  - Add fallback bounded text chunking only for unsupported or failed code parses, while preserving path, language, line ranges, symbol/signature, and parent-scope metadata.

  **Must NOT do**:
  - Do not use naive fixed-size line windows as the primary code chunk representation.
  - Do not drop provenance metadata needed for precise result explanations.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Parser/chunker correctness determines retrieval quality and result explainability.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `playwright-reviewer`: unrelated domain.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 6, 7
  - **Blocked By**: None

  **References**:
  - Brainstorming decision in `.sisyphus/drafts/mem0-mgrep.md` - Code chunking should be AST/symbol-aware, not raw AST storage.
  - External chunking research summary - AST/symbol-aware chunking is the preferred default; fallback text chunking only when parsing fails.
  - Metis review - File type scope is code + Markdown only in v1, so parser/chunker support can stay bounded.

  **Acceptance Criteria**:
  - [ ] Code chunker emits symbol-aware chunks with file path, language, symbol name/kind, scope/signature, and line-range metadata.
  - [ ] Markdown chunker emits heading/section-aware chunks with stable boundaries.
  - [ ] Fallback chunker activates only when parsing fails or language is unsupported.

  **QA Scenarios**:
  ```
  Scenario: Code chunker preserves semantic boundaries
    Tool: Bash (pytest)
    Preconditions: Fixture code files with functions, classes, and methods exist
    Steps:
      1. Run `pytest tests/ -k "chunker and code" -v`
      2. Assert emitted chunks align to symbol boundaries rather than arbitrary mid-function splits
      3. Assert chunk metadata includes file path, symbol kind/name, and start/end lines
    Expected Result: Structure-aware chunks are produced for code fixtures
    Failure Indicators: Mid-symbol splits, missing metadata, or empty content chunks
    Evidence: .sisyphus/evidence/task-5-code-chunking.txt

  Scenario: Markdown chunker and fallback chunker behave correctly
    Tool: Bash (pytest)
    Preconditions: Markdown fixtures + one intentionally unparseable code fixture
    Steps:
      1. Run markdown/fallback chunk tests
      2. Assert markdown chunks align to headings/sections
      3. Assert unparseable code file falls back to bounded text chunking with preserved path metadata
    Expected Result: Markdown and fallback chunking both succeed predictably
    Failure Indicators: Flat unsectioned markdown output or hard failure on parse errors
    Evidence: .sisyphus/evidence/task-5-markdown-fallback.txt
  ```

  **Commit**: NO

- [x] 6. Add sync, status, and reset indexing orchestration

  **What to do**:
  - Build the orchestration layer that connects scanning, chunking, manifest updates, and file-corpus persistence.
  - Implement one-shot sync, current corpus status reporting, and explicit reset/drop behavior for the file/doc corpus.
  - Make indexing lifecycle responses include safe, truthful counts and timestamps.

  **Must NOT do**:
  - Do not let reset/drop affect memory corpus state.
  - Do not leave manifest/file-corpus state half-updated without explicit degraded/error reporting.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: This stitches multiple new subsystems together and must maintain consistency under failure.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `systematic-debugging`: better suited if implementation later reveals failures.

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential within Wave 2
  - **Blocks**: Tasks 8, 9, 10
  - **Blocked By**: Tasks 2, 3, 4, 5

  **References**:
  - `services/memory_service.py` - Example orchestration style for memory operations and route-to-service delegation.
  - `services/runtime.py` - Existing config/bootstrap seam to extend for file-corpus lifecycle configuration.
  - Metis review - Explicitly requires reset/drop and warns about partial index state and delete/prune semantics.

  **Acceptance Criteria**:
  - [ ] Sync can index a fixture root end-to-end and persist manifest/chunk state.
  - [ ] Status reports indexed roots, file/chunk counts, and last-sync timestamps.
  - [ ] Reset/drop clears file-corpus state cleanly and independently.

  **QA Scenarios**:
  ```
  Scenario: Sync indexes a fixture repo and status reports it
    Tool: Bash (pytest)
    Preconditions: Fixture repo exists in tests
    Steps:
      1. Run `pytest tests/ -k "sync and status" -v`
      2. Assert sync indexes code + markdown fixture files
      3. Assert status shows root count, file count, and non-null last sync time
    Expected Result: Sync and status lifecycle both work end-to-end in tests
    Failure Indicators: Zero indexed files, missing timestamps, or stale manifest state after sync
    Evidence: .sisyphus/evidence/task-6-sync-status.txt

  Scenario: Reset/drop clears only file/doc corpus state
    Tool: Bash (pytest)
    Preconditions: Indexed fixture root exists and memory fixture data is present
    Steps:
      1. Run reset-focused tests after seeding both corpora
      2. Assert file/doc corpus is empty after reset
      3. Assert memory corpus data remains queryable
    Expected Result: Reset isolates file/doc corpus only
    Failure Indicators: Memory data loss or dangling manifest entries after reset
    Evidence: .sisyphus/evidence/task-6-reset-isolation.txt
  ```

  **Commit**: NO

- [x] 7. Add unified cross-corpus query service

  **What to do**:
  - Add a service that queries memory-store retrieval and file/doc corpus retrieval independently, normalizes results to a shared hit shape, and fuses them into one ranked response.
  - Preserve truthful provenance and degradation/capability reporting per corpus/stage.
  - Support corpus selection (`memory_store`, `file_corpus`, `all`) and v1 filters such as path/language/scope.

  **Must NOT do**:
  - Do not mutate the existing `RetrievalService` contract in a way that breaks memory-only behavior.
  - Do not hide partial failures; report degraded results explicitly.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Ranking/fusion/provenance across separate corpora is the central logic of the feature.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `ultrabrain`: problem is substantial but still conventional and bounded.

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential within Wave 2
  - **Blocks**: Tasks 9, 10, 13
  - **Blocked By**: Tasks 3, 5

  **References**:
  - `services/retrieval_service.py` - Current lexical/semantic fusion, reranking, and degradation patterns to preserve conceptually for the memory side.
  - `services/anchor_service.py` - Existing provenance normalization patterns to adapt for file/doc hits.
  - `README.md` - Current `/retrieve` capability/degradation contract, which the unified query should emulate rather than contradict.
  - `.sisyphus/drafts/mem0-mgrep.md` - Confirms separate corpora and no synthesized answers in v1.

  **Acceptance Criteria**:
  - [ ] Unified query can return memory-only, file-only, and fused cross-corpus results.
  - [ ] Results include corpus provenance and score/trace metadata.
  - [ ] Partial corpus failures produce degraded but non-fatal responses when safe results exist.

  **QA Scenarios**:
  ```
  Scenario: Unified query fuses memory and file results
    Tool: Bash (pytest)
    Preconditions: Test fixtures seed both memory and file/doc corpora
    Steps:
      1. Run `pytest tests/ -k "unified query or cross corpus" -v`
      2. Assert one query can return hits from both corpora
      3. Assert each result identifies its corpus/source and ranking metadata
    Expected Result: A fused ranked result set is returned with provenance
    Failure Indicators: Results collapse into one corpus, lose provenance, or rank inconsistently
    Evidence: .sisyphus/evidence/task-7-cross-corpus.txt

  Scenario: Unified query degrades gracefully when file corpus is unavailable
    Tool: Bash (pytest)
    Preconditions: Test doubles can force file-corpus failure while memory retrieval still works
    Steps:
      1. Trigger a query with file-corpus failure injected
      2. Assert memory hits are still returned
      3. Assert response marks degraded=true with file-corpus degradation reason
    Expected Result: Non-fatal degraded response is returned
    Failure Indicators: Whole query fails or degraded metadata is missing
    Evidence: .sisyphus/evidence/task-7-degraded-query.txt
  ```

  **Commit**: NO

- [x] 8. Add watch lifecycle service for indexed roots

  **What to do**:
  - Implement background watch/start/stop orchestration for indexed roots using the scanning/fingerprint pipeline.
  - Ensure file changes trigger safe incremental resync behavior and delete/prune handling.
  - Expose watch state so status can report whether a root is actively watched.

  **Must NOT do**:
  - Do not block request handlers while watch loops run.
  - Do not leave orphaned watchers after stop/reset operations.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Background process lifecycle and filesystem events are operationally fragile.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `systematic-debugging`: reserve for actual watcher failures during execution.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 9, 10 after Task 6 unblocks it)
  - **Blocks**: Tasks 9, 10
  - **Blocked By**: Tasks 4, 6

  **References**:
  - Metis review - Watch mode must run outside the request loop and handle symlinks, deletes, and partial state safely.
  - `.sisyphus/drafts/mem0-mgrep.md` - Confirms watch + query is required in v1.

  **Acceptance Criteria**:
  - [ ] Watch can start and stop for a fixture root.
  - [ ] File edits and deletes are reflected in subsequent query results within a bounded window.
  - [ ] Status can report active watchers.

  **QA Scenarios**:
  ```
  Scenario: Watch start/stop lifecycle works
    Tool: Bash (pytest)
    Preconditions: Watch service tests with temp fixture repo
    Steps:
      1. Run `pytest tests/ -k "watch lifecycle" -v`
      2. Start watching a temp root in the test
      3. Stop the watcher and assert it fully unregisters
    Expected Result: Watchers start and stop cleanly without leaked state
    Failure Indicators: Orphaned watcher state or inability to stop a watcher
    Evidence: .sisyphus/evidence/task-8-watch-lifecycle.txt

  Scenario: File edits propagate to subsequent query state
    Tool: Bash (pytest)
    Preconditions: Temp watched root with one queryable file
    Steps:
      1. Start watch in the test fixture
      2. Modify the file content to include a new unique token
      3. Query after the watch cycle and assert the new token is returned; delete the file and assert results disappear
    Expected Result: Watch updates and deletes are reflected in indexed state
    Failure Indicators: Stale results remain after change/delete or updates never appear
    Evidence: .sisyphus/evidence/task-8-watch-propagation.txt
  ```

  **Commit**: NO

- [x] 9. Wire new FastAPI routes and request handling

  **What to do**:
  - Add additive HTTP routes for unified query, sync, watch start/stop, status, reset, and capabilities.
  - Route these handlers through dedicated services rather than overloading current memory handlers.
  - Keep correlation-id propagation, structured tracing, and HTTP error mapping consistent with existing backend behavior.

  **Must NOT do**:
  - Do not change the signatures or semantics of `/search`, `/retrieve`, `/memories`, or `/reset`.
  - Do not bypass the service layer with route-local business logic.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: The route layer should stay thin and mostly wire already-built services.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `verification-before-completion`: execution-stage verification, not planning.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: Tasks 10, 11, 13
  - **Blocked By**: Tasks 2, 6, 7, 8

  **References**:
  - `server.py` - Current thin-route style and error wrapper `_execute_service_call` to follow.
  - `services/tracing.py` - Existing request correlation and structured trace conventions to preserve.
  - `services/memory_service.py` - Example of delegating route logic into a dedicated service.

  **Acceptance Criteria**:
  - [ ] New routes exist for query, sync, watch start/stop, status, reset, and capabilities.
  - [ ] Existing routes remain unchanged.
  - [ ] New routes emit correlation-id headers and structured trace events.

  **QA Scenarios**:
  ```
  Scenario: New routes respond with expected HTTP contracts
    Tool: Bash (pytest)
    Preconditions: Route tests added with fake services/backends
    Steps:
      1. Run `pytest tests/test_server.py -k "query or sync or watch or status or capabilities" -v`
      2. Confirm 200/4xx behavior matches the new models and route contracts
      3. Confirm correlation-id propagation behaves like existing routes
    Expected Result: Additive routes behave consistently with the existing FastAPI layer
    Failure Indicators: Missing routes, broken status codes, or missing request IDs
    Evidence: .sisyphus/evidence/task-9-route-contracts.txt

  Scenario: Existing memory routes still behave unchanged
    Tool: Bash (pytest)
    Preconditions: Task 1 regression tests present
    Steps:
      1. Run `pytest tests/test_server.py -k "crud or search or retrieve" -v`
      2. Confirm all previous assertions still pass after route additions
    Expected Result: No regression to current memory surfaces
    Failure Indicators: Existing route failures or altered response payloads
    Evidence: .sisyphus/evidence/task-9-regression-routes.txt
  ```

  **Commit**: NO

- [x] 10. Add backend API integration and regression coverage

  **What to do**:
  - Add tests covering the new API routes, sync/query behavior, status/watch/reset responses, and degraded cross-corpus cases.
  - Extend existing regression harness to prove the additive surface coexists safely with current memory behavior.
  - Add fixture repos/files that exercise code + Markdown indexing and path/language filters.

  **Must NOT do**:
  - Do not rely on live external services for core tests.
  - Do not leave critical query/index flows untested behind only CLI or MCP tests.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: This is broad verification over multiple new backend features and regressions.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `playwright-reviewer`: no browser/UI testing required.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 15, Final verification
  - **Blocked By**: Tasks 1, 2, 6, 7, 8, 9

  **References**:
  - `tests/test_server.py` - Existing test conventions and fake backend pattern to extend.
  - `README.md` - Current contract descriptions to preserve in regression coverage.

  **Acceptance Criteria**:
  - [ ] New backend tests cover sync, query, watch, status, reset, capabilities, and degraded cross-corpus responses.
  - [ ] Existing regression suite still passes.
  - [ ] Fixture data demonstrates code + Markdown indexing behavior.

  **QA Scenarios**:
  ```
  Scenario: Full backend API test suite passes
    Tool: Bash (pytest)
    Preconditions: New and existing backend tests present
    Steps:
      1. Run `pytest tests/ -v`
      2. Confirm all backend API tests and regressions pass
      3. Save the full output log
    Expected Result: Entire backend test suite passes
    Failure Indicators: Any failing sync/query/watch/reset/capabilities or regression tests
    Evidence: .sisyphus/evidence/task-10-full-backend-suite.txt

  Scenario: Fixture repo produces code and markdown hits via API tests
    Tool: Bash (pytest)
    Preconditions: Fixture repo seeded in tests
    Steps:
      1. Run fixture-focused query tests
      2. Assert one code hit and one markdown hit are returned for targeted queries
    Expected Result: Both supported v1 file types are queryable
    Failure Indicators: One supported type never appears in results
    Evidence: .sisyphus/evidence/task-10-fixture-types.txt
  ```

  **Commit**: NO

- [x] 11. Add CLI HTTP client and core commands

  **What to do**:
  - Add a Python CLI entrypoint and command structure for `query`, `sync`, `watch`, `status`, and `reset`.
  - Implement the CLI as a thin HTTP client over the new backend endpoints so HTTP remains the source of truth.
  - Provide flags for corpus selection, limits, root paths, and output modes appropriate for v1.

  **Must NOT do**:
  - Do not implement separate business logic in the CLI that diverges from backend semantics.
  - Do not make `--help` depend on a running backend.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Thin client/wrapper over the HTTP API with a contained surface.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `caveman`: not relevant to the task itself.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: Tasks 12, 14, 15
  - **Blocked By**: Task 9

  **References**:
  - `README.md` - Existing curl examples and backend URL defaults to mirror in CLI docs/help.
  - `server.py` - Source of truth for HTTP route behavior.
  - `.sisyphus/drafts/mem0-mgrep.md` - Confirms HTTP + CLI parity requirement.

  **Acceptance Criteria**:
  - [ ] CLI supports `query`, `sync`, `watch`, `status`, and `reset`.
  - [ ] CLI defaults to the configured backend URL and supports override flags/env var.
  - [ ] CLI `--help` works without a running backend.

  **QA Scenarios**:
  ```
  Scenario: CLI help and command parsing work without backend access
    Tool: Bash
    Preconditions: CLI entrypoint installed/importable
    Steps:
      1. Run `python -m mgrep_cli --help`
      2. Run `python -m mgrep_cli query --help`
      3. Run `python -m mgrep_cli sync --help`
    Expected Result: Help text renders and exits 0 for root + subcommands
    Failure Indicators: Help requires backend connectivity or crashes
    Evidence: .sisyphus/evidence/task-11-cli-help.txt

  Scenario: CLI query/sync/status/reset call the backend correctly
    Tool: Bash (pytest or local server + CLI)
    Preconditions: Backend routes available in tests or local fixture server
    Steps:
      1. Run CLI smoke tests against a fixture backend
      2. Invoke query, sync, status, and reset commands with known fixture data
      3. Assert stdout/exit codes match expected backend responses
    Expected Result: CLI is a faithful HTTP wrapper
    Failure Indicators: Divergent flags, broken serialization, or wrong exit codes
    Evidence: .sisyphus/evidence/task-11-cli-smoke.txt
  ```

  **Commit**: NO

- [x] 12. Add CLI test coverage and smoke fixtures

  **What to do**:
  - Add tests for command parsing, backend request mapping, output formatting, and failure/empty-result behavior.
  - Add fixture scenarios for no-results, degraded backend responses, and reset confirmations.
  - Ensure CLI tests stay backend-agnostic where possible via stubbed HTTP responses.

  **Must NOT do**:
  - Do not rely exclusively on manual CLI testing.
  - Do not hardcode unstable output details that make tests brittle without value.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: This is focused coverage for a thin command wrapper.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `requesting-code-review`: review is a later phase, not a planning ingredient.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 15, Final verification
  - **Blocked By**: Task 11

  **References**:
  - Task 11 CLI contract - Source of truth for expected subcommands and flag behavior.
  - Metis review - Explicitly calls out empty results, server-down behavior, and CLI usability as acceptance concerns.

  **Acceptance Criteria**:
  - [ ] CLI tests cover happy path, no-results, degraded backend response, and backend-unavailable cases.
  - [ ] CLI smoke fixtures prove code + Markdown query scenarios.

  **QA Scenarios**:
  ```
  Scenario: CLI automated tests cover success and failure paths
    Tool: Bash (pytest)
    Preconditions: CLI tests implemented
    Steps:
      1. Run `pytest tests/ -k "cli" -v`
      2. Confirm tests cover success, empty results, degraded response, and backend unavailable cases
    Expected Result: CLI test suite passes across core scenarios
    Failure Indicators: Missing failure handling or broken exit codes/output
    Evidence: .sisyphus/evidence/task-12-cli-tests.txt

  Scenario: CLI smoke fixtures show both file types are reachable
    Tool: Bash (pytest)
    Preconditions: Fixture backend/stub returns code + markdown hits
    Steps:
      1. Run fixture-based CLI smoke tests
      2. Assert output contains one code path and one markdown path for targeted queries
    Expected Result: CLI can surface both supported v1 file categories
    Failure Indicators: One file type never appears or output loses provenance
    Evidence: .sisyphus/evidence/task-12-cli-fixtures.txt
  ```

  **Commit**: NO

- [x] 13. Add HTTP-backed MCP bridge and tool schemas

  **What to do**:
  - Add an MCP bridge process that exposes tools over MCP while forwarding work to the FastAPI backend over HTTP.
  - Define MCP tools for at least `mgrep_query`, `mgrep_sync`, `mgrep_status`, and `mgrep_reset` using schemas aligned to the new backend routes.
  - Ensure tool responses preserve enough structure for OpenCode agents to use results deterministically.

  **Must NOT do**:
  - Do not duplicate backend indexing/query logic inside the MCP bridge.
  - Do not make MCP depend on synthesized answer generation in v1.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: MCP protocol correctness and tool schema design are high-leverage integration points.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `find-skills`: not relevant; this is not a skill-discovery task.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: Tasks 14, 15, Final verification
  - **Blocked By**: Tasks 7, 9

  **References**:
  - User decision in `.sisyphus/drafts/mem0-mgrep.md` - MCP integration must be an HTTP-backed bridge.
  - OpenCode integration notes from `README.md` - Backend currently serves the local OpenCode mem0 plugin via `MEM0_SERVER_URL`.
  - `server.py` route contracts - MCP bridge must map to HTTP, not invent separate semantics.

  **Acceptance Criteria**:
  - [ ] MCP bridge starts and responds to `tools/list`.
  - [ ] MCP tool schemas are valid and aligned to backend route inputs.
  - [ ] Tool calls return structured results for query/status/sync/reset.

  **QA Scenarios**:
  ```
  Scenario: MCP bridge exposes the expected tools
    Tool: Bash
    Preconditions: MCP bridge entrypoint implemented
    Steps:
      1. Start `python -m services.mcp_bridge` as a subprocess in a test or shell harness
      2. Send `tools/list` JSON-RPC over stdio
      3. Assert returned tool names include `mgrep_query`, `mgrep_sync`, `mgrep_status`, `mgrep_reset`
    Expected Result: MCP bridge responds with a valid tools list
    Failure Indicators: Invalid JSON-RPC, missing tools, or process crash
    Evidence: .sisyphus/evidence/task-13-mcp-tools-list.txt

  Scenario: MCP query tool forwards to backend and returns structured hits
    Tool: Bash (MCP JSON-RPC)
    Preconditions: Backend running against fixture data or stubbed bridge backend
    Steps:
      1. Call `mgrep_query` over MCP with a fixture query targeting known indexed content
      2. Assert returned payload includes hit list, corpus provenance, and score/range metadata
    Expected Result: MCP results are agent-usable and consistent with HTTP query shape
    Failure Indicators: Flattened/unstructured responses or missing provenance fields
    Evidence: .sisyphus/evidence/task-13-mcp-query.txt
  ```

  **Commit**: NO

- [x] 14. Add OpenCode MCP configuration docs and examples

  **What to do**:
  - Document how to register the new HTTP-backed MCP bridge in OpenCode, including command, environment, and example tool usage.
  - Include example `opencode.json`/MCP snippets or equivalent user-local configuration guidance without touching the user’s actual global config in the plan.
  - Explain how the new mgrep-like tools complement, not replace, the existing mem0 plugin behavior.

  **Must NOT do**:
  - Do not assume write access to the user’s real `~/.config/opencode/opencode.json` during implementation without explicit direction.
  - Do not document synthesized answer mode or unsupported file types in v1 examples.

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: This task is documentation/config guidance focused.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `visual-explainer`: plain docs/config examples are enough here.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 15, Final verification
  - **Blocked By**: Tasks 11, 13

  **References**:
  - `README.md` - Current OpenCode integration section and canonical backend URL defaults.
  - User decision in draft - HTTP-backed bridge chosen for v1.
  - Environment/config guidance in `services/runtime.py` - Existing env var style to mirror in docs.

  **Acceptance Criteria**:
  - [ ] Docs explain how to run the MCP bridge and point it at the backend.
  - [ ] Docs include example OpenCode MCP configuration snippets.
  - [ ] Docs explicitly call out v1 scope: code + Markdown, ranked hits only, no synthesized answers.

  **QA Scenarios**:
  ```
  Scenario: Configuration docs are executable against a local test setup
    Tool: Bash
    Preconditions: Docs/examples written and bridge/backend available
    Steps:
      1. Follow the documented command/environment example in a clean shell
      2. Start the MCP bridge with the documented backend URL
      3. Verify the bridge starts without undocumented extra steps
    Expected Result: Docs are accurate and self-sufficient
    Failure Indicators: Missing env vars, wrong commands, or undocumented prerequisites
    Evidence: .sisyphus/evidence/task-14-doc-smoke.txt

  Scenario: Docs scope statements match actual v1 behavior
    Tool: Bash (grep)
    Preconditions: Docs file exists
    Steps:
      1. Search docs for `PDF`, `answer`, `audio`, `video`
      2. Confirm unsupported capabilities are either absent or explicitly marked out of scope
    Expected Result: Docs do not overclaim beyond v1
    Failure Indicators: Docs advertise unsupported functionality as available
    Evidence: .sisyphus/evidence/task-14-scope-check.txt
  ```

  **Commit**: NO

- [x] 15. Add end-to-end API, CLI, and MCP parity harness

  **What to do**:
  - Create an end-to-end verification harness that runs a fixture indexing/query scenario across HTTP API, CLI, and MCP bridge and compares the core result semantics.
  - Prove parity for query/status/reset at minimum, and verify that path/corpus provenance and result ids remain consistent enough across interfaces.
  - Capture artifacts/logs for final QA evidence.

  **Must NOT do**:
  - Do not accept silent divergence where one interface returns materially different corpora or provenance than another.
  - Do not skip runtime parity checks because unit tests already pass.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: This is cross-surface integration verification requiring orchestration across backend, CLI, and MCP.
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `subagent-driven-development`: execution strategy, not a task-specific skill.

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential
  - **Blocks**: Final verification
  - **Blocked By**: Tasks 10, 12, 13, 14

  **References**:
  - All previous task outputs - This harness is the integration proof that the three interfaces behave consistently.
  - `.sisyphus/drafts/mem0-mgrep.md` - Confirms HTTP + CLI parity plus MCP integration are first-class scope items.

  **Acceptance Criteria**:
  - [ ] One indexed fixture root can be queried consistently from HTTP, CLI, and MCP.
  - [ ] Status and reset semantics are consistent across the three interfaces.
  - [ ] Evidence logs/artifacts exist for final verification.

  **QA Scenarios**:
  ```
  Scenario: HTTP, CLI, and MCP return consistent query semantics
    Tool: Bash
    Preconditions: Backend, CLI, and MCP bridge are all runnable locally; fixture root indexed
    Steps:
      1. Call HTTP `/query` with a known fixture query and save JSON output
      2. Run `python -m mgrep_cli query "<same query>" --corpus all` and save stdout/json output
      3. Call `mgrep_query` via MCP with the same query and save JSON-RPC output
      4. Compare returned top hit ids/paths/corpora across all three outputs
    Expected Result: Core result semantics match across interfaces
    Failure Indicators: One interface omits a corpus, loses provenance, or returns materially different top hits
    Evidence: .sisyphus/evidence/task-15-parity-query.txt

  Scenario: Status and reset stay consistent across interfaces
    Tool: Bash
    Preconditions: Indexed fixture root exists
    Steps:
      1. Read status via HTTP, CLI, and MCP and compare indexed root/file counts
      2. Trigger reset through one interface
      3. Re-read status across the other interfaces and confirm they all observe the cleared file/doc corpus
    Expected Result: Shared backend state is visible consistently across all consumers
    Failure Indicators: Divergent status counts or reset visible only through one interface
    Evidence: .sisyphus/evidence/task-15-parity-status-reset.txt
  ```

  **Commit**: NO

---

## Final Verification Wave

> 4 review agents run in parallel after all implementation tasks complete. All must approve before the work is presented for user sign-off.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Verify every must-have item exists, every out-of-scope guardrail remains absent, and evidence files for API/CLI/MCP workflows are present.

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `pytest tests/ -v`, inspect changed files for unused imports, commented-out code, silent exception swallowing, and brittle watch/process handling.

- [ ] F3. **Real Runtime QA** — `unspecified-high`
  Start the backend, run sync/query/status/reset through curl, run CLI commands, then run MCP `tools/list` + `tools/call` smoke checks and save outputs under `.sisyphus/evidence/final-qa/`.

- [ ] F4. **Scope Fidelity Check** — `deep`
  Compare final diff against this plan and ensure no synthesized answer mode, no PDF indexing, and no breaking changes to current memory endpoints slipped into the implementation.

---

## Commit Strategy

- **Agent commits**: NO
- **Policy**: No agent may commit or push unless the user explicitly instructs it.
- **Developer-only suggestion**: if the user later requests commits, group them by wave (foundations, backend surfaces, clients/integration).

---

## Success Criteria

### Verification Commands
```bash
pytest tests/ -v
# Expected: all existing memory tests plus new indexing/query/CLI/MCP tests pass

python -m mgrep_cli --help
# Expected: query, sync, watch, status, reset commands are listed

curl -s http://localhost:8000/query/capabilities
# Expected: JSON includes memory_store + file_corpus capabilities and watch/reset support

python -m services.mcp_bridge <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
EOF
# Expected: JSON includes mgrep_query, mgrep_sync, mgrep_status, mgrep_reset tools
```

### Final Checklist
- [ ] Existing `/search`, `/retrieve`, and memory CRUD behavior remains intact
- [ ] Code + Markdown fixtures can be synced, watched, queried, and reset through HTTP
- [ ] HTTP, CLI, and MCP bridge expose consistent query/status/reset semantics
- [ ] Unified query results include corpus provenance, scores/metadata, and degradation/capability fields
- [ ] Ignore rules and file-size/binary safety guards prevent noisy or dangerous indexing
- [ ] No synthesized answer mode, PDF support, or other out-of-scope capabilities were added to v1
