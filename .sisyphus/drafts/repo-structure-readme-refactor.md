# Draft: Repo Structure + README Refactor

## Requirements (confirmed)
- User wants a plan to separate current root-level server and cargo applications into two different folders.
- User wants README to be refactored accordingly.
- Path migration should be comprehensive (scripts/CI/config/docs updated to new locations).
- Target layout should be `server/` + `cli/`.
- Automated test work should be included as tests-after implementation tasks.
- README should be fully restructured (not just minimal path edits).

## Technical Decisions
- Planning mode only (no implementation in this session).
- Keep output as a single plan file under `.sisyphus/plans/` once requirements are fully clear.
- Target top-level layout selected: `server/` + `cli/`.
- Command style preference: repo-root oriented commands for docs and scripts.

## Research Findings
- Current repo has root-level Python server files (`server.py`, `api_models.py`, `services/`, `tests/`, `requirements.txt`, `Dockerfile`).
- Current repo has root-level Rust CLI app in `cli/` (Cargo app present in repo root context).
- Current repo has root-level `README.md` that will need section/path updates after restructuring.
- CI/release workflows exist under `.github/workflows/ci.yaml` and `.github/workflows/release.yml`; release workflow already builds CLI using `cli/Cargo.toml`.
- Docker and ops scripts (`Dockerfile`, `docker-compose.yaml`, `start.sh`, `stop.sh`, `status.sh`, `build.sh`) currently assume server assets at repo root.
- README contains many root-path assumptions (`python server.py`, `uvicorn server:app`, `pip install -r requirements.txt`, `services/...`, `tests/...`) and will require broad path updates.

## Scope Boundaries
- INCLUDE: directory restructuring plan and README refactor plan.
- EXCLUDE: direct code/file implementation in this planning session.

## Open Questions
- Should server folder include its own local README (`server/README.md`) in addition to the root README, or keep all docs centralized in root README?
