from __future__ import annotations

import logging
import math
import re
import uuid
from typing import Any

from .metrics import file_corpus_operations_total

logger = logging.getLogger(__name__)


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
        """Store chunks for *file_path* under *root*, replacing any previous chunks.

        Each element of *chunks* may optionally carry:

        - ``summary_text`` (``str | None``) — a human-readable summary of the
          chunk's content, e.g. produced by an LLM.  Stored as-is; ``None``
          when omitted.
        - ``summary_embedding`` (``list[float] | None``) — a vector embedding
          of ``summary_text`` for semantic search.  Stored as-is; ``None`` when
          omitted.  Neither field is required; existing callers that do not
          supply them continue to work unchanged.
        """
        file_corpus_operations_total.labels(operation="upsert").inc()
        self._remove_file_chunks(root, file_path)
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
                "indexed_at": chunk.get("indexed_at"),
                "summary_text": chunk.get("summary_text"),
                "summary_embedding": chunk.get("summary_embedding"),
            }
            storage_key = f"{root}\x00{file_path}\x00{chunk_id}"
            self._chunks[storage_key] = record

    def delete_file(self, root: str, file_path: str) -> None:
        file_corpus_operations_total.labels(operation="delete").inc()
        self._remove_file_chunks(root, file_path)

    def _remove_file_chunks(self, root: str, file_path: str) -> None:
        """Remove all stored chunks for *file_path* under *root* without metrics.

        This is the internal chunk-removal primitive used by both
        :meth:`upsert_chunks` (replace semantics) and :meth:`delete_file`
        (explicit deletion).  Only :meth:`delete_file` emits a metrics counter
        so that ``operation="delete"`` is not inflated by upsert-internal
        replacements.
        """
        prefix = f"{root}\x00{file_path}\x00"
        keys_to_remove = [k for k in self._chunks if k.startswith(prefix)]
        for key in keys_to_remove:
            del self._chunks[key]

    def query(
        self,
        query_text: str,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
        chunk_memory_enabled: bool = False,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        """Return chunks matching *query_text*.

        Lexical search always runs first.  Semantic search also runs when
        *chunk_memory_enabled* is ``True`` and *query_embedding* is provided;
        otherwise this method returns the lexical results unchanged.  Both
        paths apply the same filters and result limit.  Chunks that match either
        path are merged by ID, with the higher score retained for duplicate
        matches.  Chunks without a non-empty ``summary_embedding`` are skipped
        by the semantic path.

        Unlike :meth:`query_with_summaries`, this combined query catches any
        semantic-search error, logs a warning, and returns the lexical results.
        The fallback may be empty when the lexical search has no matches.
        """
        file_corpus_operations_total.labels(operation="query").inc()
        lexical = self._lexical_query(query_text, filters=filters, limit=limit)

        if not chunk_memory_enabled or query_embedding is None:
            return lexical

        try:
            merged = self._merge_with_semantic(
                lexical=lexical,
                filters=filters,
                query_embedding=query_embedding,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "summary-backed retrieval failed, falling back to lexical: %s", exc
            )
            return lexical

        return merged

    def query_with_summaries(
        self,
        query_embedding: list[float],
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return chunks ranked by cosine similarity against *query_embedding*.

        Only chunks that have a non-empty ``summary_embedding`` field are
        considered.  Chunks without embeddings are skipped silently.  An empty
        or all-zero query vector gives every considered chunk a score of
        ``0.0``.  This semantic-only API does not perform lexical search or
        catch similarity errors; callers that need lexical fallback should use
        :meth:`query`.
        """
        results: list[tuple[float, dict[str, Any]]] = []
        for chunk in self._chunks.values():
            embedding = chunk.get("summary_embedding")
            if not embedding:
                continue
            if filters:
                if not all(
                    str(chunk.get(field)) == str(value)
                    for field, value in filters.items()
                ):
                    continue
            score = _cosine_similarity(query_embedding, embedding)
            results.append((score, chunk))

        results.sort(key=lambda t: t[0], reverse=True)
        return [dict(chunk) | {"score": score} for score, chunk in results[:limit]]

    def _lexical_query(
        self,
        query_text: str,
        filters: dict[str, Any] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return chunks containing *query_text*, ranked by a simple TF score.

        Scoring: (token_frequency_sum + phrase_bonus) / word_count.
        The phrase bonus equals ``len(query_tokens)`` when the full query string
        appears verbatim in the content, rewarding exact matches over scattered
        token hits.  Results are sorted descending by score before the *limit*
        cap is applied so the highest-scoring chunks are always returned.

        Args:
            query_text: The search string.  Chunks that do not contain
                *query_text* as a substring (case-insensitive) are excluded
                before scoring.  An empty string matches all chunks.
            filters: Optional dict of ``{field: value}`` pairs applied as an
                AND filter after the substring check.  ``None`` disables
                filtering.  All values are compared as strings.
            limit: Maximum number of results to return after sorting.

        Returns:
            List of chunk dicts (copies of stored chunks) with an added
            ``"score"`` key (float, rounded to 6 decimal places), sorted
            descending by score, capped at *limit*.
        """
        results: list[dict[str, Any]] = []
        lowered_query = query_text.lower()
        query_tokens = [t for t in re.findall(r"[a-z0-9_-]+", lowered_query) if t]

        for chunk in self._chunks.values():
            content: str = chunk.get("content") or ""
            lowered_content = content.lower()
            if lowered_query and lowered_query not in lowered_content:
                continue
            if filters:
                if not all(
                    str(chunk.get(field)) == str(value)
                    for field, value in filters.items()
                ):
                    continue
            tf = (
                sum(lowered_content.count(t) for t in query_tokens)
                if query_tokens
                else 0
            )
            phrase_bonus = (
                len(query_tokens)
                if query_tokens and lowered_query in lowered_content
                else 0
            )
            word_count = max(len(lowered_content.split()), 1)
            score = round((tf + phrase_bonus) / word_count, 6)
            result = dict(chunk)
            result["score"] = score
            results.append(result)

        results.sort(key=lambda c: c.get("score", 0.0), reverse=True)
        return results[:limit]

    def _merge_with_semantic(
        self,
        lexical: list[dict[str, Any]],
        filters: dict[str, Any] | None,
        query_embedding: list[float],
        limit: int,
    ) -> list[dict[str, Any]]:
        semantic = self.query_with_summaries(
            query_embedding=query_embedding,
            filters=filters,
            limit=limit,
        )

        by_id: dict[str, dict[str, Any]] = {}
        for chunk in lexical:
            by_id[chunk["id"]] = dict(chunk)
        for chunk in semantic:
            chunk_id = chunk["id"]
            if chunk_id in by_id:
                by_id[chunk_id]["score"] = max(
                    by_id[chunk_id].get("score", 0.0), chunk.get("score", 0.0)
                )
            else:
                by_id[chunk_id] = dict(chunk)

        merged = sorted(by_id.values(), key=lambda c: c.get("score", 0.0), reverse=True)
        return merged[:limit]

    def reset(self) -> dict[str, Any]:
        file_corpus_operations_total.labels(operation="reset").inc()
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

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
