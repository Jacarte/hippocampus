# Draft: Rust CLI and Summary Indexing

## Requirements (confirmed)
- Migrate the CLI from Python to Rust.
- Target output is one single binary.
- No Python required for the CLI.
- CLI remains a thin HTTP client.
- CI must build binaries for Linux and macOS.
- CI must publish release artifacts to the GitHub Releases channel.
- Add a repository LICENSE file as part of the task.
- Add a server-side flag when indexing/adding files so the model can generate summaries for indexed objects (for example functions/classes without docs).
- When summary generation is enabled, store both the natural-language summary text and its embeddings alongside the chunk/object data.
- Query behavior should use those summary embeddings/text in addition to existing natural-text matching.

## Technical Decisions
- License choice: add an `Apache-2.0` LICENSE file.
- New CLI tool name: `m0grep`.
- CLI compatibility: preserve the current CLI contract exactly (same commands/flags/output behavior unless later explicitly changed).
- Release trigger: publish release artifacts when changes are merged to `main`.
- Target binaries: Linux amd64, macOS amd64, and macOS arm64.
- Version source of truth: add a root-level `VERSION` file; release/versioned build logic must read from it, and Docker workflows should be able to consume it via `$(cat VERSION)`.
- If `VERSION` was not bumped or that version already exists as a GitHub Release, CI should skip publishing rather than fail or overwrite.
- Migration cutoff: remove the Python CLI entirely after the Rust CLI lands; no long-term fallback.
- Approved implementation approach: keep the Python server in this repo, add an in-repo Rust CLI, do a direct cutover after parity, and publish release artifacts from this same repository.
- Summary generation control: per indexing request only; no server-wide default.
- Summary-aware query behavior: use generated summary text/embeddings only when a server-side flag is enabled (example given: `USE_CHUNK_MEMORY`).

## Proposed Design (working)
- Repository architecture: existing Python backend remains; add a Rust CLI crate/module in-repo as the sole distributed CLI artifact.
- CLI scope: preserve current commands (`query`, `sync`, `watch`, `status`, `reset`) and current default URL/env behavior.
- Release scope: GitHub Actions builds target binaries on merge to `main` and publishes them to GitHub Releases using the root `VERSION` file.
- Indexing scope: server gains an opt-in summary-generation path for indexed code objects, persisting both summary text and summary embeddings for later retrieval/query use.

## Test Strategy Decision
- Infrastructure exists: YES (`pytest`, current CLI tests in `tests/test_cli.py`).
- Automated test approach: YES — TDD.
- Agent-executed QA: required in final plan.

## Research Findings
- Current CLI entrypoint is `mgrep_cli.py` using Typer + requests.
- Current commands: `query`, `sync`, `watch`, `status`, `reset`.
- CLI default backend URL is `http://localhost:8000` or `MEM0_SERVER_URL`.
- Existing CLI tests live in `tests/test_cli.py` and validate help text, request payloads, endpoint paths, error handling, and reset confirmation.
- Repository currently has no `go.mod` and no GitHub Actions workflow files.
- Current build helper `build.sh` only builds Docker image; no release automation exists.

## Open Questions
- What versioning/release naming scheme should be used for releases produced from merges to `main`?

## Scope Boundaries
- INCLUDE: Rust CLI migration, single-binary distribution, CI build/release pipeline, shared VERSION-file-based versioning for release/Docker usage.
- INCLUDE: optional LLM-generated summary generation during file/object indexing, storage of summary text + embeddings, and retrieval/query integration for those summaries.
- EXCLUDE: Server rewrite to Rust or broader backend port unless explicitly requested.
