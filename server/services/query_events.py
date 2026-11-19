"""Structured JSON query events written to stdout for log shipping.

Two event kinds are emitted, one compact JSON object per line, on the dedicated
``mem0.events`` logger (never propagated to the root logger):

* ``query_event`` - one per ``/search``, ``/retrieve`` or ``/query`` request.
  ``/query`` events add ``candidates`` (results before the score threshold).
* ``memory_hit`` - one per returned memory (at most ``MAX_MEMORY_HITS``).

Events carry identifiers, counts and scores only. Memory text is never
included. Every emit helper swallows its own failures so that observability can
never alter or fail a response.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .tracing import current_request_id

EVENTS_LOGGER_NAME = "mem0.events"
QUERY_MAX_CHARS = 500
MAX_MEMORY_HITS = 50
_REASON_MAX_CHARS = 200
PROJECT_MAX_CHARS = 200
_PROJECT_SUFFIX = re.compile(r":project:([0-9a-f]+)$")

_LOGGER = logging.getLogger(EVENTS_LOGGER_NAME)


class _StdoutEventHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Stream handler that resolves ``sys.stdout`` on every emit.

    Resolving lazily keeps output on the live stdout even when it is replaced
    after setup (for example by test capture).
    """

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self) -> Any:
        return sys.stdout

    @stream.setter
    def stream(self, value: Any) -> None:
        return None


def setup_events_logger() -> logging.Logger:
    """Configure the ``mem0.events`` logger; safe to call repeatedly."""
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False
    if not any(isinstance(h, _StdoutEventHandler) for h in _LOGGER.handlers):
        handler = _StdoutEventHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(message)s"))
        _LOGGER.addHandler(handler)
    return _LOGGER


setup_events_logger()


def derive_project(user_id: str | None, explicit: str | None = None) -> str:
    """Return the project label for an event, at most ``PROJECT_MAX_CHARS`` long.

    An explicit non-empty ``project`` (a readable id such as
    ``git:github.com/org/repo``) wins and is truncated, never rejected.
    Otherwise the hash is taken from a ``...:project:<hex>`` suffix of
    ``user_id``; anything else yields ``""``.
    """
    if isinstance(explicit, str) and explicit:
        return explicit[:PROJECT_MAX_CHARS]
    if isinstance(user_id, str):
        match = _PROJECT_SUFFIX.search(user_id)
        if match:
            return match.group(1)[:PROJECT_MAX_CHARS]
    return ""


def extract_memory_hits(response: Any) -> list[tuple[str, float | None]]:
    """Return ``(memory_id, score)`` per returned memory, in returned order.

    Accepts a ``{"results": [...]}`` envelope, a ``{"hits": [...]}`` envelope,
    or a bare list. Items without an id are skipped. Ids come from ``id`` or
    ``memory_id``; scores from ``score`` or ``_retrieval.score``.
    """
    items: Any = response
    if isinstance(response, Mapping):
        items = response.get("results")
        if not isinstance(items, list):
            items = response.get("hits")
    if not isinstance(items, list):
        return []

    hits: list[tuple[str, float | None]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        memory_id = item.get("id", item.get("memory_id"))
        if memory_id is None or memory_id == "":
            continue
        hits.append((str(memory_id), _score_of(item)))
    return hits


def summarize_retrieval(response: Any) -> dict[str, Any]:
    """Pull stage counts and degradation data out of a search/retrieve response.

    Uses the ``trace.retrieval`` diagnostics computed by the retrieval service.
    Stage counts the service did not compute stay ``None``.
    """
    summary: dict[str, Any] = {
        "lexical": None,
        "semantic": None,
        "rerank": None,
        "degraded": False,
        "degradation_reasons": [],
    }
    if not isinstance(response, Mapping):
        return summary

    trace = response.get("trace")
    diagnostics = trace.get("retrieval") if isinstance(trace, Mapping) else None
    if not isinstance(diagnostics, Mapping):
        diagnostics = {}

    summary["lexical"] = _int_or_none(diagnostics.get("lexical_count"))
    summary["semantic"] = _int_or_none(diagnostics.get("semantic_count"))
    if diagnostics.get("rerank_applied") is True:
        summary["rerank"] = _int_or_none(diagnostics.get("rerank_count"))

    stage_flags = diagnostics.get("degraded")
    degraded_stages = (
        [stage for stage, flag in stage_flags.items() if flag is True]
        if isinstance(stage_flags, Mapping)
        else []
    )
    reasons = response.get("degradation_reasons")
    if not isinstance(reasons, list):
        reasons = diagnostics.get("degradation_reasons")
    if not isinstance(reasons, list):
        reasons = [f"{stage}_unavailable" for stage in degraded_stages]

    summary["degradation_reasons"] = [_cap_reason(reason) for reason in reasons]
    summary["degraded"] = bool(
        response.get("degraded") is True
        or degraded_stages
        or summary["degradation_reasons"]
    )
    return summary


def emit_query_event(
    *,
    endpoint: str,
    query: str,
    user_id: str | None,
    agent_id: str | None,
    run_id: str | None,
    project: str | None,
    hits: int,
    filters: Mapping[str, Any] | None = None,
    scopes: Sequence[str] | None = None,
    limit: int | None = None,
    lexical: int | None = None,
    semantic: int | None = None,
    rerank: int | None = None,
    degraded: bool = False,
    degradation_reasons: Sequence[Any] | None = None,
    latency_ms: float | None = None,
    candidates: int | None = None,
) -> None:
    """Emit one ``query_event`` line. Never raises.

    ``candidates`` is an extra key only ``/query`` sets: the number of
    retrieval results before the ``min_score_memory`` threshold. It is omitted
    from the event when ``None``.
    """
    try:
        query_text = query if isinstance(query, str) else str(query)
        event = {
            "event": "query_event",
            "ts": _now_iso(),
            "request_id": current_request_id(),
            "endpoint": endpoint,
            "query": query_text[:QUERY_MAX_CHARS],
            "query_truncated": len(query_text) > QUERY_MAX_CHARS,
            "filters": dict(filters) if filters else None,
            "scopes": list(scopes) if scopes is not None else None,
            "limit": limit,
            "user_id": user_id or "",
            "agent_id": agent_id or "",
            "run_id": run_id or "",
            "project": project or "",
            "hits": int(hits),
            "lexical": lexical,
            "semantic": semantic,
            "rerank": rerank,
            "degraded": bool(degraded),
            "degradation_reasons": [
                _cap_reason(reason) for reason in (degradation_reasons or [])
            ],
            "latency_ms": _round_or_none(latency_ms),
        }
        if candidates is not None:
            event["candidates"] = int(candidates)
        _write(event)
    except Exception:  # noqa: BLE001
        return


def emit_memory_hits(
    *,
    endpoint: str,
    hits: Sequence[tuple[str, float | None]],
    user_id: str | None,
    agent_id: str | None,
    project: str | None,
) -> None:
    """Emit one ``memory_hit`` line per hit (max ``MAX_MEMORY_HITS``). Never raises."""
    try:
        timestamp = _now_iso()
        request_id = current_request_id()
        for rank, (memory_id, score) in enumerate(hits[:MAX_MEMORY_HITS], start=1):
            try:
                _write(
                    {
                        "event": "memory_hit",
                        "ts": timestamp,
                        "request_id": request_id,
                        "endpoint": endpoint,
                        "memory_id": memory_id,
                        "rank": rank,
                        "score": score,
                        "user_id": user_id or "",
                        "agent_id": agent_id or "",
                        "project": project or "",
                    }
                )
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        return


def _write(event: dict[str, Any]) -> None:
    setup_events_logger()
    _LOGGER.info(
        json.dumps(event, separators=(",", ":"), default=str, allow_nan=False)
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _score_of(item: Mapping[str, Any]) -> float | None:
    candidates = [item.get("score")]
    retrieval = item.get("_retrieval")
    if isinstance(retrieval, Mapping):
        candidates.append(retrieval.get("score"))
    for value in candidates:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if math.isfinite(value):
                return float(value)
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _round_or_none(value: float | None) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(value):
            return round(float(value), 3)
    return None


def _cap_reason(reason: Any) -> str:
    return str(reason)[:_REASON_MAX_CHARS]
