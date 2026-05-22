# Scope Fidelity Report
**Date:** 2026-05-19  
**Plan:** rust-cli-and-summary-indexing  
**Auditor:** Executor (F4 Final Verification Wave)

---

## 1. Deleted File Verification

| File | Expected | Actual | Status |
|---|---|---|---|
| `mgrep_cli.py` | DELETED | DELETED | ✅ |
| `tests/test_cli.py` | DELETED | DELETED | ✅ |

**Note:** `tests/__pycache__/test_cli.cpython-310-pytest-8.4.1.pyc` bytecode artifact remains. This is a Python build artifact (not source) that is not imported by any surviving test and will be regenerated on next run. It does NOT constitute a residual CLI import. Harmless.

---

## 2. Residual `mgrep_cli` Imports in Tests

```
grep -rn "from mgrep_cli|import mgrep_cli" tests/ → EXIT 1 (no matches)
```

✅ **CLEAN** — no surviving Python imports of the old CLI module.

---

## 3. Rust CLI Command Scope (5-command contract)

Files in `cli/src/commands/`:

```
mod.rs      ← module aggregator (required by Rust, not a command)
query.rs    ← command 1
sync.rs     ← command 2
watch.rs    ← command 3
status.rs   ← command 4
reset.rs    ← command 5
```

✅ Exactly 5 commands: `query`, `sync`, `watch`, `status`, `reset`. `mod.rs` is a Rust module file, not a new command. **No unplanned commands.**

---

## 4. Python Backend Architecture

- Framework: `FastAPI` — `from fastapi import FastAPI` confirmed in `server.py` lines 7–8.
- `app = FastAPI(...)` on line 56.
- No framework swap detected.
- All existing routes remain; new routes added (sync, query, status, reset, watch) but no removals.

✅ **Python FastAPI backend unchanged in architecture.**

---

## 5. `generate_summaries` — Opt-in, Not Hardcoded

- `api_models.py` lines 93, 133, 144: `generate_summaries: bool = Field(...)` with default `False`.
- `services/indexing_service.py` line 57: `generate_summaries: bool = False` parameter default.
- `server.py` line 271: `generate_summaries=sync_req.generate_summaries` — passes caller value, no override.
- Grep `generate_summaries=True` in `services/` → **0 hits** (only in docstrings at lines 40/42, not code).

✅ **Opt-in only. No hardcoded `True`.**

---

## 6. `USE_CHUNK_MEMORY` Environment Gate

- `services/runtime.py` line 170: `os.environ.get("USE_CHUNK_MEMORY", "")` — reads env var.
- `server.py` lines 37, 251, 261: `is_chunk_memory_enabled()` called and result passed to query service.
- `tests/test_server.py` lines 2037, 2042, 2045: tests exercise env var toggle.

✅ **Server-side gate via `USE_CHUNK_MEMORY` env var confirmed.**

---

## 7. CI Release Workflow — Skip Logic

`.github/workflows/release.yml`:

- `check-release` job queries GitHub Releases API for existing tag.
- If release exists: sets `already-released=true`, emits `"skipping publish"`.
- `build` job: `if: needs.check-release.outputs.already-released == 'false'`
- `publish` job: same guard.

✅ **Skip (not fail, not overwrite) on version match confirmed.**

---

## 8. New Files Added — Scope Accounting

**Expected new files (per spec):**

| File/Path | Present | Notes |
|---|---|---|
| `cli/` (Rust crate) | ✅ | Full crate with 5 commands |
| `VERSION` | ✅ | |
| `rust-toolchain.toml` | ✅ | |
| `LICENSE` | ✅ | |
| `.github/workflows/release.yml` | ✅ | |
| `services/summary_service.py` | ✅ | |
| `README.md` (changed) | ✅ | |

**Additional files changed in scope (legitimate additions):**

| File | Reason |
|---|---|
| `api_models.py` | New request/response models for 5 CLI commands |
| `server.py` | New route handlers for CLI commands |
| `services/indexing_service.py` | New indexing logic |
| `services/query_service.py` | New query logic |
| `services/runtime.py` | `USE_CHUNK_MEMORY` gate utility |
| `services/watch_service.py` | Watch command backend |
| `services/file_corpus_service.py` | File corpus support |
| `services/file_scanner.py` | File scanning |
| `services/index_manifest_service.py` | Manifest management |
| `services/chunkers/` | Chunking infrastructure |
| `services/mcp_bridge.py` | MCP bridge |
| `tests/test_*.py` (new) | Tests for all new services |
| `tests/fixtures/` | Test data |
| `.gitignore`, `Dockerfile`, `build.sh`, `requirements.txt` | Build/infra support |
| `logs.txt` | Runtime artifact (not a code change) |

All additions are directly traceable to the 16 implementation tasks. No extraneous backend refactors or framework changes detected.

---

## 9. Unplanned Files / Contamination

- No unplanned CLI commands beyond the 5-command contract.
- No Python CLI remnants in source (only harmless `.pyc` artifact).
- No architectural changes to Python backend.
- No hardcoded summary generation.
- No missing chunk-memory gate.
- No overwrite risk in CI (skip logic confirmed).

---

## Summary

| Check | Result |
|---|---|
| `mgrep_cli.py` deleted | ✅ |
| `tests/test_cli.py` deleted | ✅ |
| No residual `mgrep_cli` imports in tests | ✅ |
| CLI has exactly 5 commands | ✅ |
| Python backend still FastAPI | ✅ |
| `generate_summaries` opt-in (default False) | ✅ |
| `USE_CHUNK_MEMORY` env gate present | ✅ |
| CI skips on existing release | ✅ |
| No unplanned backend refactors | ✅ |
| All changed files within task scope | ✅ |

---

**Tasks [16/16 compliant] | Contamination [CLEAN] | Unaccounted [CLEAN] | VERDICT: APPROVE**
