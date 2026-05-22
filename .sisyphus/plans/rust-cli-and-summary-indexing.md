# Rust CLI Migration and Summary-Enriched Indexing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## TL;DR

> **Quick Summary**: Replace the Python `mgrep` CLI with an in-repo Rust single binary named `m0grep` that preserves the current command contract, add merge-to-`main` GitHub Release automation driven by a root `VERSION` file, and extend the Python server with opt-in summary-enriched indexing plus query-time chunk-memory retrieval gated by `USE_CHUNK_MEMORY`.
>
> **Deliverables**:
> - Rust `m0grep` binary in `cli/` with parity for `query`, `sync`, `watch`, `status`, and `reset`
> - Root `VERSION` contract used by CI/release logic and Docker build tagging
> - GitHub Actions release workflow for Linux amd64, macOS amd64, and macOS arm64
> - Repository `LICENSE` file using Apache-2.0
> - Server-side `generate_summaries` indexing flag plus stored summary text/embeddings
> - Query integration that consults summary-backed chunk memory only when `USE_CHUNK_MEMORY` is enabled server-side
> - Removal of `mgrep_cli.py` and obsolete Python CLI tests after Rust parity is verified
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 3 implementation waves + final verification wave
> **Critical Path**: 1 → 2 → 3 → 6/7/8 → 15 and 4 → 9 → 10 → 11 → F1-F4

---

## Context

### Original Request
- Migrate the CLI to Rust as one single binary and remove the Python CLI.
- Keep the CLI a thin HTTP client.
- Add CI that builds binaries for Linux amd64, macOS amd64, and macOS arm64, then publishes them to GitHub Releases on merges to `main`.
- Introduce a server-side indexing flag that asks the model to generate summaries for indexed objects and stores both summary text and embeddings.
- Use those summary artifacts for retrieval/query only when the server-side `USE_CHUNK_MEMORY` flag is enabled.

### Interview Summary
**Key Discussions**:
- Preserve the current CLI contract exactly; no breaking rename/restructure of commands or core flags.
- Use TDD for the migration and server feature work.
- Root `VERSION` file is the source of truth; Docker/build paths must be able to read `$(cat VERSION)`.
- Add an Apache-2.0 `LICENSE` file as part of the work.
- Publishing happens when changes merge to `main`.
- If `VERSION` was not bumped or a release already exists, CI skips publishing instead of failing or overwriting.
- Summary generation is per indexing request only.
- Summary-backed retrieval is enabled only when a server-side flag such as `USE_CHUNK_MEMORY` is enabled.

**Research Findings**:
- Existing CLI entrypoint is `mgrep_cli.py`, implemented with Typer + requests, and already acts as a thin HTTP client.
- Existing server routes include `/query`, `/index/sync`, `/index/watch/start`, `/index/watch/stop`, `/index/status`, and `/index/reset`.
- `api_models.py` already defines `UnifiedQueryRequest`, `IndexSyncRequest`, `WatchStartRequest`, `WatchStopRequest`, and `IndexResetRequest`.
- `services/indexing_service.py`, `services/watch_service.py`, `services/file_corpus_service.py`, and `services/query_service.py` are the main extension points for summary-enriched indexing/query.
- The repo currently has no GitHub Actions workflows and no Rust project files.

### Metis Review
**Identified Gaps** (resolved in plan defaults):
- Rust CLI location defaulted to `cli/` to keep Python backend layout stable.
- Rust stack defaulted to `clap` + `reqwest` + `serde` + `anyhow` for a minimal HTTP CLI.
- Release matrix defaulted to native GitHub runners: `ubuntu-latest`, `macos-13`, and `macos-14`.
- Summary generation defaults to the server's existing runtime LLM/embedder configuration; failure logs a warning and indexes the chunk without summary fields.
- Summary-backed query stays server-gated (`USE_CHUNK_MEMORY`) and is not exposed as a new CLI flag to preserve exact CLI contract.
- `VERSION` format defaulted to trimmed semver text without a `v` prefix (for example `1.2.3`).

---

## Work Objectives

### Core Objective
Ship a Rust replacement for the current Python CLI without changing the external command contract, while also extending the Python indexing/query pipeline to optionally enrich chunks with model-generated summaries and summary embeddings under explicit server-side control.

### Concrete Deliverables
- `cli/` Rust crate that builds a single `m0grep` binary
- Root `VERSION` file and version-alignment checks
- `.github/workflows/release.yml` release automation
- `LICENSE` with Apache-2.0 text
- Summary-aware indexing inputs in `api_models.py` / `server.py`
- Summary generation service plus persisted summary text/embedding fields in file corpus records
- Query-path support for summary-backed chunk retrieval gated by `USE_CHUNK_MEMORY`
- Removal of `mgrep_cli.py` and `tests/test_cli.py`

### Definition of Done
- [ ] `cargo test --manifest-path cli/Cargo.toml` passes for the Rust CLI parity suite
- [ ] `pytest tests/test_server.py tests/test_indexing.py tests/test_query_api.py tests/test_integration.py -v` passes with summary feature coverage
- [ ] `gh workflow run` is not required for validation because the release workflow logic can be dry-checked locally via shell and YAML inspection, and on merge to `main` it builds three binaries and publishes/skips correctly
- [ ] Legacy Python CLI file `mgrep_cli.py` is removed and the supported compiled tool name is `m0grep`

### Must Have
- Exact command parity for `query`, `sync`, `watch`, `status`, and `reset`
- No Python runtime dependency for CLI usage after migration
- Repository includes an Apache-2.0 `LICENSE` file
- Release publish/skip behavior driven exclusively by `VERSION`
- Summary text and summary embeddings stored together with indexed chunk/object records when summary generation is requested
- Query path can incorporate summary-backed chunk memory only when `USE_CHUNK_MEMORY` is enabled server-side

### Must NOT Have (Guardrails)
- No FastAPI route-path changes or incompatible response schema changes for existing endpoints
- No extra CLI features beyond preserving the current Python contract
- No release overwrite behavior for an existing version
- No summary generation on indexing requests that did not opt in
- No indexing failure solely because summary generation or summary embedding generation failed
- No agent commit or push steps; the developer remains the only party allowed to commit/push
- No server rewrite to Rust and no plan-file commits

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — all verification must be agent-executed with commands and captured evidence.

### Test Decision
- **Infrastructure exists**: YES (`pytest`, existing HTTP/index/query suites)
- **Automated tests**: TDD
- **Framework**: `pytest` for Python server, `cargo test` for Rust CLI
- **If TDD**: each implementation task writes/updates the failing test first, then minimal code, then reruns the relevant target

### QA Policy
- **CLI verification**: Use Bash to run `cargo test`, `cargo run -- ...`, and binary smoke commands against a local FastAPI test server or test harness
- **API verification**: Use Bash + `pytest` and targeted `curl`/`python - <<'PY'` probes against the server app where needed
- **Index/query verification**: Use Bash to run targeted pytest selectors plus JSON assertions
- **Documentation pass**: Every task touching public functions, changed functions, or changed type fields must include a final language-appropriate doc-comment/docstring pass before completion
- Evidence saved under `.sisyphus/evidence/task-{N}-*.txt`

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start immediately — foundations and contracts):
├── Task 1: VERSION + Rust repo hygiene scaffold [quick]
├── Task 2: Rust CLI crate bootstrap [quick]
├── Task 3: Rust CLI shared config/HTTP/output layer [unspecified-high]
├── Task 4: Server request/runtime flag plumbing for summaries [unspecified-high]
└── Task 5: File corpus summary field persistence contract [unspecified-high]

Wave 2 (After Wave 1 — core implementation in parallel):
├── Task 6: Rust query/status command parity [unspecified-high]
├── Task 7: Rust sync/watch command parity [unspecified-high]
├── Task 8: Rust reset/help/error parity [unspecified-high]
├── Task 9: Summary generation service [deep]
├── Task 10: Indexing + watch propagation for summary enrichment [deep]
└── Task 11: Query integration for chunk memory summaries [deep]

Wave 3 (After Wave 2 — release, cleanup, and docs):
├── Task 12: GitHub Actions build/release workflow [unspecified-high]
├── Task 13: VERSION propagation to Docker/build helpers [quick]
├── Task 14: Add Apache-2.0 LICENSE and document licensing [writing]
├── Task 15: User-facing docs for Rust CLI and release behavior [writing]
└── Task 16: Remove Python CLI after Rust parity is green [quick]

Wave FINAL (After ALL tasks — 4 parallel reviews, then explicit user okay):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay

Critical Path: 1 → 2 → 3 → 6/7/8 → 16 → F1-F4 and 4 → 9 → 10 → 11 → F1-F4
Parallel Speedup: ~60% faster than sequential execution
Max Concurrent: 6
```

### Dependency Matrix

- **1**: — — 2, 12, 13, 14, 15, 1
- **2**: 1 — 3, 6, 7, 8, 12, 14, 15, 16, 1
- **3**: 2 — 6, 7, 8, 15, 1
- **4**: — — 9, 10, 11, 1
- **5**: — — 10, 11, 1
- **6**: 2, 3 — 16, 2
- **7**: 2, 3 — 16, 2
- **8**: 2, 3 — 16, 2
- **9**: 4, 5 — 10, 11, 2
- **10**: 4, 5, 9 — 11, 2
- **11**: 4, 5, 9, 10 — 15, F1-F4, 2
- **12**: 1, 2 — 15, 16, F1-F4, 3
- **13**: 1 — 15, F1-F4, 3
- **14**: 1 — 15, F1-F4, 3
- **15**: 1, 2, 11, 12, 13, 14 — F1-F4, 3
- **16**: 2, 3, 6, 7, 8, 12 — F1-F4, 3

### Agent Dispatch Summary

- **1**: **5** — T1 → `quick`, T2 → `quick`, T3 → `unspecified-high`, T4 → `unspecified-high`, T5 → `unspecified-high`
- **2**: **6** — T6-T8 → `unspecified-high`, T9-T11 → `deep`
- **3**: **5** — T12 → `unspecified-high`, T13 → `quick`, T14-T15 → `writing`, T16 → `quick`
- **FINAL**: **4** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [x] 1. Establish repository versioning and Rust-toolchain foundations

  **What to do**:
  - Add a root `VERSION` file containing the initial semver value used by release logic.
  - Add Rust toolchain metadata and ignore rules required for an in-repo CLI crate.
  - Ensure the chosen version string can be safely trimmed/read from shell and Rust without newline bugs.
  - Add tests or shell assertions that lock the expected `VERSION` format and trimming behavior.
  - Perform the required doc-comment/docstring final pass for any changed public config/type fields.

  **Must NOT do**:
  - Do not add auto-bump logic.
  - Do not create release automation in this task.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: constrained scaffolding and format contract work across a few files.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: ensures the version-format checks are written before helper logic.
  - **Skills Evaluated but Omitted**:
    - `using-git-worktrees`: execution-time concern, not needed for planning.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2-5)
  - **Blocks**: 2, 12, 13, 14
  - **Blocked By**: None

  **References**:
  - `README.md` - current documented runtime/build defaults that the new versioning contract must not contradict.
  - `build.sh` - existing build helper to update later for version consumption expectations.
  - `requirements.txt` - example of a root-level version-pinned dependency file; useful as a repo-convention reference for simple root metadata files.

  **Acceptance Criteria**:
  - [ ] Root `VERSION` file exists and contains a trimmed semver string.
  - [ ] Rust toolchain/bootstrap files needed for `cargo` commands exist.
  - [ ] A targeted test or shell check proves `VERSION` parsing trims trailing newline safely.

  **QA Scenarios**:
  ```
  Scenario: VERSION file is consumable by shell and Rust helpers
    Tool: Bash
    Preconditions: Repo contains new VERSION file and any helper used to read it
    Steps:
      1. Run `printf 'VERSION=<%s>\n' "$(cat VERSION)" | tee .sisyphus/evidence/task-1-version-shell.txt`
      2. Assert the output matches `VERSION=<X.Y.Z>` with no extra whitespace characters.
      3. Run the targeted version-format test command and tee output to `.sisyphus/evidence/task-1-version-test.txt`.
    Expected Result: VERSION is read exactly once as trimmed semver and tests pass.
    Failure Indicators: whitespace in captured value, malformed semver, or failing test.
    Evidence: .sisyphus/evidence/task-1-version-shell.txt

  Scenario: Invalid VERSION format fails fast in tests
    Tool: Bash
    Preconditions: Version-format validation test exists.
    Steps:
      1. Run the targeted validation test selector in its normal passing state.
      2. Confirm the selector name/output indicates the invalid-format branch is covered.
    Expected Result: validation suite includes explicit failure-path coverage.
    Evidence: .sisyphus/evidence/task-1-version-test.txt
  ```

  **Commit**: NO

- [x] 2. Bootstrap the Rust CLI crate and binary entrypoint

  **What to do**:
  - Create the in-repo Rust crate under `cli/` with a binary named `m0grep`.
  - Add `Cargo.toml`, `src/main.rs`, and minimal module layout for subcommands and shared HTTP/client code.
  - Mirror the current CLI command names at the parser level without implementing full behavior yet.
  - Add failing Rust parity tests that assert help/command registration structure for `query`, `sync`, `watch`, `status`, and `reset`.
  - Perform the required doc-comment pass on public Rust modules/functions exposed by the crate.

  **Must NOT do**:
  - Do not remove the Python CLI yet.
  - Do not implement final command behavior here; only scaffolding and parser registration.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: focused crate scaffolding in a self-contained directory.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: the help/command parity tests should fail before crate wiring is added.
  - **Skills Evaluated but Omitted**:
    - `typescript-reviewer`: irrelevant to Rust work.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 3, 6, 7, 8, 12, 14, 15
  - **Blocked By**: 1

  **References**:
  - `mgrep_cli.py` - authoritative source for current command names and option surface.
  - `tests/test_cli.py:TestHelp` - existing help-command coverage that the Rust CLI must eventually match.
  - `tests/test_e2e_parity.py:TestCapabilitiesStatusParity` - examples of current CLI invocation expectations in test harnesses.

  **Acceptance Criteria**:
  - [ ] `cargo test --manifest-path cli/Cargo.toml help`-related selectors fail first, then pass.
  - [ ] Running `cargo run --manifest-path cli/Cargo.toml -- --help` lists the five required subcommands.
  - [ ] Binary name resolves to `m0grep` in Cargo metadata/output.

  **QA Scenarios**:
  ```
  Scenario: Rust CLI advertises the expected command surface
    Tool: Bash
    Preconditions: Rust crate exists under `cli/`.
    Steps:
      1. Run `cargo run --manifest-path cli/Cargo.toml -- --help | tee .sisyphus/evidence/task-2-root-help.txt`.
      2. Assert the output contains `query`, `sync`, `watch`, `status`, and `reset`.
      3. Run `cargo test --manifest-path cli/Cargo.toml help -- --nocapture | tee .sisyphus/evidence/task-2-help-tests.txt`.
    Expected Result: help output and tests agree on the command surface.
    Failure Indicators: missing subcommand, wrong binary name, or failing help tests.
    Evidence: .sisyphus/evidence/task-2-root-help.txt

  Scenario: Missing subcommand registration is caught by the test suite
    Tool: Bash
    Preconditions: help tests exist.
    Steps:
      1. Run the specific help-related cargo test selector.
      2. Confirm output identifies the registered subcommand expectations.
    Expected Result: suite would catch parser regressions immediately.
    Evidence: .sisyphus/evidence/task-2-help-tests.txt
  ```

  **Commit**: NO

- [x] 3. Build shared Rust CLI config, HTTP client, and error/output parity layer

  **What to do**:
  - Implement the shared Rust logic for base URL resolution, request dispatch, HTTP error mapping, connection-error mapping, JSON printing, and plain-text no-result output.
  - Match current defaults: `MEM0_SERVER_URL` fallback to `http://localhost:8000` and 30-second request timeout.
  - Add Rust parity tests that mirror `tests/test_cli.py` behavior for URL selection, HTTP errors, connection errors, and raw/JSON output helpers.
  - Keep watch behavior aligned with existing semantics: start and stop are independent HTTP calls, not a long-lived streaming client.
  - Do the required doc-comment pass on public Rust config/error helpers.

  **Must NOT do**:
  - Do not change server endpoints.
  - Do not add config-file support or new output modes.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: correctness-sensitive parity layer with multiple edge cases.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: needed to port existing Python CLI expectations into Rust before coding.
  - **Skills Evaluated but Omitted**:
    - `systematic-debugging`: not primary unless parity tests start failing unexpectedly.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 6, 7, 8, 15
  - **Blocked By**: 2

  **References**:
  - `mgrep_cli.py:_base_url`, `_post`, `_get` - current default URL and error-handling contract.
  - `tests/test_cli.py:TestQueryCommand.test_backend_unavailable` - expected failure handling for connection errors.
  - `tests/test_cli.py:TestStatusCommand.test_backend_http_error` - expected HTTP error behavior.
  - `tests/test_cli.py:TestQueryCommand.test_happy_path_raw_flag` - raw JSON output expectation.
  - `services/watch_service.py` - confirms watch is server-managed polling, so the CLI only starts/stops watches.

  **Acceptance Criteria**:
  - [ ] Rust shared helpers pass parity tests for base URL, timeout, error handling, and raw output.
  - [ ] `watch` implementation assumptions remain request/response based, not streaming.
  - [ ] No new config source beyond existing env + explicit flag behavior.

  **QA Scenarios**:
  ```
  Scenario: Rust shared client matches Python-style error behavior
    Tool: Bash
    Preconditions: cargo parity tests for connection and HTTP error paths exist.
    Steps:
      1. Run `cargo test --manifest-path cli/Cargo.toml parity_errors -- --nocapture | tee .sisyphus/evidence/task-3-parity-errors.txt`.
      2. Assert the suite covers connection refusal and HTTP 5xx mapping.
    Expected Result: all error-path parity tests pass.
    Failure Indicators: mismatched exit/error text, wrong timeout/default URL behavior.
    Evidence: .sisyphus/evidence/task-3-parity-errors.txt

  Scenario: Raw output path emits valid JSON only when requested
    Tool: Bash
    Preconditions: local test server or mocked transport exists in test suite.
    Steps:
      1. Run the targeted raw-output cargo test selector.
      2. Confirm captured output parses as JSON and no formatted text markers leak in.
    Expected Result: raw mode is deterministic JSON.
    Evidence: .sisyphus/evidence/task-3-raw-mode.txt
  ```

  **Commit**: NO

- [x] 4. Extend API models and server request plumbing for opt-in summary indexing and chunk-memory gating

  **What to do**:
  - Add request fields to the indexing endpoints/models so sync/watch requests can opt into summary generation per request.
  - Add or expose server-side runtime reading for `USE_CHUNK_MEMORY` without changing existing route paths.
  - Thread the new request fields from `server.py` into `IndexingService` and from query handling into `QueryService`/service dependencies as needed.
  - Add failing pytest coverage for request validation and for server-side gating behavior.
  - Do the required docstring pass for all changed Pydantic models, request fields, and public helper functions.

  **Must NOT do**:
  - Do not make summary generation globally on by default.
  - Do not expose a new CLI query flag for chunk-memory use.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: public API contract changes with backward-compatibility constraints.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: model and route tests must lock the new contract before implementation.
  - **Skills Evaluated but Omitted**:
    - `verification-before-completion`: useful later, but not the main implementation driver.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 9, 10, 11
  - **Blocked By**: None

  **References**:
  - `api_models.py:IndexSyncRequest`, `WatchStartRequest`, `WatchStopRequest`, `UnifiedQueryRequest` - request models to extend.
  - `server.py:index_sync`, `index_watch_start`, `index_watch_stop`, `unified_query` - route plumbing to preserve while threading new flags.
  - `services/runtime.py:get_runtime_options` - place to mirror environment-backed runtime toggles such as `USE_CHUNK_MEMORY`.
  - `tests/test_server.py` - existing request/route testing pattern.

  **Acceptance Criteria**:
  - [ ] Sync/watch request models accept a per-request summary-generation flag without breaking existing callers.
  - [ ] Server can determine whether chunk-memory retrieval is enabled from `USE_CHUNK_MEMORY`.
  - [ ] Existing route paths and successful response shapes remain backward compatible.

  **QA Scenarios**:
  ```
  Scenario: Sync request accepts summary opt-in without breaking legacy payloads
    Tool: Bash
    Preconditions: pytest coverage for API model and route validation exists.
    Steps:
      1. Run `pytest tests/test_server.py -k 'sync and summary' -v | tee .sisyphus/evidence/task-4-sync-summary.txt`.
      2. Run `pytest tests/test_api_models.py -k 'IndexSyncRequest or WatchStartRequest' -v | tee .sisyphus/evidence/task-4-models.txt`.
      3. Assert legacy payloads without the new flag still pass.
    Expected Result: both new and legacy request shapes validate correctly.
    Failure Indicators: 422/400 on legacy calls or missing new field support.
    Evidence: .sisyphus/evidence/task-4-sync-summary.txt

  Scenario: USE_CHUNK_MEMORY gating is server-side only
    Tool: Bash
    Preconditions: tests can patch env vars.
    Steps:
      1. Run targeted pytest selectors with `USE_CHUNK_MEMORY` enabled and disabled.
      2. Confirm query behavior changes only with server env changes, not request shape changes.
    Expected Result: gate is controlled solely by server runtime flag.
    Evidence: .sisyphus/evidence/task-4-chunk-memory-gate.txt
  ```

  **Commit**: NO

- [x] 5. Persist summary text and summary embeddings in file corpus records without breaking existing chunk storage

  **What to do**:
  - Extend file corpus record structures to store nullable `summary_text` and `summary_embedding` (or equivalent) fields alongside existing chunk metadata.
  - Preserve backward compatibility so existing chunks without summary data still query and reset correctly.
  - Add tests for upsert/query/reset behavior when summary fields are absent, present, or mixed.
  - Keep embedding storage shape explicit and documented.
  - Do the required doc-comment/docstring pass for changed storage fields and helper functions.

  **Must NOT do**:
  - Do not change the meaning of existing `content` fields.
  - Do not make summary fields mandatory.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: storage-shape change that must remain backward compatible.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: mixed-summary and no-summary storage cases must be locked first.
  - **Skills Evaluated but Omitted**:
    - `systematic-debugging`: only needed if mixed-mode regressions appear.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 10, 11
  - **Blocked By**: None

  **References**:
  - `services/file_corpus_service.py` - current chunk record structure and query/reset semantics.
  - `tests/test_indexing.py:TestFileCorpusIsolation` - storage behavior expectations to extend.
  - `api_models.py:FileHit` - downstream response contract that may need optional summary-related provenance metadata if surfaced internally.

  **Acceptance Criteria**:
  - [ ] File corpus can store chunks with or without summary fields.
  - [ ] Existing non-summary query/reset behavior remains green.
  - [ ] Mixed corpora (some chunks summarized, some not) do not error.

  **QA Scenarios**:
  ```
  Scenario: Mixed summary/no-summary chunks persist and query safely
    Tool: Bash
    Preconditions: pytest coverage exists for file corpus storage.
    Steps:
      1. Run `pytest tests/test_indexing.py -k 'summary or FileCorpusIsolation' -v | tee .sisyphus/evidence/task-5-file-corpus.txt`.
      2. Confirm both summarized and non-summarized chunks can be queried/reset without exceptions.
    Expected Result: mixed-mode storage tests pass.
    Failure Indicators: KeyError on missing fields, reset/query regressions.
    Evidence: .sisyphus/evidence/task-5-file-corpus.txt

  Scenario: Summary embedding field remains nullable
    Tool: Bash
    Preconditions: targeted test covers absent summary embedding.
    Steps:
      1. Run the nullable-summary selector.
      2. Verify output includes a case where summary fields are omitted entirely.
    Expected Result: omission is supported and tested.
    Evidence: .sisyphus/evidence/task-5-nullable-summary.txt
  ```

  **Commit**: NO

- [x] 6. Implement Rust `query` and `status` command parity end-to-end

  **What to do**:
  - Port `query` and `status` commands into Rust using the shared HTTP/config layer.
  - Match request payloads, raw-output handling, no-results messaging, and formatted hit rendering.
  - Add/port Rust parity tests corresponding to the Python command tests and existing HTTP/CLI parity fixtures.
  - Validate against a local FastAPI test harness or request-mocking layer.
  - Perform the required Rust doc-comment final pass for public command handlers and option structs.

  **Must NOT do**:
  - Do not add extra output flags or change field names in rendered JSON.
  - Do not consult summary-backed query behavior from the CLI; query gating remains server-side.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: direct command parity with multiple formatting and payload branches.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: parity tests must anchor request shapes and output before implementation.
  - **Skills Evaluated but Omitted**:
    - `playwright-reviewer`: no browser UI involved.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7-11)
  - **Blocks**: 15
  - **Blocked By**: 2, 3

  **References**:
  - `mgrep_cli.py:query`, `status` - authoritative behavior for request payload and output formatting.
  - `tests/test_cli.py:TestQueryCommand`, `TestStatusCommand` - detailed parity expectations.
  - `tests/test_e2e_parity.py:TestCapabilitiesStatusParity`, `TestQueryParity` - repo-level integration pattern for CLI/API parity.
  - `server.py:unified_query`, `index_status` - endpoint contracts the Rust CLI must call.

  **Acceptance Criteria**:
  - [ ] Rust `query` and `status` tests pass for happy path, raw JSON, no results, backend unavailable, and HTTP error cases.
  - [ ] Payload fields and default URL behavior match the Python CLI contract.
  - [ ] Integration parity coverage proves Rust command output is acceptable against the existing server responses.

  **QA Scenarios**:
  ```
  Scenario: Rust query/status commands match expected payload and output behavior
    Tool: Bash
    Preconditions: cargo parity tests for query/status exist.
    Steps:
      1. Run `cargo test --manifest-path cli/Cargo.toml query_status -- --nocapture | tee .sisyphus/evidence/task-6-query-status-tests.txt`.
      2. Run `cargo run --manifest-path cli/Cargo.toml -- status` against the local harness or mocked integration path and capture output.
      3. Assert output includes the expected JSON keys for status and formatted hit lines for query.
    Expected Result: all query/status parity tests pass and command output is stable.
    Failure Indicators: mismatched payload keys, wrong output text, or failing tests.
    Evidence: .sisyphus/evidence/task-6-query-status-tests.txt

  Scenario: No-results query path remains graceful
    Tool: Bash
    Preconditions: test harness can return empty hits.
    Steps:
      1. Run the targeted no-results cargo test selector.
      2. Confirm output contains `No results found.` and exits 0.
    Expected Result: empty-hit behavior exactly mirrors current CLI semantics.
    Evidence: .sisyphus/evidence/task-6-no-results.txt
  ```

  **Commit**: NO

- [x] 7. Implement Rust `sync` and `watch` command parity end-to-end

  **What to do**:
  - Port `sync` and `watch` commands into Rust with the same payload shapes as the Python CLI.
  - Preserve current watch semantics: `watch` starts or stops server-managed polling via `/index/watch/start` and `/index/watch/stop`.
  - Add Rust parity tests for root payload forwarding, watch start/stop path selection, and error handling.
  - Add integration tests against the existing FastAPI watch/sync handlers.
  - Perform the required Rust doc-comment pass on the watch/sync command code and options.

  **Must NOT do**:
  - Do not build a long-running local file watcher into the Rust CLI.
  - Do not change server-side watch implementation semantics.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: parity-sensitive command behavior tied to server endpoint selection.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: endpoint-selection and payload tests should fail first.
  - **Skills Evaluated but Omitted**:
    - `systematic-debugging`: not primary unless watch semantics drift.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 15
  - **Blocked By**: 2, 3

  **References**:
  - `mgrep_cli.py:sync`, `watch` - current command behavior.
  - `tests/test_cli.py:TestSyncCommand`, `TestWatchCommand` - payload and endpoint-path expectations.
  - `services/watch_service.py` - confirms the server-side watch loop implementation.
  - `tests/test_integration.py:test_watch_start_stop_uses_watch_service_without_real_polling` - integration expectations for watch endpoints.
  - `server.py:index_sync`, `index_watch_start`, `index_watch_stop` - endpoints to preserve.

  **Acceptance Criteria**:
  - [ ] Rust `sync` and `watch` parity tests pass for payload shape, endpoint routing, and network/HTTP failures.
  - [ ] Watch start/stop remains a pair of explicit HTTP calls.
  - [ ] Integration tests confirm Rust CLI works with existing sync/watch endpoints.

  **QA Scenarios**:
  ```
  Scenario: Rust sync/watch commands hit the correct endpoints with correct payloads
    Tool: Bash
    Preconditions: cargo parity tests and integration harness exist.
    Steps:
      1. Run `cargo test --manifest-path cli/Cargo.toml sync_watch -- --nocapture | tee .sisyphus/evidence/task-7-sync-watch-tests.txt`.
      2. Run the targeted integration tests against the FastAPI harness and capture output to `.sisyphus/evidence/task-7-integration.txt`.
    Expected Result: all selectors pass and confirm `/index/sync`, `/index/watch/start`, and `/index/watch/stop` usage.
    Failure Indicators: wrong endpoint path, missing root payload, or watch semantics drift.
    Evidence: .sisyphus/evidence/task-7-sync-watch-tests.txt

  Scenario: Watch stop path handles already-stopped roots gracefully
    Tool: Bash
    Preconditions: stop-path test exists.
    Steps:
      1. Run the stop-path cargo/pytest selector.
      2. Confirm it exits successfully without requiring a live long-running client session.
    Expected Result: stop behavior remains idempotent from the CLI perspective.
    Evidence: .sisyphus/evidence/task-7-watch-stop.txt
  ```

  **Commit**: NO

- [x] 8. Implement Rust `reset` command, confirmation flow, and remaining CLI parity edges

  **What to do**:
  - Port `reset` confirmation behavior and payload shape into Rust.
  - Preserve `--yes` semantics and non-destructive abort behavior.
  - Fill any remaining CLI parity gaps such as exit codes, subcommand help text, or shared formatting not covered by Tasks 6-7.
  - Add Rust tests covering confirm/abort, confirm/proceed, and backend error paths.
  - Do the required Rust doc-comment final pass on reset-related handlers and option structs.

  **Must NOT do**:
  - Do not change the destructive confirmation message semantics without a compelling parity reason.
  - Do not delete the Python CLI in this task.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: user-facing destructive-command semantics and exit-code parity matter.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: confirmation/abort behavior should be locked by tests first.
  - **Skills Evaluated but Omitted**:
    - `verification-before-completion`: final verification concern, not implementation.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 15
  - **Blocked By**: 2, 3

  **References**:
  - `mgrep_cli.py:reset` - current prompt/abort/payload behavior.
  - `tests/test_cli.py:TestResetCommand` - confirm/abort/error-path expectations.
  - `server.py:index_reset` and `api_models.py:IndexResetRequest` - backend reset contract.

  **Acceptance Criteria**:
  - [ ] Rust reset command passes tests for `--yes`, declined confirmation, confirmed prompt, HTTP errors, and connection failures.
  - [ ] Abort path does not call the backend and exits 0.
  - [ ] Payload includes `confirm: true` when reset proceeds.

  **QA Scenarios**:
  ```
  Scenario: Rust reset confirmation flow mirrors current CLI behavior
    Tool: Bash
    Preconditions: cargo tests for reset flow exist.
    Steps:
      1. Run `cargo test --manifest-path cli/Cargo.toml reset -- --nocapture | tee .sisyphus/evidence/task-8-reset-tests.txt`.
      2. Run a targeted integration/mocked command invocation that declines confirmation and capture output.
      3. Confirm the backend-call mock remains untouched in the decline case.
    Expected Result: all reset flow tests pass and abort path is non-destructive.
    Failure Indicators: backend called on abort, wrong exit code, missing `confirm: true` payload.
    Evidence: .sisyphus/evidence/task-8-reset-tests.txt

  Scenario: Reset backend error path surfaces clear failure
    Tool: Bash
    Preconditions: mocked 500/connection error tests exist.
    Steps:
      1. Run the reset error selector.
      2. Confirm command exits non-zero and captures the backend failure output.
    Expected Result: destructive command fails loudly on backend problems.
    Evidence: .sisyphus/evidence/task-8-reset-errors.txt
  ```

  **Commit**: NO

- [ ] 9. Add a summary-generation service using the server’s existing LLM/embedder configuration

  **What to do**:
  - Implement a dedicated service/helper that can generate a natural-language summary for a chunk/object and derive embeddings for that summary using the server’s configured LLM/embedder stack.
  - Define failure policy explicitly: if summary generation or embedding fails, log a warning and return a no-summary result without aborting indexing.
  - Add targeted tests for successful generation, empty/whitespace summary rejection, provider failure fallback, and embedding failure fallback.
  - Keep prompt/template logic localized and documented.
  - Perform the required docstring final pass on all new service functions and changed type fields.

  **Must NOT do**:
  - Do not introduce a mandatory new provider configuration path for normal indexing.
  - Do not make summary generation a prerequisite for chunk persistence.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: new cross-cutting behavior touching LLM and embedding policy with failure handling.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: necessary to pin fallback policy and empty-summary handling first.
  - **Skills Evaluated but Omitted**:
    - `systematic-debugging`: useful only after unexpected provider interactions.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 10, 11
  - **Blocked By**: 4, 5

  **References**:
  - `services/runtime.py:get_config_from_env`, `build_memory_instance` - existing provider/config patterns.
  - `requirements.txt` - confirms available model/embedding-related dependencies already in the repo.
  - `tests/test_server.py` - existing fake-memory/fake-config testing style for non-network server behavior.

  **Acceptance Criteria**:
  - [ ] Summary service returns summary text + embedding payload on success.
  - [ ] Empty/whitespace summary is treated as failure and does not poison indexing.
  - [ ] Provider/embedding failure produces a logged warning and a no-summary result.

  **QA Scenarios**:
  ```
  Scenario: Summary service succeeds and returns both text and embeddings
    Tool: Bash
    Preconditions: pytest selectors exist for summary service success path.
    Steps:
      1. Run `pytest tests/test_server.py tests/test_indexing.py -k 'summary service and success' -v | tee .sisyphus/evidence/task-9-summary-success.txt`.
      2. Confirm output includes a case asserting both summary text and embedding vector presence.
    Expected Result: success-path summary service tests pass.
    Failure Indicators: missing embedding output, missing summary text, or failing provider mocks.
    Evidence: .sisyphus/evidence/task-9-summary-success.txt

  Scenario: Summary generation failure does not abort indexing contract
    Tool: Bash
    Preconditions: fallback-path tests exist.
    Steps:
      1. Run targeted failure selectors for LLM timeout, empty summary, and embedder failure.
      2. Confirm each case logs a warning and returns a no-summary result instead of raising.
    Expected Result: fallback policy is enforced and tested.
    Evidence: .sisyphus/evidence/task-9-summary-failure.txt
  ```

  **Commit**: NO

- [x] 10. Integrate summary generation into sync/watch indexing without breaking baseline indexing

  **What to do**:
  - Thread the per-request summary-generation flag through `IndexingService.sync` and any watch-triggered resync path.
  - Generate summaries only for opted-in requests and store the resulting summary text/embedding into the file corpus record structure.
  - Preserve baseline indexing behavior when the flag is false or when summary generation fails.
  - Add targeted indexing/integration tests for summary-disabled, summary-enabled, and mixed-mode reindex cases.
  - Perform the required docstring final pass on changed indexing/watch functions and request-handling types.

  **Must NOT do**:
  - Do not regenerate summaries for all existing chunks unless the chosen implementation explicitly defines that policy.
  - Do not make watch-mode indexing behave differently from direct sync for the same flag value.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: indexing pipeline change with mixed-mode behavior and failure fallback.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: mixed summary/no-summary indexing cases should fail before implementation.
  - **Skills Evaluated but Omitted**:
    - `verification-before-completion`: final-stage concern only.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 11
  - **Blocked By**: 4, 5, 9

  **References**:
  - `services/indexing_service.py:sync` - current indexing loop to extend.
  - `services/watch_service.py` - watch loop that repeatedly calls `sync`.
  - `services/chunkers/code_chunker.py`, `services/chunkers/tree_sitter_chunker.py` - chunk/object boundaries whose output will receive summaries.
  - `tests/test_indexing.py:TestIndexingService` - existing incremental indexing coverage to extend.
  - `tests/test_integration.py:test_sync_fixture_root_status_shows_nonzero_counts` - baseline sync contract to preserve.

  **Acceptance Criteria**:
  - [ ] Indexing with summaries disabled matches baseline counts and behavior.
  - [ ] Indexing with summaries enabled stores summary text/embeddings on eligible chunks.
  - [ ] Watch-triggered sync honors the same summary flag semantics.
  - [ ] Summary failure still yields indexed chunks without summary fields.

  **QA Scenarios**:
  ```
  Scenario: Summary-disabled sync remains behaviorally identical to baseline
    Tool: Bash
    Preconditions: pytest/integration selectors cover baseline sync.
    Steps:
      1. Run `pytest tests/test_indexing.py tests/test_integration.py -k 'sync and not summary' -v | tee .sisyphus/evidence/task-10-sync-baseline.txt`.
      2. Confirm file and chunk counts match the pre-feature expectations.
    Expected Result: summary-disabled path shows no regression.
    Failure Indicators: changed counts, new errors, or altered status output.
    Evidence: .sisyphus/evidence/task-10-sync-baseline.txt

  Scenario: Summary-enabled sync stores enrichment without failing on provider issues
    Tool: Bash
    Preconditions: summary-enabled indexing tests exist.
    Steps:
      1. Run targeted selectors for summary-enabled sync and failure fallback.
      2. Confirm at least one case asserts stored summary text/embedding fields and one case asserts fallback to plain chunk storage.
    Expected Result: enrichment is additive and non-fatal.
    Evidence: .sisyphus/evidence/task-10-sync-summary.txt
  ```

  **Commit**: NO

- [x] 11. Add query-path chunk-memory retrieval using stored summaries behind `USE_CHUNK_MEMORY`

  **What to do**:
  - Extend `QueryService` and/or supporting retrieval helpers so file-corpus retrieval can consult stored summary text/embeddings when `USE_CHUNK_MEMORY` is enabled server-side.
  - Preserve existing lexical file-corpus behavior when the flag is disabled.
  - Define merging/ranking behavior explicitly for content hits versus summary-backed hits and handle mixed corpora safely.
  - Add tests for flag-disabled baseline behavior, flag-enabled enrichment behavior, degradation/fallback, and mixed-summary corpora.
  - Perform the required docstring final pass on changed retrieval/query functions and any new response/provenance fields.

  **Must NOT do**:
  - Do not require all chunks to have summaries.
  - Do not expose summary text directly in CLI formatting unless existing API requirements demand it.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: retrieval semantics, ranking, and gating behavior are logic-heavy.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: required to lock baseline vs. gated behavior and mixed-corpus safety.
  - **Skills Evaluated but Omitted**:
    - `systematic-debugging`: only if retrieval ranking becomes unexpectedly brittle.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 14, F1-F4
  - **Blocked By**: 4, 5, 9, 10

  **References**:
  - `services/query_service.py` - current fused query logic.
  - `services/file_corpus_service.py:query` - current lexical-only file-corpus lookup to extend or complement.
  - `tests/test_query_api.py` - current fused result and degradation behavior expectations.
  - `tests/test_integration.py:test_degraded_query_file_corpus_raises_returns_memory_hits_and_degraded_flag` - degradation pattern to preserve.
  - `server.py:query_capabilities` - may need truthful capability updates if file-corpus semantic/chunk-memory support becomes available when gated.

  **Acceptance Criteria**:
  - [ ] With `USE_CHUNK_MEMORY` disabled, query results and degradation metadata remain baseline-compatible.
  - [ ] With `USE_CHUNK_MEMORY` enabled, summary-backed retrieval contributes additional or better-ranked file hits when summaries exist.
  - [ ] Mixed corpora (summarized + non-summarized chunks) query without error.
  - [ ] Degradation/fallback remains truthful if summary-backed retrieval fails.

  **QA Scenarios**:
  ```
  Scenario: Query baseline is unchanged when USE_CHUNK_MEMORY is disabled
    Tool: Bash
    Preconditions: env-gated query tests exist.
    Steps:
      1. Run `USE_CHUNK_MEMORY=0 pytest tests/test_query_api.py tests/test_integration.py -k 'query and baseline' -v | tee .sisyphus/evidence/task-11-query-baseline.txt`.
      2. Confirm file-corpus results still derive from the original lexical path.
    Expected Result: disabled mode preserves baseline behavior.
    Failure Indicators: changed results/provenance without the flag.
    Evidence: .sisyphus/evidence/task-11-query-baseline.txt

  Scenario: Summary-backed chunk memory participates only when enabled
    Tool: Bash
    Preconditions: summary-enriched fixtures/tests exist.
    Steps:
      1. Run `USE_CHUNK_MEMORY=1 pytest tests/test_query_api.py tests/test_integration.py -k 'chunk memory or summary query' -v | tee .sisyphus/evidence/task-11-query-summary.txt`.
      2. Confirm at least one assertion proves summary-backed retrieval affects results and one assertion proves mixed-summary corpora remain safe.
    Expected Result: enabled mode adds summary-based retrieval safely.
    Evidence: .sisyphus/evidence/task-11-query-summary.txt
  ```

  **Commit**: NO

- [x] 12. Add GitHub Actions multi-platform build and release workflow keyed off `VERSION`

  **What to do**:
  - Create the GitHub Actions workflow(s) needed to build the Rust CLI on merges to `main` for Linux amd64, macOS amd64, and macOS arm64.
  - Read the release version from the root `VERSION` file, trim whitespace safely, and skip publishing if the version is unchanged/already released.
  - Publish binaries to GitHub Releases without overwrite behavior.
  - Include workflow logic/tests/comments that make the skip path explicit and auditable.
  - Perform the required documentation/comment final pass for workflow env vars and shell logic.

  **Must NOT do**:
  - Do not add auto-version bumping.
  - Do not overwrite/update an existing release for the same version.
  - Do not trigger release publishing from branches other than `main`.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: CI/release logic is correctness-sensitive and externally visible.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: shell-check and workflow-logic validation should be written before final YAML is trusted.
  - **Skills Evaluated but Omitted**:
    - `finishing-a-development-branch`: not relevant; this is pre-merge CI design.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 13-15)
  - **Blocks**: 14, 15, F1-F4
  - **Blocked By**: 1, 2

  **References**:
  - `.github/workflows/` - currently empty; new workflow location.
  - `build.sh` - current build helper; useful for aligning expectations about build output naming.
  - Root `VERSION` file from Task 1 - release source of truth.
  - `README.md` - documented runtime/build behavior that release docs must match.

  **Acceptance Criteria**:
  - [ ] Workflow triggers on merges/pushes to `main`.
  - [ ] Workflow builds all three requested targets.
  - [ ] Workflow creates a GitHub Release when version is new.
  - [ ] Workflow exits successfully while skipping publish when the version already exists or was not effectively bumped.

  **QA Scenarios**:
  ```
  Scenario: Release workflow logic resolves version and skip conditions correctly
    Tool: Bash
    Preconditions: workflow YAML and any helper shell snippets exist.
    Steps:
      1. Run the version-resolution/skip shell helper locally or via targeted shell invocation and tee output to `.sisyphus/evidence/task-12-version-skip.txt`.
      2. Confirm it trims `VERSION`, detects an existing version case, and emits a skip decision without non-zero exit.
      3. Inspect workflow YAML for the three target builds and capture the relevant lines into `.sisyphus/evidence/task-12-workflow.txt`.
    Expected Result: workflow logic clearly supports publish and skip paths.
    Failure Indicators: overwrite path, missing target, or non-zero skip behavior.
    Evidence: .sisyphus/evidence/task-12-version-skip.txt

  Scenario: Workflow matrix covers linux-amd64, macos-amd64, and macos-arm64
    Tool: Bash
    Preconditions: workflow file exists.
    Steps:
      1. Run `grep -nE 'ubuntu|macos-13|macos-14|linux|darwin|amd64|arm64' .github/workflows/*.yml | tee .sisyphus/evidence/task-12-matrix.txt`.
      2. Confirm all three required targets are represented.
    Expected Result: release matrix includes the requested build targets.
    Evidence: .sisyphus/evidence/task-12-matrix.txt
  ```

  **Commit**: NO

- [x] 13. Propagate `VERSION` into Docker/build helpers and version-aware scripts

  **What to do**:
  - Update Docker/build helper files so version-aware commands can consume `$(cat VERSION)`.
  - Keep Docker behavior otherwise unchanged.
  - Add targeted checks or script tests proving the helper reads the version correctly.
  - Perform the required doc-comment/docstring final pass for any changed helper descriptions/comments.

  **Must NOT do**:
  - Do not redesign the Docker image or deployment flow.
  - Do not add unrelated shell tooling.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: constrained helper-script update.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: lightweight shell assertions should lock behavior first.
  - **Skills Evaluated but Omitted**:
    - `verification-before-completion`: belongs in final verification wave.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: 14, F1-F4
  - **Blocked By**: 1

  **References**:
  - `build.sh` - existing helper to extend for version awareness.
  - `Dockerfile` - version-aware build context, if labels/args are introduced.
  - Root `VERSION` file - canonical source.

  **Acceptance Criteria**:
  - [ ] Helper scripts can read and use the root `VERSION` file.
  - [ ] Docker/build behavior remains otherwise unchanged.
  - [ ] A shell check verifies the helper consumes the expected version string.

  **QA Scenarios**:
  ```
  Scenario: Build helper reads VERSION correctly
    Tool: Bash
    Preconditions: updated helper script exists.
    Steps:
      1. Run the helper in a dry or echo-only mode if available, or run the version extraction snippet directly, capturing output to `.sisyphus/evidence/task-13-build-helper.txt`.
      2. Confirm the reported version equals `$(cat VERSION)` exactly.
    Expected Result: helper consumes the canonical version string with no whitespace issues.
    Failure Indicators: empty version, mismatched version, or helper crash.
    Evidence: .sisyphus/evidence/task-13-build-helper.txt

  Scenario: Docker/build changes stay minimal
    Tool: Bash
    Preconditions: diff exists.
    Steps:
      1. Run `git diff -- Dockerfile build.sh | tee .sisyphus/evidence/task-13-diff.txt`.
      2. Confirm only version-related lines changed.
    Expected Result: Docker/build scope remains tightly constrained.
    Evidence: .sisyphus/evidence/task-13-diff.txt
  ```

  **Commit**: NO

- [x] 14. Add Apache-2.0 LICENSE file and wire licensing references

  **What to do**:
  - Add a root `LICENSE` file containing the full Apache-2.0 license text.
  - Update any primary documentation or metadata references that should mention the repository license.
  - Ensure the chosen license name is written consistently as `Apache-2.0`.
  - Perform the required final doc pass for any changed text files.

  **Must NOT do**:
  - Do not choose a different license.
  - Do not add custom license clauses.

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: license text and related documentation are text-heavy and accuracy-sensitive.
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `visual-explainer`: no visual artifact needed.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: 15, F1-F4
  - **Blocked By**: 1

  **References**:
  - Root repository files (`README.md`, packaging/release docs) - places that may mention license.
  - Official Apache-2.0 text - source for exact license contents.

  **Acceptance Criteria**:
  - [ ] Root `LICENSE` file exists.
  - [ ] License text is Apache-2.0, unmodified.
  - [ ] Any updated docs consistently reference Apache-2.0.

  **QA Scenarios**:
  ```
  Scenario: LICENSE file exists and contains Apache-2.0 text
    Tool: Bash
    Preconditions: LICENSE file has been added.
    Steps:
      1. Run `head -n 20 LICENSE | tee .sisyphus/evidence/task-14-license-head.txt`.
      2. Confirm the output contains `Apache License` and `Version 2.0`.
      3. Run `test -f LICENSE` and capture success in `.sisyphus/evidence/task-14-license-check.txt`.
    Expected Result: LICENSE file exists with recognizable Apache-2.0 header text.
    Failure Indicators: missing file, wrong license name, or modified/custom text.
    Evidence: .sisyphus/evidence/task-14-license-head.txt

  Scenario: Docs reference the selected license consistently
    Tool: Bash
    Preconditions: any docs mentioning the license have been updated.
    Steps:
      1. Run `grep -Rni 'Apache-2.0\|Apache License' README.md docs .github || true | tee .sisyphus/evidence/task-14-license-grep.txt`.
      2. Confirm any references align with the root LICENSE file.
    Expected Result: documentation references are consistent with Apache-2.0.
    Evidence: .sisyphus/evidence/task-14-license-grep.txt
  ```

  **Commit**: NO

- [x] 15. Update repository docs for the Rust CLI, VERSION contract, release behavior, and summary-enriched indexing

  **What to do**:
  - Update README and any relevant docs to explain the Rust CLI, how to build/run it, how `VERSION` and release publishing work, and how summary-enriched indexing/query gating works.
  - Document that summary generation is per indexing request and that summary-backed query behavior is gated by the server-side `USE_CHUNK_MEMORY` flag.
  - Document the release skip behavior for unchanged/existing versions.
  - Ensure docs do not imply any unplanned CLI redesign or backend rewrite.
  - Perform the required final doc pass for clarity and consistency.

  **Must NOT do**:
  - Do not leave stale instructions referring to the Python CLI or to `mgrep` as the supported final binary name.
  - Do not claim automatic release overwrites or automatic version bumps.

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: documentation-heavy task with technical accuracy requirements.
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `visual-explainer`: not necessary unless documentation needs diagrams.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: F1-F4
  - **Blocked By**: 1, 2, 11, 12, 13, 14

  **References**:
  - `README.md` - primary user/developer documentation to update.
  - `mgrep_cli.py` - old CLI behavior reference so docs can describe parity correctly before removal.
  - `server.py`, `api_models.py`, `services/indexing_service.py`, `services/query_service.py` - authoritative sources for new server behavior to document.

  **Acceptance Criteria**:
  - [ ] README/docs describe the Rust CLI as the supported CLI and use the final binary name `m0grep`.
  - [ ] Docs explain `VERSION`-driven release behavior and skip conditions.
  - [ ] Docs explain summary-generation opt-in and `USE_CHUNK_MEMORY` gating clearly.

  **QA Scenarios**:
  ```
  Scenario: Docs accurately describe new CLI and release behavior
    Tool: Bash
    Preconditions: documentation updates are complete.
    Steps:
      1. Run `grep -nE 'Rust CLI|VERSION|GitHub Release|USE_CHUNK_MEMORY|summary' README.md | tee .sisyphus/evidence/task-14-readme-grep.txt`.
      2. Manually inspect the captured lines for accuracy against implemented files.
    Expected Result: README contains accurate references to all new behavior.
    Failure Indicators: stale Python CLI docs, missing VERSION contract, or missing summary-gating docs.
    Evidence: .sisyphus/evidence/task-14-readme-grep.txt

  Scenario: No stale Python CLI primary-usage guidance remains
    Tool: Bash
    Preconditions: docs updated.
    Steps:
      1. Run `grep -Rni 'mgrep_cli.py\|python .*mgrep\|\bmgrep\b' README.md docs || true | tee .sisyphus/evidence/task-14-stale-cli.txt`.
      2. Confirm any remaining hits are historical/migration notes only, not primary instructions, and that supported usage is documented as `m0grep`.
    Expected Result: docs no longer present Python CLI as the supported path.
    Evidence: .sisyphus/evidence/task-14-stale-cli.txt
  ```

  **Commit**: NO

- [x] 16. Remove the Python CLI and obsolete Python CLI tests after Rust parity is proven

  **What to do**:
  - Delete `mgrep_cli.py` and retire/replace `tests/test_cli.py` once the Rust CLI parity suite and release workflow are green.
  - Update any imports, docs, and references so no runtime/test path depends on the removed Python CLI.
  - Ensure parity/integration coverage now points at the Rust CLI where appropriate.
  - Perform the required final doc/comment pass on changed references and migration notes.

  **Must NOT do**:
  - Do not remove the Python CLI before Rust Tasks 6-8 are green.
  - Do not remove server-side Python code unrelated to the CLI entrypoint/tests.

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: focused cleanup task after parity proof exists.
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: cleanup must happen only after the new parity suite proves coverage.
  - **Skills Evaluated but Omitted**:
    - `using-git-worktrees`: execution setup only, not task-specific logic.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: F1-F4
  - **Blocked By**: 2, 3, 6, 7, 8, 12

  **References**:
  - `mgrep_cli.py` - file to remove after parity is established.
  - `tests/test_cli.py` - obsolete Python CLI suite to retire/replace.
  - `tests/test_e2e_parity.py` - existing repo-level parity patterns that should be redirected to the Rust CLI.
  - `README.md` - update references so the removed entrypoint is not documented.

  **Acceptance Criteria**:
  - [ ] `mgrep_cli.py` is removed from the repo.
  - [ ] No active tests import the removed Python CLI.
  - [ ] Rust parity/integration suites remain green after removal and reference `m0grep` as the compiled tool name.

  **QA Scenarios**:
  ```
  Scenario: Python CLI is fully removed with no lingering imports
    Tool: Bash
    Preconditions: cleanup is complete.
    Steps:
      1. Run `grep -Rni 'from mgrep_cli import\|import mgrep_cli\|mgrep_cli.py' . | tee .sisyphus/evidence/task-15-grep.txt`.
      2. Confirm there are no hits outside historical evidence or plan files.
    Expected Result: removed CLI has no live references.
    Failure Indicators: tests/docs/imports still reference the deleted Python CLI.
    Evidence: .sisyphus/evidence/task-15-grep.txt

  Scenario: Rust parity suite stays green after Python CLI removal
    Tool: Bash
    Preconditions: Rust parity suite exists.
    Steps:
      1. Run `cargo test --manifest-path cli/Cargo.toml | tee .sisyphus/evidence/task-15-cargo-test.txt`.
      2. Run the selected pytest integration suite and capture output.
    Expected Result: new CLI remains fully validated after cleanup.
    Evidence: .sisyphus/evidence/task-15-cargo-test.txt
  ```

  **Commit**: NO

---

## Final Verification Wave

> 4 review agents run in parallel. ALL must approve. Present consolidated results to the user and wait for explicit okay before marking work complete.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each Must Have, verify implementation exists by reading files, running the parity suites, and checking release/workflow YAML. For each Must NOT Have, search for forbidden patterns such as Python CLI leftovers, route changes, unconditional summary generation, or release overwrite logic. Confirm evidence files exist in `.sisyphus/evidence/`.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `cargo test --manifest-path cli/Cargo.toml`, `cargo fmt --manifest-path cli/Cargo.toml -- --check`, `cargo clippy --manifest-path cli/Cargo.toml -- -D warnings`, and `pytest tests/test_server.py tests/test_indexing.py tests/test_query_api.py tests/test_integration.py -v`. Review changed files for dead flags, silent catch-all behavior, undocumented public fields, duplicated release logic, and unused imports.
  Output: `Build [PASS/FAIL] | Rust QA [PASS/FAIL] | Pytest [PASS/FAIL] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  From a clean checkout, build the Rust binary, run summary-disabled and summary-enabled indexing scenarios, verify query behavior with and without `USE_CHUNK_MEMORY`, and inspect GitHub workflow skip logic by shell-evaluating the version checks. Save outputs under `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  Compare each task spec to the resulting diff. Verify the Rust CLI only replaces the old contract, the backend remains Python, summary enrichment is opt-in for indexing and gated for query, and release logic skips instead of overwriting. Flag any unplanned CLI feature or backend refactor.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **No agent commits or pushes.** The executing agent must leave all changes uncommitted for developer review.

---

## Success Criteria

### Verification Commands
```bash
cargo test --manifest-path cli/Cargo.toml
pytest tests/test_server.py tests/test_indexing.py tests/test_query_api.py tests/test_integration.py -v
python -m pytest tests/test_e2e_parity.py -v
```

### Final Checklist
- [ ] Rust CLI covers all existing commands with parity-tested behavior
- [ ] GitHub Actions builds and release logic are wired to `VERSION`
- [ ] Docker/build helpers consume `$(cat VERSION)` for version-aware builds
- [ ] Summary generation is opt-in on indexing requests and non-fatal on failure
- [ ] Summary-backed chunk memory participates in query only when `USE_CHUNK_MEMORY` is enabled
- [ ] Python CLI files are removed only after Rust parity is proven
