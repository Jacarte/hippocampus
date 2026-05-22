"""Unified cross-corpus query service.

Queries the memory store and/or file/doc corpus independently, normalises
results to shared hit shapes, fuses them into one ranked response, and
reports truthful provenance and degradation.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from api_models import FileHit, MemoryHit, UnifiedQueryResponse
from .file_corpus_service import FileCorpusService


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
        chunk_memory_enabled: bool = False,
        query_embedding: list[float] | None = None,
        memory_instance: Any | None = None,
        min_score_memory: float = 0.5,
        min_score_files: float = 0.05,
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
            chunk_memory_enabled: When ``True``, file-corpus retrieval
                also consults ``summary_embedding`` fields on chunks via
                semantic similarity.  When ``False`` (default), behavior is
                identical to the previous lexical-only baseline.
            query_embedding: Optional pre-computed vector embedding of
                *query_text*.  When ``None`` and *chunk_memory_enabled* is
                ``True``, the embedding is derived automatically from
                *memory_instance* (``memory_instance.embedding_model.embed``).
                If embedding derivation fails, a warning is logged and the
                query falls back to lexical-only results.  Ignored when
                *chunk_memory_enabled* is ``False``.
            min_score_memory: Minimum score threshold (range 0.0–1.0) for
                memory-store hits.  Hits with a score strictly below this value
                are excluded before the result is truncated to *limit*.
                Defaults to ``0.5``.  Set to ``0.0`` to return all memory hits
                regardless of score.
            min_score_files: Minimum score threshold (range 0.0–1.0) for
                file-corpus hits.  Hits with a score strictly below this value
                are excluded before the result is truncated to *limit*.
                Defaults to ``0.05`` (BM25 noise floor).  Set to ``0.0`` to
                return all file hits regardless of score.

        Returns:
            A dict matching the UnifiedQueryResponse shape containing
            fused, ranked hits and degradation metadata.
        """
        expanded: list[str] = _expand_corpora(corpora)

        if (
            chunk_memory_enabled
            and query_embedding is None
            and memory_instance is not None
        ):
            try:
                query_embedding = memory_instance.embedding_model.embed(query_text)
            except Exception as exc:
                logger.warning(
                    "Failed to embed query for chunk-memory retrieval: %s", exc
                )

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
                        chunk_memory_enabled=chunk_memory_enabled,
                        query_embedding=query_embedding,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                degraded = True
                degradation_reasons.append(f"file_corpus: {exc}")

        if "memory_store" in expanded:
            corpora_queried.append("memory_store")
            try:
                all_hits.extend(
                    self._query_memory_store(
                        query_text,
                        limit=limit,
                        user_id=user_id,
                        memory_instance=memory_instance,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                degraded = True
                degradation_reasons.append(f"memory_store: {exc}")

        all_hits.sort(key=lambda h: h.score, reverse=True)
        filteredMems = [
            h
            for h in all_hits
            if (
                h.corpus == "memory_store" and h.score >= min_score_memory
            )  # add decay factor to memory scores to prefer file hits when scores are close
        ]
        filteredFiles = [
            h
            for h in all_hits
            if (
                h.corpus == "file_corpus"
                and QueryService._get_score_by_decay_factor(h) >= min_score_files
            )
        ]
        available_hits_by_corpus = self._count_hits_by_corpus(
            filteredMems + filteredFiles
        )
        truncated = filteredMems[:limit] + filteredFiles[:limit]

        return UnifiedQueryResponse(
            hits=truncated,
            total=len(all_hits),
            corpora_queried=corpora_queried,
            available_hits_by_corpus=available_hits_by_corpus,
            degraded=degraded,
            degradation_reasons=degradation_reasons,
        ).model_dump()

    @staticmethod
    def _get_score_by_decay_factor(hit: FileHit | MemoryHit) -> float:
        if isinstance(hit, MemoryHit):
            created_at = hit.metadata.get("created_at") if hit.metadata else None
            if created_at:
                from datetime import datetime, timedelta

                try:
                    created_at_dt = datetime.fromisoformat(created_at)
                    age = datetime.now() - created_at_dt
                    halflife = timedelta(days=3)
                    decay_factor = 0.9 ** (age / halflife)
                    # day 1, 0.9; day 3, 0.729; day 7, 0.531; day 14, 0.282
                    return hit.score * decay_factor
                except ValueError:
                    logger.warning(
                        "Invalid created_at format for memory_id %s: %s",
                        getattr(hit, "memory_id", "unknown"),
                        created_at,
                    )
            else:
                logger.warning(
                    "Missing created_at for memory_id %s; skipping decay",
                    getattr(hit, "memory_id", "unknown"),
                )
            return hit.score
        return hit.score

    def _query_file_corpus(
        self,
        query_text: str,
        *,
        path_filter: str | None,
        language_filter: str | None,
        limit: int,
        chunk_memory_enabled: bool = False,
        query_embedding: list[float] | None = None,
    ) -> list[FileHit]:
        """Query the file corpus and return normalised FileHit objects.

        When *chunk_memory_enabled* is ``True`` and *query_embedding* is
        provided, the underlying corpus query also performs semantic
        similarity search against chunk summary embeddings.
        """
        filters: dict[str, Any] = {}
        if path_filter is not None:
            filters["file_path"] = path_filter
        if language_filter is not None:
            filters["language"] = language_filter

        raw: list[dict[str, Any]] = self._corpus.query(
            query_text,
            filters=filters or None,
            limit=limit,
            chunk_memory_enabled=chunk_memory_enabled,
            query_embedding=query_embedding,
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
                    "datetime": self._coerce_file_datetime(chunk),
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
        memory_instance: Any | None,
    ) -> list[MemoryHit]:
        """Query the memory store and return normalised MemoryHit objects.

        Returns an empty list when *memory_instance* is ``None`` (memory not yet
        initialised), instead of raising, so the file-corpus path is unaffected.

        Args:
            query_text: Natural-language query string forwarded to the retrieval
                backend.
            limit: Maximum number of results to return.
            user_id: When not ``None``, forwarded as ``user_id`` to the retrieval
                call to scope results to a specific user.  Omitted from the call
                when ``None``.
            memory_instance: Initialised memory object with a ``search`` method.
                When ``None``, the method returns ``[]`` immediately without
                contacting the backend.

        Returns:
            List of :class:`~api_models.MemoryHit` objects, each with
            ``memory_id``, ``content``, ``score``, ``corpus`` (always
            ``"memory_store"``), and optional ``metadata`` fields.  Empty list
            if *memory_instance* is ``None`` or the backend returns no results.
        """
        if memory_instance is None:
            return []

        kwargs: dict[str, Any] = {"query": query_text, "limit": limit}
        if user_id is not None:
            kwargs["user_id"] = user_id

        raw_response = self._retrieval.search(memory_instance, **kwargs)
        raw = self._coerce_memory_results(raw_response)

        return [
            MemoryHit.model_validate(
                {
                    "memory_id": str(result.get("id", "")),
                    "content": result.get("memory", ""),
                    "score": self._coerce_memory_score(result),
                    "datetime": self._coerce_memory_datetime(result),
                    "corpus": "memory_store",
                    "metadata": result.get("metadata"),
                }
            )
            for result in raw
        ]

    @staticmethod
    def _coerce_memory_results(raw_response: Any) -> list[dict[str, Any]]:
        if isinstance(raw_response, list):
            return [record for record in raw_response if isinstance(record, dict)]
        if isinstance(raw_response, dict):
            results = raw_response.get("results")
            if isinstance(results, list):
                return [record for record in results if isinstance(record, dict)]
        raise TypeError(
            "Memory retrieval response must be a list or dict containing 'results'."
        )

    @staticmethod
    def _coerce_memory_score(result: dict[str, Any]) -> float:
        top_level_score = result.get("score")
        if isinstance(top_level_score, (int, float)):
            return float(top_level_score)

        retrieval_metadata = result.get("_retrieval")
        if isinstance(retrieval_metadata, dict):
            nested_score = retrieval_metadata.get("score")
            if isinstance(nested_score, (int, float)):
                return float(nested_score)

        return 0.0

    @staticmethod
    def _coerce_memory_datetime(result: dict[str, Any]) -> str | None:
        for key in ("updated_at", "created_at"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value

        metadata = result.get("metadata")
        if isinstance(metadata, dict):
            for key in ("updated_at", "created_at"):
                value = metadata.get(key)
                if isinstance(value, str) and value:
                    return value

        return None

    @staticmethod
    def _coerce_file_datetime(chunk: dict[str, Any]) -> str | None:
        for key in ("indexed_at", "last_indexed_at", "modified_at", "created_at"):
            value = chunk.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _count_hits_by_corpus(hits: list[FileHit | MemoryHit]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for hit in hits:
            counts[hit.corpus] = counts.get(hit.corpus, 0) + 1
        return counts


def _expand_corpora(corpora: list[str]) -> list[str]:
    if "all" in corpora:
        return ["memory_store", "file_corpus"]
    return list(dict.fromkeys(corpora))
