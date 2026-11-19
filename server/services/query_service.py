"""Memory-store query normalization, filtering, and degradation handling."""

from __future__ import annotations

import logging
import time
from typing import Any

from api_models import MemoryHit, UnifiedQueryResponse
from . import query_events
from .metrics import query_duration_seconds, query_hits_count


logger = logging.getLogger(__name__)


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
        project: str | None = None,
    ) -> dict[str, Any]:
        """Return score-filtered memory hits and truthful query counts.

        Both supported selectors, ``memory_store`` and the compatibility alias
        ``all``, query only the memory store. The HTTP model validates selectors;
        this service treats the already-validated selector as provenance rather
        than routing input. Explicit file-corpus selection is unsupported.

        Unlike ``/search`` and ``/retrieve``, this method normalizes each result
        to :class:`MemoryHit` and applies ``min_score_memory`` after retrieval.
        ``total`` counts normalized backend candidates before thresholding, while
        ``available_hits_by_corpus`` counts threshold-qualified hits before the
        score-ordered result is limited. Missing memory initialization is an
        empty, non-degraded no-op. Retrieval or normalization failures instead
        produce an empty degraded result with the backend reason.
        """
        # Pydantic validates HTTP selectors; both supported values alias memory.
        del corpora

        started_at = time.monotonic()
        raw_hits: list[MemoryHit] = []
        retrieval_summary: dict[str, Any] | None = None
        degraded = False
        degradation_reasons: list[str] = []
        try:
            raw_hits, retrieval_summary = self._query_memory_store(
                query_text,
                limit=limit,
                user_id=user_id,
                memory_instance=memory_instance,
            )
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
        returned_hits = qualified_hits[:limit]
        query_hits_count.labels(corpus="memory_store").observe(len(returned_hits))

        response = UnifiedQueryResponse(
            hits=returned_hits,
            total=len(raw_hits),
            corpora_queried=["memory_store"],
            available_hits_by_corpus=availability,
            degraded=degraded,
            degradation_reasons=degradation_reasons,
        ).model_dump()
        self._emit_query_events(
            query_text=query_text,
            limit=limit,
            user_id=user_id,
            project=project,
            response=response,
            retrieval_summary=retrieval_summary,
            started_at=started_at,
        )
        return response

    @staticmethod
    def _emit_query_events(
        *,
        query_text: str,
        limit: int,
        user_id: str | None,
        project: str | None,
        response: dict[str, Any],
        retrieval_summary: dict[str, Any] | None,
        started_at: float,
    ) -> None:
        """Emit stdout query/hit events; never raises or alters the response.

        Stage counts and degradation come from the underlying retrieval
        response; ``candidates`` is the pre-threshold result count.
        """
        try:
            resolved_project = query_events.derive_project(user_id, project)
            hits = query_events.extract_memory_hits(response)
            summary = retrieval_summary or query_events.summarize_retrieval(None)
            reasons = list(response["degradation_reasons"])
            reasons.extend(
                reason
                for reason in summary["degradation_reasons"]
                if reason not in reasons
            )
            query_events.emit_query_event(
                endpoint="query",
                query=query_text,
                limit=limit,
                user_id=user_id,
                agent_id=None,
                run_id=None,
                project=resolved_project,
                hits=len(hits),
                lexical=summary["lexical"],
                semantic=summary["semantic"],
                rerank=summary["rerank"],
                degraded=bool(response["degraded"] or summary["degraded"]),
                degradation_reasons=reasons,
                latency_ms=(time.monotonic() - started_at) * 1000,
                candidates=response["total"],
            )
            query_events.emit_memory_hits(
                endpoint="query",
                hits=hits,
                user_id=user_id,
                agent_id=None,
                project=resolved_project,
            )
        except Exception:  # noqa: BLE001
            logger.debug("query event emission failed", exc_info=True)

    def _query_memory_store(
        self,
        query_text: str,
        *,
        limit: int,
        user_id: str | None,
        memory_instance: Any | None,
    ) -> tuple[list[MemoryHit], dict[str, Any] | None]:
        """Normalize retrieval results, or return no hits before memory startup.

        Returns the hits and the retrieval response's stage/degradation summary
        (``None`` when the retrieval boundary was not called).

        ``query_text`` and ``limit`` are always forwarded to the retrieval
        boundary. ``user_id`` is forwarded only when present. A missing memory
        instance is a no-op and does not call the retrieval boundary. Every
        returned hit has ``corpus="memory_store"``; malformed response envelopes
        raise and are reported as degradation by :meth:`query`.
        """
        if memory_instance is None:
            return [], None

        kwargs: dict[str, Any] = {"query": query_text, "limit": limit}
        if user_id is not None:
            kwargs["user_id"] = user_id

        raw_response = self._retrieval.search(memory_instance, **kwargs)
        hits = [
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
        return hits, query_events.summarize_retrieval(raw_response)

    @staticmethod
    def _coerce_memory_results(raw_response: Any) -> list[dict[str, Any]]:
        """Accept mem0's list form or ``results`` envelope, dropping non-dicts."""
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
        """Prefer a numeric top-level score, then retrieval metadata, else zero."""
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
        """Select updated then created timestamps, including metadata, else none."""
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
