from __future__ import annotations

from typing import Any

from services.query_service import QueryService
from services.retrieval_service import RetrievalService


class FakeRetrieval:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    def search(self, memory_instance: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append({"memory_instance": memory_instance, **kwargs})
        return self.results


class ErrorRetrieval:
    def search(self, memory_instance: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("memory unavailable")


def _memory_result(
    memory_id: str,
    score: float,
    *,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": memory_id,
        "memory": f"content for {memory_id}",
        "score": score,
        "metadata": {"created_at": "2026-01-01T00:00:00Z"},
    }
    if created_at is not None:
        result["created_at"] = created_at
    if updated_at is not None:
        result["updated_at"] = updated_at
    return result


def test_query_response_counts_before_and_after_threshold() -> None:
    retrieval = FakeRetrieval(
        [
            _memory_result("low", 0.2),
            _memory_result("highest", 0.95),
            _memory_result("middle", 0.75),
        ]
    )
    memory = object()
    service = QueryService(retrieval_service=retrieval)

    result = service.query(
        "hello",
        corpora=["memory_store"],
        limit=1,
        user_id="alice",
        memory_instance=memory,
        min_score_memory=0.5,
    )

    assert result["total"] == 3
    assert result["available_hits_by_corpus"] == {"memory_store": 2}
    assert [hit["memory_id"] for hit in result["hits"]] == ["highest"]
    assert result["corpora_queried"] == ["memory_store"]
    assert retrieval.calls == [
        {
            "memory_instance": memory,
            "query": "hello",
            "limit": 1,
            "user_id": "alice",
        }
    ]


def test_query_all_alias_matches_memory_store() -> None:
    results = [_memory_result("one", 0.9)]
    service = QueryService(retrieval_service=FakeRetrieval(results))
    memory = object()

    memory_only = service.query(
        "hello", corpora=["memory_store"], memory_instance=memory
    )
    alias = service.query("hello", corpora=["all"], memory_instance=memory)

    assert alias == memory_only
    assert alias["corpora_queried"] == ["memory_store"]


def test_query_all_filtered_preserves_raw_total() -> None:
    service = QueryService(
        retrieval_service=FakeRetrieval([_memory_result("low", 0.1)])
    )

    result = service.query(
        "hello",
        corpora=["memory_store"],
        memory_instance=object(),
        min_score_memory=0.9,
    )

    assert result["total"] == 1
    assert result["available_hits_by_corpus"] == {}
    assert result["hits"] == []


def test_query_preserves_score_and_prefers_updated_datetime() -> None:
    service = QueryService(
        retrieval_service=FakeRetrieval(
            [
                _memory_result(
                    "dated",
                    0.8,
                    created_at="2026-05-20T10:00:00Z",
                    updated_at="2026-05-21T10:00:00Z",
                )
            ]
        )
    )

    result = service.query(
        "dated", corpora=["memory_store"], memory_instance=object()
    )

    assert result["hits"][0]["score"] == 0.8
    assert result["hits"][0]["datetime"] == "2026-05-21T10:00:00Z"


def test_query_uses_nested_retrieval_score() -> None:
    retrieval = FakeRetrieval(
        [
            {
                "id": "nested",
                "memory": "nested score",
                "_retrieval": {"score": 0.7},
                "metadata": None,
            }
        ]
    )
    service = QueryService(retrieval_service=retrieval)

    result = service.query(
        "nested", corpora=["memory_store"], memory_instance=object()
    )

    assert result["hits"][0]["score"] == 0.7


def test_query_returns_empty_without_initialized_memory() -> None:
    retrieval = FakeRetrieval([_memory_result("unused", 0.9)])
    service = QueryService(retrieval_service=retrieval)

    result = service.query("hello", corpora=["all"], memory_instance=None)

    assert result["hits"] == []
    assert result["total"] == 0
    assert result["corpora_queried"] == ["memory_store"]
    assert result["degraded"] is False
    assert retrieval.calls == []


def test_query_degrades_gracefully_on_memory_error() -> None:
    service = QueryService(retrieval_service=ErrorRetrieval())

    result = service.query(
        "hello", corpora=["memory_store"], memory_instance=object()
    )

    assert result["hits"] == []
    assert result["total"] == 0
    assert result["degraded"] is True
    assert result["degradation_reasons"] == ["memory_store: memory unavailable"]


def test_query_memory_store_works_with_real_retrieval_service() -> None:
    class FakeMemoryBackend:
        def __init__(self) -> None:
            self.last_search_kwargs: dict[str, Any] | None = None

        def search(self, **kwargs: Any) -> dict[str, Any]:
            self.last_search_kwargs = kwargs
            return {"results": [_memory_result("real", 0.9)]}

        def get_all(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

    memory = FakeMemoryBackend()
    service = QueryService(retrieval_service=RetrievalService())

    result = service.query(
        "hello",
        corpora=["memory_store"],
        memory_instance=memory,
        user_id="alice",
        limit=3,
    )

    assert result["hits"][0]["memory_id"] == "real"
    assert memory.last_search_kwargs is not None
    assert memory.last_search_kwargs["top_k"] == 3
    assert memory.last_search_kwargs["filters"] == {"user_id": "alice"}
