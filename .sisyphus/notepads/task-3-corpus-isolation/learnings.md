
## Task 3 — Corpus isolation services

- `FileCorpusService` uses `\x00`-delimited keys `{root}\x00{file_path}\x00{chunk_id}` to provide O(1) prefix deletion without a nested dict structure.
- `IndexManifestService` uses `@dataclass` with `field(default_factory=...)` matching the rest of the codebase's dataclass style (see `api_models.py`).
- Both services have zero external deps (no mem0 import) — isolation enforced at import level.
- Evidence written to `.sisyphus/evidence/task-3-corpus-isolation.txt` via `python -m pytest` (not `rtk pytest` which strips tee output).
