"""Unified cross-corpus query service.

Queries the memory store and/or file/doc corpus independently, normalises
results to shared hit shapes, fuses them into one ranked response, and
reports truthful provenance and degradation.
"""
from __future__ import annotations

from typing import Any

from api_models import FileHit, MemoryHit, UnifiedQueryResponse
from services.file_corpus_service import FileCorpusService


class QueryService:
    """Fuses results from the memory store and the file corpus."""

    def __init__(
        self,
        corpus: FileCorpusService,
        retrieval_service: Any,
    ) -> None:
        self._corpus = corpus
        self._retrieval = retrieval_service

    def query(
        self,
        query_text: str,
        corpora: list[str],
        limit: int = 10,
        path_filter: str | None = None,
        language_filter: str | None = None,
        scope_filter: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a fused cross-corpus query.

        Args:
            query_text: The text to search for.
            corpora: Which corpora to query.  Valid values are
                ``"memory_store"``, ``"file_corpus"``, and ``"all"``.
                ``"all"`` is expanded to both stores.
            limit: Maximum number of hits to return across all corpora.
            path_filter: Optional file-path substring filter applied to
                the file corpus.
            language_filter: Optional programming-language filter applied
                to the file corpus.
            scope_filter: Reserved for future use; not yet consumed by
                the file corpus.
            user_id: Optional user identifier forwarded to the memory
                store retrieval call.

        Returns:
            A dict matching the UnifiedQueryResponse shape containing
            fused, ranked hits and degradation metadata.
        """
        expanded: list[str] = _expand_corpora(corpora)

        all_hits: list[FileHit | MemoryHit] = []
        corpora_queried: list[str] = []
        degraded = False
        degradation_reasons: list[str] = []

        if "file_corpus" in expanded:
            corpora_queried.append("file_corpus")
            try:
                all_hits.extend(
                    self._query_file_corpus(
                        query_text,
                        path_filter=path_filter,
                        language_filter=language_filter,
                        limit=limit,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                degraded = True
                degradation_reasons.append(f"file_corpus: {exc}")

        if "memory_store" in expanded:
            corpora_queried.append("memory_store")
            try:
                all_hits.extend(
                    self._query_memory_store(query_text, limit=limit, user_id=user_id)
                )
            except Exception as exc:  # noqa: BLE001
                degraded = True
                degradation_reasons.append(f"memory_store: {exc}")

        all_hits.sort(key=lambda h: h.score, reverse=True)
        truncated = all_hits[:limit]

        return UnifiedQueryResponse(
            hits=truncated,
            total=len(all_hits),
            corpora_queried=corpora_queried,
            degraded=degraded,
            degradation_reasons=degradation_reasons,
        ).model_dump()

    def _query_file_corpus(
        self,
        query_text: str,
        *,
        path_filter: str | None,
        language_filter: str | None,
        limit: int,
    ) -> list[FileHit]:
        filters: dict[str, Any] = {}
        if path_filter is not None:
            filters["file_path"] = path_filter
        if language_filter is not None:
            filters["language"] = language_filter

        raw: list[dict[str, Any]] = self._corpus.query(
            query_text, filters=filters or None, limit=limit
        )

        return [
            FileHit.model_validate(
                {
                    "path": chunk.get("file_path", ""),
                    "language": chunk.get("language") or "",
                    "symbol_name": chunk.get("symbol_name"),
                    "symbol_kind": chunk.get("symbol_kind"),
                    "line_start": chunk.get("line_start") or 0,
                    "line_end": chunk.get("line_end") or 0,
                    "snippet": chunk.get("content", ""),
                    "score": float(chunk.get("score", 0.0)),
                    "corpus": "file_corpus",
                }
            )
            for chunk in raw
        ]

    def _query_memory_store(
        self,
        query_text: str,
        *,
        limit: int,
        user_id: str | None,
    ) -> list[MemoryHit]:
        kwargs: dict[str, Any] = {"query": query_text, "limit": limit}
        if user_id is not None:
            kwargs["user_id"] = user_id

        raw: list[dict[str, Any]] = self._retrieval.search(**kwargs)

        return [
            MemoryHit.model_validate(
                {
                    "memory_id": str(result.get("id", "")),
                    "content": result.get("memory", ""),
                    "score": float((result.get("_retrieval") or {}).get("score", 0.0)),
                    "corpus": "memory_store",
                    "metadata": result.get("metadata"),
                }
            )
            for result in raw
        ]


def _expand_corpora(corpora: list[str]) -> list[str]:
    if "all" in corpora:
        return ["memory_store", "file_corpus"]
    return list(dict.fromkeys(corpora))
