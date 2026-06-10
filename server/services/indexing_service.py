from __future__ import annotations

import hashlib
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

    def sync(self, root: str, generate_summaries: bool = False) -> dict[str, Any]:
        """Synchronise *root* into the corpus and manifest.

        Reads files directly from the server's local filesystem, so *root* must
        be a path that is accessible to the server process.  This makes it
        suitable only for co-located (same-machine) use.  For remote or
        cross-machine indexing, use :meth:`ingest` instead, which accepts file
        contents supplied by the caller rather than reading them from disk.

        Unlike :meth:`ingest`, this method also handles deletions: files that
        were previously indexed but are no longer present on disk are removed
        from the corpus.  The namespace key is always *root* itself; there is no
        ``project_id`` override here.

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
            ``synced_at``, and ``errors``.
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

    def ingest(
        self,
        root: str,
        files: list[dict[str, str]],
        generate_summaries: bool = False,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Ingest pre-read file contents into the corpus.

        Unlike :meth:`sync`, this method does not touch the filesystem — the
        caller supplies file contents directly.  This makes it safe to use with
        a remote server that has no access to the client's filesystem.

        Args:
            root: Logical namespace label for this batch (e.g. the project root
                on the client machine).  Does not need to exist on the server.
            files: List of dicts, each with ``file_path`` (str) and ``content``
                (str) keys.
            generate_summaries: When ``True`` and a memory instance is available,
                generate LLM chunk summaries.  Silently skipped when memory is
                not configured.
            project_id: Optional stable project identifier. When provided, used
                as the corpus namespace instead of *root*, ensuring that chunks
                from the same project indexed from different machines or paths
                do not overlap with other projects.

        Returns:
            Dict with ``root``, ``files_indexed``, ``chunks_indexed``,
            ``ingested_at``, and ``errors`` keys.
        """
        namespace = project_id if project_id else root
        self._manifest.register_root(namespace)

        files_indexed = 0
        chunks_indexed = 0
        errors: list[str] = []

        for file_entry in files:
            file_path: str = file_entry["file_path"]
            content: str = file_entry["content"]
            try:
                ext = _ext(file_path)
                if ext in _MARKDOWN_EXTS:
                    chunks = MarkdownChunker().chunk(file_path, content)
                else:
                    language = _EXT_TO_LANG.get(ext, "unknown")
                    chunks = CodeChunker().chunk(file_path, content, language)

                indexed_at = _now_iso()
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

                self._corpus.upsert_chunks(namespace, file_path, chunks)

                chunk_ids = [c.get("id", str(i)) for i, c in enumerate(chunks)]
                fingerprint = hashlib.sha256(
                    content.encode("utf-8", errors="replace")
                ).hexdigest()
                self._manifest.update_file(namespace, file_path, fingerprint, chunk_ids)

                files_indexed += 1
                chunks_indexed += len(chunks)
            except Exception as exc:
                errors.append(f"{file_path}: {exc}")

        if files_indexed > 0:
            indexing_files_total.labels(operation="ingest").inc(files_indexed)
            indexing_chunks_total.labels(operation="ingest").inc(chunks_indexed)

        return {
            "root": root,
            "files_indexed": files_indexed,
            "chunks_indexed": chunks_indexed,
            "ingested_at": _now_iso(),
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

    def file_chunks(
        self,
        file_path: str,
        root: str | None = None,
        include_embeddings: bool = False,
    ) -> dict[str, Any]:
        """Return all indexed chunks for *file_path*, plus its manifest record.

        When *root* is given, only that root namespace is searched.  When None,
        all known roots are searched and their chunks are concatenated.
        The manifest record (fingerprint, last_indexed_at, chunk_count) is
        included from the first root that has a record for the file; None when
        no manifest entry exists (e.g. the file was removed after indexing).

        Args:
            file_path: Relative path of the file as stored in the index
                (e.g. ``"src/main.py"``).
            root: Root namespace to scope the search.  Pass ``None`` to search
                across every registered root.
            include_embeddings: When ``True`` each chunk carries the raw
                ``summary_embedding`` vector.  When ``False`` (default) the
                vector is replaced by a boolean ``has_summary_embedding`` to
                keep the response compact.  Delegates directly to
                :meth:`FileCorpusService.get_file_chunks`.

        Returns:
            A dict with keys:

            - ``file_path`` – echoed from the argument.
            - ``root`` – echoed from the argument (may be ``None``).
            - ``manifest`` – dict with ``root``, ``fingerprint``,
              ``last_indexed_at``, and ``chunk_count``; or ``None`` when no
              manifest entry exists for the file.
            - ``chunk_count`` – total number of chunks returned.
            - ``chunks`` – list of chunk dicts; see
              :meth:`FileCorpusService.get_file_chunks` for the exact shape.
        """
        roots_to_search = (
            [root] if root is not None else list(self._manifest._roots.keys())
        )

        all_chunks: list[dict[str, Any]] = []
        manifest_info: dict[str, Any] | None = None

        for r in roots_to_search:
            chunks = self._corpus.get_file_chunks(
                r, file_path, include_embeddings=include_embeddings
            )
            all_chunks.extend(chunks)
            if manifest_info is None:
                record = self._manifest.get_file_record(r, file_path)
                if record is not None:
                    manifest_info = {
                        "root": r,
                        "fingerprint": record.fingerprint,
                        "last_indexed_at": record.last_indexed_at,
                        "chunk_count": len(record.chunk_ids),
                    }

        return {
            "file_path": file_path,
            "root": root,
            "manifest": manifest_info,
            "chunk_count": len(all_chunks),
            "chunks": all_chunks,
        }
