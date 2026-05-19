from __future__ import annotations

import uuid
from typing import Any


class FileCorpusService:
    """In-memory file/doc chunk store, isolated from the mem0 Memory namespace."""

    def __init__(self) -> None:
        self._chunks: dict[str, dict[str, Any]] = {}

    def upsert_chunks(
        self,
        root: str,
        file_path: str,
        chunks: list[dict[str, Any]],
    ) -> None:
        self.delete_file(root, file_path)
        for chunk in chunks:
            chunk_id = str(chunk.get("id") or uuid.uuid4())
            record: dict[str, Any] = {
                "id": chunk_id,
                "root": root,
                "file_path": file_path,
                "language": chunk.get("language"),
                "symbol_name": chunk.get("symbol_name"),
                "symbol_kind": chunk.get("symbol_kind"),
                "line_start": chunk.get("line_start"),
                "line_end": chunk.get("line_end"),
                "content": chunk.get("content", ""),
                "score": 0.0,
            }
            storage_key = f"{root}\x00{file_path}\x00{chunk_id}"
            self._chunks[storage_key] = record

    def delete_file(self, root: str, file_path: str) -> None:
        prefix = f"{root}\x00{file_path}\x00"
        keys_to_remove = [k for k in self._chunks if k.startswith(prefix)]
        for key in keys_to_remove:
            del self._chunks[key]

    def query(
        self,
        query_text: str,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        lowered_query = query_text.lower()

        for chunk in self._chunks.values():
            content: str = chunk.get("content") or ""
            if lowered_query and lowered_query not in content.lower():
                continue
            if filters:
                match = all(
                    str(chunk.get(field)) == str(value)
                    for field, value in filters.items()
                )
                if not match:
                    continue
            results.append(dict(chunk))
            if len(results) >= limit:
                break

        return results

    def reset(self) -> dict[str, Any]:
        cleared_count = len(self._chunks)
        self._chunks.clear()
        return {"cleared_chunks": cleared_count}

    def get_status(self) -> dict[str, Any]:
        root_counts: dict[str, int] = {}
        file_set: set[str] = set()
        for chunk in self._chunks.values():
            root: str = chunk["root"]
            root_counts[root] = root_counts.get(root, 0) + 1
            file_set.add(f"{chunk['root']}\x00{chunk['file_path']}")

        return {
            "total_chunks": len(self._chunks),
            "total_files": len(file_set),
            "roots": root_counts,
        }
