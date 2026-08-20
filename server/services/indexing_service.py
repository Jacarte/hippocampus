from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from .file_corpus_service import FileCorpusService
from .file_scanner import FileScanner, _EXT_TO_LANG
from .index_manifest_service import IndexManifestService
from .chunkers import CodeChunker, MarkdownChunker
from .metrics import indexing_chunks_total, indexing_files_total
from .summary_service import SummaryService

logger = logging.getLogger(__name__)

_MARKDOWN_EXTS = {".md", ".txt", ".rst"}


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _file_mtime_iso(path: str) -> str:
    return (
        datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _ext(file_path: str) -> str:
    return ("." + file_path.rsplit(".", 1)[-1].lower()) if "." in file_path else ""


class IndexingService:
    """Service that synchronises a directory tree into the file corpus.

    Args:
        corpus: Storage layer for chunk records.
        manifest: Tracks per-file fingerprints and chunk IDs.
        scanner: Scans the filesystem and detects changes.
        memory_instance: Optional mem0 Memory instance used to instantiate
            :class:`~services.summary_service.SummaryService` when
            ``generate_summaries=True`` is passed to :meth:`sync`.  When
            ``None``, summary generation is silently skipped even if
            ``generate_summaries=True``.
    """

    def __init__(
        self,
        corpus: FileCorpusService,
        manifest: IndexManifestService,
        scanner: FileScanner,
        memory_instance: Any = None,
    ) -> None:
        self._corpus = corpus
        self._manifest = manifest
        self._scanner = scanner
        self._memory = memory_instance

    def set_memory_instance(self, memory_instance: Any | None) -> None:
        """Attach or replace the memory instance used for summary generation.

        Called automatically by :func:`~services.runtime.initialize_memory`
        after the server memory is initialised or reconfigured.  When ``None``,
        summary generation is silently skipped even if
        ``generate_summaries=True``.

        Args:
            memory_instance: A configured mem0 ``Memory`` object, or ``None``
                to disable summary generation.
        """
        self._memory = memory_instance

    def iter_manifest_files(self) -> list[tuple[str, Any]]:
        """Public accessor over :meth:`IndexManifestService.iter_file_records`.

        Returns a snapshot of every tracked file as
        ``(file_key, FileRecord)`` pairs so callers outside the service
        (notably :class:`AdminService` when building the index overview)
        can iterate per-file state without reaching into the private
        ``_manifest._files`` dict.  ``file_key`` is the
        ``"{root}\\x00{file_path}"`` composite used by the manifest.

        Returns:
            A fresh list of ``(file_key, FileRecord)`` tuples; mutating
            the result does not change the underlying manifest.
        """
        return self._manifest.iter_file_records()

    def file_has_summary_embedding(self, root: str, file_path: str) -> bool:
        """Return ``True`` when the corpus has a summary embedding for the file.

        Thin pass-through to
        :meth:`FileCorpusService.has_summary_embedding` so the admin
        overview can populate :class:`AdminIndexFileInfo`'s
        ``has_summary_embedding`` flag without depending on the
        internal ``_corpus`` attribute.  Returns ``False`` for files
        that have no chunks or no summary embeddings.

        Args:
            root: Namespace the file is stored under.
            file_path: Relative file path within *root*.

        Returns:
            ``True`` if any chunk for the file has a non-empty
            ``summary_embedding``; ``False`` otherwise.
        """
        return self._corpus.has_summary_embedding(root, file_path)

    def sync(self, root: str, generate_summaries: bool = False) -> dict[str, Any]:
        """Synchronise *root* into the corpus and manifest.

        Reads files directly from the server's filesystem, so *root* must be a
        path that is accessible to the server process, including from inside its
        container when applicable.  Files that were previously indexed but are
        no longer present on disk are removed from the corpus.  The namespace
        key is always *root* itself.

        Args:
            root: Absolute path to the directory tree to index.  Must be
                accessible to the server process at call time.
            generate_summaries: When ``True`` and a memory instance is
                available, call :class:`~services.summary_service.SummaryService`
                for each indexed chunk and store the resulting
                ``summary_text`` / ``summary_embedding`` fields.  Any
                failure during summary generation logs a warning and leaves
                the chunk stored without summary fields.  Defaults to
                ``False`` (no summaries generated).

        Returns:
            A dict with keys ``root``, ``files_indexed``, ``chunks_indexed``,
            ``synced_at``, and ``errors``.  An unchanged tree succeeds with zero
            indexed counts.  Per-file failures are collected in ``errors`` and
            do not prevent other changed files from being processed.  Indexing
            counters advance only when at least one file is indexed.
        """
        self._manifest.register_root(root)

        current_files = self._scanner.scan(root)

        prev_fingerprints: dict[str, str] = {}
        for file_info in current_files:
            record = self._manifest.get_file_record(root, file_info["file_path"])
            if record is not None:
                prev_fingerprints[file_info["file_path"]] = record.fingerprint

        for file_key, record in self._manifest._files.items():
            if file_key.startswith(f"{root}\x00"):
                fp = file_key[len(root) + 1 :]
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

                indexed_at = _file_mtime_iso(abs_path)
                for chunk in chunks:
                    chunk.setdefault("indexed_at", indexed_at)

                if generate_summaries and self._memory is None:
                    logger.warning(
                        "generate_summaries=True but memory instance is not configured; "
                        "chunks will be indexed without summaries."
                    )
                if generate_summaries and self._memory is not None:
                    summary_svc = SummaryService(self._memory)
                    for chunk in chunks:
                        try:
                            result = summary_svc.generate_summary(
                                chunk.get("content", ""),
                                chunk.get("symbol_name") or "",
                            )
                            text = result.summary_text
                            if text and text.strip():
                                chunk["summary_text"] = text
                                chunk["summary_embedding"] = result.summary_embedding
                        except Exception as exc:
                            logger.warning(
                                "Summary generation failed for chunk in %s: %s",
                                file_path,
                                exc,
                            )

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

        if files_indexed > 0:
            indexing_files_total.labels(operation="sync").inc(files_indexed)
            indexing_chunks_total.labels(operation="sync").inc(chunks_indexed)

        return {
            "root": root,
            "files_indexed": files_indexed,
            "chunks_indexed": chunks_indexed,
            "synced_at": _now_iso(),
            "errors": errors,
        }

    def status(self) -> dict[str, Any]:
        """Return a point-in-time summary of the in-memory manifest and corpus.

        The result contains root records, total manifest files, total corpus
        chunks, and the latest root ``indexed_at`` value.  It does not report
        background-job progress or watcher thread state; the HTTP status handler
        adds recent job errors separately.  With no registered roots,
        ``last_synced_at`` is ``None`` and totals are zero.
        """
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
        """Clear the in-memory corpus and manifest and report removed totals.

        The operation is idempotent: resetting empty state returns zero counts.
        It does not stop watcher threads or alter background-job records, so
        callers that require a durable empty index must stop watchers separately.
        """
        corpus_result = self._corpus.reset()
        files_cleared = self._manifest.get_status()["total_files"]
        self._manifest.reset()

        return {
            "files_cleared": files_cleared,
            "chunks_cleared": corpus_result["cleared_chunks"],
            "reset_at": _now_iso(),
        }
