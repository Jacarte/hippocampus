# Repo Structure + README Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move root-level Python server assets into `server/` and keep the Rust CLI under `cli/`, update all affected paths in scripts, CI, Docker, and tests, then fully restructure the root README.

**Architecture:** Two first-class top-level application directories — `server/` (FastAPI + services + tests) and `cli/` (existing Rust Cargo workspace). Root keeps orchestration concerns only: shell scripts, Docker files, CI workflows, env files, and `README.md`. All commands in docs/scripts stay repo-root oriented (no required `cd` into subdirectories).

**Tech Stack:** Python 3.11 / FastAPI / pytest, Rust / Cargo, Docker / docker-compose, GitHub Actions

**Constraints (from user + mem0):**
- Only the developer may commit or push changes — no agent may commit
- Sisyphus plan files must NOT be committed
- Mandatory docstring/comment pass after any implementation task
- Tests-after strategy; test suite must pass green before marking any task done

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `server/` | New home for all Python server assets |
| Move | `server.py` → `server/server.py` | FastAPI app entry point |
| Move | `api_models.py` → `server/api_models.py` | Pydantic request/response models |
| Move | `services/` → `server/services/` | All service modules |
| Move | `tests/` → `server/tests/` | Full pytest suite |
| Move | `requirements.txt` → `server/requirements.txt` | Python dependencies |
| Modify | `Dockerfile` | Update COPY paths for new layout |
| Modify | `docker-compose.yaml` | Update build context / paths if needed |
| Modify | `start.sh`, `stop.sh`, `status.sh`, `build.sh` | Fix any path references |
| Modify | `.github/workflows/ci.yaml` | Fix Docker build context/Dockerfile path |
| Modify | `.github/workflows/release.yml` | Verify CLI build path still correct |
| Modify | `.dockerignore` | Ensure ignores are still accurate |
| Rewrite | `README.md` | Full restructure: Server + CLI + MCP sections |

---

## Task 1: Move Python server assets into `server/`

**Files:**
- Create: `server/` directory
- Move: `server.py` → `server/server.py`
- Move: `api_models.py` → `server/api_models.py`
- Move: `services/` → `server/services/`
- Move: `tests/` → `server/tests/`
- Move: `requirements.txt` → `server/requirements.txt`

- [ ] **Step 1: Create `server/` directory**

```bash
mkdir server
```

- [ ] **Step 2: Move server assets**

```bash
mv server.py server/server.py
mv api_models.py server/api_models.py
mv services server/services
mv tests server/tests
mv requirements.txt server/requirements.txt
```

- [ ] **Step 3: Verify files are in place**

```bash
ls server/
# Expected: server.py  api_models.py  services/  tests/  requirements.txt
```

---

## Task 2: Fix Python import paths in tests and service modules

Test files currently do `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` to reach the repo root. After the move, `parents[1]` will point to `server/`, which is correct for finding `server.py` and `api_models.py`. But any service-internal imports that reference sibling modules also need to be verified.

**Files:**
- Inspect/Modify: `server/tests/test_server.py` — sys.path depth
- Inspect/Modify: `server/tests/test_*.py` — all test files with sys.path manipulation
- Inspect/Modify: `server/services/mcp_bridge.py` — any direct module imports

- [ ] **Step 1: Check current sys.path depth in all test files**

```bash
grep -rn "sys.path" server/tests/
```

Expected: each file uses `parents[1]` pointing to `server/` which is now the Python root — this should be correct already if tests were importing `from server import ...` and `from services import ...`.

- [ ] **Step 2: Verify `server/tests/test_server.py` imports resolve**

The file currently has:
```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```
After move: `parents[1]` = `server/` — correct, since `server.py` and `services/` are both direct children of `server/`.

No change needed to this line.

- [ ] **Step 3: Check services for cross-module imports**

```bash
grep -rn "^from services\|^import services\|^from api_models\|^import api_models" server/services/ server/server.py
```

All relative imports should resolve fine since `server/` is now the Python root for the app. If any file does `from services.X import Y`, those still work as long as the test runner is invoked from `server/` or with `PYTHONPATH=server`.

- [ ] **Step 4: Verify `server/services/mcp_bridge.py` sys.path**

```bash
head -20 server/services/mcp_bridge.py
```

If it does path manipulation, ensure it still points correctly after the move.

---

## Task 3: Update `Dockerfile` for new layout

**Files:**
- Modify: `Dockerfile`

Current COPY commands:
```dockerfile
COPY server.py .
COPY api_models.py .
COPY tests ./tests
COPY services ./services
```
These must change to copy from the `server/` subdirectory.

- [ ] **Step 1: Update COPY instructions in Dockerfile**

Replace the current COPY block (lines ~40-43) with:

```dockerfile
COPY server/requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt
```

Wait — `requirements.txt` is also COPY'd earlier in the builder stage. Update both COPY references:

Builder stage (line ~12):
```dockerfile
COPY server/requirements.txt .
```

Final stage COPY block:
```dockerfile
COPY server/server.py .
COPY server/api_models.py .
COPY server/tests ./tests
COPY server/services ./services
```

CMD remains:
```dockerfile
CMD ["python", "server.py"]
```

- [ ] **Step 2: Verify Dockerfile syntax is valid**

```bash
docker build --no-cache --dry-run . 2>&1 | head -30
# OR: check manually that every referenced file now exists under server/
```

If `docker build --dry-run` is not available:
```bash
head -60 Dockerfile
# Manually confirm each COPY source path starts with server/
```

---

## Task 4: Update `docker-compose.yaml`

Docker-compose references `dockerfile: Dockerfile` and `context: .`. The context stays `.` (repo root) and the Dockerfile stays at the root — no change needed for those. The `.env` volume mount also stays valid.

**Files:**
- Inspect: `docker-compose.yaml` — confirm no hardcoded paths to server/ assets

- [ ] **Step 1: Verify docker-compose doesn't reference old root paths**

```bash
grep -n "server\.py\|api_models\|services/\|tests/" docker-compose.yaml
```

Expected: no matches. If matches found, update them.

---

## Task 5: Update shell scripts

`start.sh`, `stop.sh`, `status.sh`, and `build.sh` use `docker-compose` commands only — they don't reference Python files directly.

- [ ] **Step 1: Verify scripts don't hardcode old root Python paths**

```bash
grep -n "server\.py\|api_models\|services/\|tests/\|requirements" start.sh stop.sh status.sh build.sh
```

Expected: no matches. Scripts deal with Docker only and should be clean.

---

## Task 6: Update CI workflows

**Files:**
- Modify (if needed): `.github/workflows/ci.yaml` — Docker build context is `.`, Dockerfile at root
- Inspect: `.github/workflows/release.yml` — already uses `cli/Cargo.toml`, should be unaffected

- [ ] **Step 1: Verify `ci.yaml` build context and Dockerfile path**

```bash
grep -n "context\|dockerfile\|Dockerfile" .github/workflows/ci.yaml
```

Current: `context: .` and `dockerfile: Dockerfile`. Both remain valid since Dockerfile stays at root.

Expected: no changes needed. If any path was hardcoded to server files, update it.

- [ ] **Step 2: Verify `release.yml` CLI build path**

```bash
grep -n "cli/\|Cargo.toml\|cargo build" .github/workflows/release.yml
```

Current: `--manifest-path cli/Cargo.toml` — correct, `cli/` stays at root level.

Expected: no changes needed.

---

## Task 7: Update `.dockerignore`

**Files:**
- Inspect/Modify: `.dockerignore`

- [ ] **Step 1: Read current `.dockerignore`**

```bash
cat .dockerignore
```

- [ ] **Step 2: Update any path entries that referenced old root-level server paths**

If `.dockerignore` has entries like `tests/` or `services/`, update them to `server/tests/` and `server/services/` so Docker ignores are still accurate. Keep all CLI-related ignores (`cli/target/`) intact.

---

## Task 8: Run test suite to confirm nothing is broken

- [ ] **Step 1: Install dependencies from the new location**

```bash
pip install -r server/requirements.txt
```

- [ ] **Step 2: Run tests with updated path context**

```bash
cd server && pytest tests/ -v
```

Expected: all tests pass with green output. If tests fail due to import errors, go back to Task 2 and fix the PYTHONPATH.

Alternative invocation from repo root (if PYTHONPATH is needed):
```bash
PYTHONPATH=server pytest server/tests/ -v
```

- [ ] **Step 3: Confirm test count matches pre-refactor baseline**

Before refactor, count was:
```bash
# Baseline count to verify (run this before moving files)
pytest tests/ --collect-only -q 2>&1 | tail -3
```
After move, the count must match.

---

## Task 9: Update root `README.md` — full restructure

Rewrite README with the following structure, updated for the new layout and repo-root command style:

**Target README outline:**
1. Title + one-paragraph overview (what this repo contains: a FastAPI memory server + Rust CLI)
2. **Repository Structure** — tree showing `server/` + `cli/` layout
3. **Quick Start** — Docker-based server start (repo-root commands)
4. **Server** section
   - Backend shape (API surface)
   - Environment variables / configuration
   - Running locally (from repo root via `uvicorn server.server:app` or Docker)
   - Testing (`cd server && pytest tests/ -v` or `PYTHONPATH=server pytest server/tests/ -v`)
   - OpenCode integration
5. **CLI (`m0grep`)** section — installation, commands, examples (unchanged from current)
6. **MCP Integration** section (unchanged from current)
7. **License + Support**

- [ ] **Step 1: Write updated README.md**

Replace the full content of `README.md` with the restructured version. Key changes from current:

- Add **Repository Structure** section with directory tree
- Change `python server.py` → `python server/server.py` (or `cd server && python server.py`)
- Change `uvicorn server:app` → `uvicorn server.server:app --app-dir .` or `cd server && uvicorn server:app`
- Change `pip install -r requirements.txt` → `pip install -r server/requirements.txt`
- Change `pytest tests/ -v` → `cd server && pytest tests/ -v`
- Change all `services/X.py` path references to `server/services/X.py`
- Keep all `m0grep`, MCP, and API contract sections intact (content unchanged, paths updated)
- Remove duplicate or stale information
- Add introductory paragraph clarifying the two-app structure

Write the full updated README content directly (no placeholders, no "TBD").

---

## Task 10: Final verification

- [ ] **Step 1: Run full test suite once more to confirm README changes didn't introduce regressions**

```bash
cd server && pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 2: Confirm Docker build still works**

```bash
docker build -t mem0server-test:refactor . 2>&1 | tail -10
```

Expected: `Successfully built ...` or `CACHED` with no errors.

- [ ] **Step 3: Spot-check README paths are accurate**

```bash
grep -n "server\.py\|api_models\|requirements\.txt\|services/\|tests/" README.md | head -30
```

Confirm every occurrence now reflects the new `server/` prefix correctly.

- [ ] **Step 4: Confirm root directory is clean**

```bash
ls *.py 2>/dev/null || echo "No root-level .py files — correct"
```

Expected: no `.py` files at repo root (they all live in `server/` now).

---

## Final Verification Wave

- [ ] **F1 — Test suite green**: `cd server && pytest tests/ -v` exits 0 with no failures
- [ ] **F2 — Docker build clean**: `docker build -t mem0server-test:refactor .` exits 0
- [ ] **F3 — No root-level Python files**: `ls *.py 2>/dev/null || echo "clean"` returns clean
- [ ] **F4 — README accuracy**: Every path in README.md that mentions `server.py`, `services/`, `tests/`, or `requirements.txt` is prefixed with `server/` or uses a `cd server &&` prefix
