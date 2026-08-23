"""Memory-store query normalization, filtering, and degradation handling."""

from __future__ import annotations

import time
from typing import Any

from api_models import MemoryHit, UnifiedQueryResponse
from .metrics import query_duration_seconds, query_hits_count


class QueryService:
    """Query the configured memory retrieval boundary."""

    def __init__(self, retrieval_service: Any) -> None:
        self._retrieval = retrieval_service

    def query(
        self,
        query_text: str,
        corpora: list[str],
        limit: int = 10,
        user_id: str | None = None,
        memory_instance: Any | None = None,
        min_score_memory: float = 0.5,
    ) -> dict[str, Any]:
        """Return score-filtered memory hits and truthful query counts.

        Both supported selectors, ``memory_store`` and the compatibility alias
        ``all``, query only the memory store. ``total`` counts normalized backend
        candidates before thresholding, while ``available_hits_by_corpus`` counts
        threshold-qualified hits before the score-ordered result is limited.
        Missing memory initialization is an empty, non-degraded result. Retrieval
        failures instead produce an empty degraded result with the backend reason.
        """
        # Pydantic validates HTTP selectors; both supported values alias memory.
        del corpora

        started_at = time.monotonic()
        raw_hits: list[MemoryHit] = []
        degraded = False
        degradation_reasons: list[str] = []
        try:
            raw_hits = self._query_memory_store(
                query_text,
                limit=limit,
                user_id=user_id,
                memory_instance=memory_instance,
            )
            query_hits_count.labels(corpus="memory_store").observe(len(raw_hits))
        except Exception as exc:  # noqa: BLE001
            degraded = True
            degradation_reasons.append(f"memory_store: {exc}")
        finally:
            query_duration_seconds.labels(corpus="memory_store").observe(
                time.monotonic() - started_at
            )

        qualified_hits = sorted(
            (hit for hit in raw_hits if hit.score >= min_score_memory),
            key=lambda hit: hit.score,
            reverse=True,
        )
        availability = (
            {"memory_store": len(qualified_hits)} if qualified_hits else {}
        )

        return UnifiedQueryResponse(
            hits=qualified_hits[:limit],
            total=len(raw_hits),
            corpora_queried=["memory_store"],
            available_hits_by_corpus=availability,
            degraded=degraded,
            degradation_reasons=degradation_reasons,
        ).model_dump()

    def _query_memory_store(
        self,
        query_text: str,
        *,
        limit: int,
        user_id: str | None,
        memory_instance: Any | None,
    ) -> list[MemoryHit]:
        """Normalize retrieval results, or return no hits before memory startup."""
        if memory_instance is None:
            return []

        kwargs: dict[str, Any] = {"query": query_text, "limit": limit}
        if user_id is not None:
            kwargs["user_id"] = user_id

        raw_response = self._retrieval.search(memory_instance, **kwargs)
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
            for result in self._coerce_memory_results(raw_response)
        ]

    @staticmethod
    def _coerce_memory_results(raw_response: Any) -> list[dict[str, Any]]:
        """Accept mem0's list form or its ``results`` response envelope."""
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
        """Prefer a numeric top-level score, then mem0 retrieval metadata."""
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
        """Select updated then created timestamps, including metadata fallbacks."""
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
