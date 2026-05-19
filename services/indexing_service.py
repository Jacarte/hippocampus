from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from services.file_corpus_service import FileCorpusService
from services.file_scanner import FileScanner, _EXT_TO_LANG
from services.index_manifest_service import IndexManifestService
from services.chunkers import CodeChunker, MarkdownChunker

_MARKDOWN_EXTS = {".md", ".txt", ".rst"}


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _ext(file_path: str) -> str:
    return ("." + file_path.rsplit(".", 1)[-1].lower()) if "." in file_path else ""


class IndexingService:
    def __init__(
        self,
        corpus: FileCorpusService,
        manifest: IndexManifestService,
        scanner: FileScanner,
    ) -> None:
        self._corpus = corpus
        self._manifest = manifest
        self._scanner = scanner

    def sync(self, root: str) -> dict[str, Any]:
        self._manifest.register_root(root)

        current_files = self._scanner.scan(root)

        prev_fingerprints: dict[str, str] = {}
        for file_info in current_files:
            record = self._manifest.get_file_record(root, file_info["file_path"])
            if record is not None:
                prev_fingerprints[file_info["file_path"]] = record.fingerprint

        for file_key, record in self._manifest._files.items():
            if file_key.startswith(f"{root}\x00"):
                fp = file_key[len(root) + 1:]
                if fp not in prev_fingerprints:
                    prev_fingerprints[fp] = record.fingerprint

        diff = self._scanner.diff(root, prev_fingerprints)

        current_by_path = {f["file_path"]: f for f in current_files}

        files_indexed = 0
        chunks_indexed = 0
        errors: list[str] = []

        for file_path in diff["created"] + diff["updated"]:
            abs_path = os.path.join(root, file_path)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()

                ext = _ext(file_path)
                if ext in _MARKDOWN_EXTS:
                    chunks = MarkdownChunker().chunk(file_path, content)
                else:
                    language = _EXT_TO_LANG.get(ext, "unknown")
                    chunks = CodeChunker().chunk(file_path, content, language)

                self._corpus.upsert_chunks(root, file_path, chunks)

                chunk_ids = [c.get("id", str(i)) for i, c in enumerate(chunks)]
                fingerprint = current_by_path[file_path]["fingerprint"]
                self._manifest.update_file(root, file_path, fingerprint, chunk_ids)

                files_indexed += 1
                chunks_indexed += len(chunks)
            except Exception as exc:
                errors.append(f"{file_path}: {exc}")

        for file_path in diff["deleted"]:
            try:
                self._corpus.delete_file(root, file_path)
                self._manifest.remove_file(root, file_path)
            except Exception as exc:
                errors.append(f"{file_path}: {exc}")

        return {
            "root": root,
            "files_indexed": files_indexed,
            "chunks_indexed": chunks_indexed,
            "synced_at": _now_iso(),
            "errors": errors,
        }

    def status(self) -> dict[str, Any]:
        manifest_status = self._manifest.get_status()
        corpus_status = self._corpus.get_status()

        last_synced_at: str | None = None
        roots_list = []
        for root_info in manifest_status["roots"].values():
            roots_list.append(root_info)
            indexed_at = root_info.get("indexed_at")
            if indexed_at and (last_synced_at is None or indexed_at > last_synced_at):
                last_synced_at = indexed_at

        return {
            "roots": roots_list,
            "total_files": manifest_status["total_files"],
            "total_chunks": corpus_status["total_chunks"],
            "last_synced_at": last_synced_at,
        }

    def reset(self) -> dict[str, Any]:
        corpus_result = self._corpus.reset()
        files_cleared = self._manifest.get_status()["total_files"]
        self._manifest.reset()

        return {
            "files_cleared": files_cleared,
            "chunks_cleared": corpus_result["cleared_chunks"],
            "reset_at": _now_iso(),
        }
