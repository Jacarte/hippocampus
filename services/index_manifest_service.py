from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


@dataclass
class RootManifest:
    root_path: str
    indexed_at: str = field(default_factory=_now_iso)
    file_count: int = 0
    chunk_count: int = 0
    watching: bool = False


@dataclass
class FileRecord:
    file_path: str
    fingerprint: str
    last_indexed_at: str = field(default_factory=_now_iso)
    chunk_ids: list[str] = field(default_factory=list)


class IndexManifestService:
    def __init__(self) -> None:
        self._roots: dict[str, RootManifest] = {}
        self._files: dict[str, FileRecord] = {}

    def register_root(self, root: str) -> None:
        if root not in self._roots:
            self._roots[root] = RootManifest(root_path=root)

    def update_file(
        self,
        root: str,
        file_path: str,
        fingerprint: str,
        chunk_ids: list[str],
    ) -> None:
        self.register_root(root)
        file_key = f"{root}\x00{file_path}"
        existing = self._files.get(file_key)
        self._files[file_key] = FileRecord(
            file_path=file_path,
            fingerprint=fingerprint,
            last_indexed_at=_now_iso(),
            chunk_ids=list(chunk_ids),
        )

        root_manifest = self._roots[root]
        if existing is None:
            root_manifest.file_count += 1
        else:
            root_manifest.chunk_count -= len(existing.chunk_ids)
        root_manifest.chunk_count += len(chunk_ids)
        root_manifest.indexed_at = _now_iso()

    def get_file_record(self, root: str, file_path: str) -> "FileRecord | None":
        return self._files.get(f"{root}\x00{file_path}")

    def remove_file(self, root: str, file_path: str) -> None:
        file_key = f"{root}\x00{file_path}"
        existing = self._files.pop(file_key, None)
        if existing is None:
            return
        if root in self._roots:
            root_manifest = self._roots[root]
            root_manifest.file_count = max(0, root_manifest.file_count - 1)
            root_manifest.chunk_count = max(
                0, root_manifest.chunk_count - len(existing.chunk_ids)
            )

    def get_status(self) -> dict[str, Any]:
        return {
            "roots": {
                root: {
                    "root_path": rm.root_path,
                    "indexed_at": rm.indexed_at,
                    "file_count": rm.file_count,
                    "chunk_count": rm.chunk_count,
                    "watching": rm.watching,
                }
                for root, rm in self._roots.items()
            },
            "total_files": len(self._files),
        }

    def reset(self) -> None:
        self._roots.clear()
        self._files.clear()
