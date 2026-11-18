"""Eligibility belongs to the backend, including clients without local filters."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.retrieval_service import RetrievalService


def _record(memory_id: str, **metadata: Any) -> dict[str, Any]:
    return {
        "id": memory_id,
        "memory": f"Redis memory {memory_id}",
        "score": 0.9,
        "metadata": {"scope": "project", **metadata},
    }


class MemoryWithRankedRecords:
    """Honor backend top_k so hidden records can consume the initial page."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.search_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append(kwargs)
        return {"results": deepcopy(self.records[: kwargs["top_k"]])}

    def get_all(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        return {"results": deepcopy(self.records[: kwargs["top_k"]])}


def _client(memory: MemoryWithRankedRecords) -> TestClient:
    from server import create_app

    app = create_app(startup_enabled=False)
    app.state.memory = memory
    return TestClient(app)


def _mixed_records() -> list[dict[str, Any]]:
    return [
        _record("hidden", inject=False),
        _record("noise", type="noise"),
        _record("cold", cold_context=True, inject=False),
        _record("archive-type", type="compaction-archive", inject=False),
        _record("archive-tier", tier="archive"),
        _record("cold-tier", tier="cold"),
        _record("archive-noise", cold_context=True, type="noise"),
        _record("legacy"),
        _record("active", inject=True, type="decision"),
    ]


@pytest.mark.parametrize("route", ["/search", "/retrieve", "/query"])
def test_normal_recall_excludes_non_injectable_noise_and_archives(route: str) -> None:
    memory = MemoryWithRankedRecords(_mixed_records())
    body: dict[str, Any] = {"query": "Redis", "user_id": "alice"}
    if route == "/retrieve":
        body.update(scopes=["project"], limit=2)
    elif route == "/query":
        body.update(corpora=["memory_store"], limit=2)

    with _client(memory) as client:
        response = client.post(route, json=body)

    assert response.status_code == 200
    payload = response.json()
    records = payload["hits"] if route == "/query" else payload["results"]
    key = "memory_id" if route == "/query" else "id"
    assert {record[key] for record in records} == {"legacy", "active"}
    if route == "/query":
        assert payload["total"] == 2
        assert payload["available_hits_by_corpus"] == {"memory_store": 2}


@pytest.mark.parametrize("route", ["/search", "/retrieve"])
def test_archive_opt_in_does_not_admit_noise_or_other_non_injectable_memories(
    route: str,
) -> None:
    memory = MemoryWithRankedRecords(_mixed_records())
    body: dict[str, Any] = {
        "query": "Redis",
        "user_id": "alice",
        "filters": {"include_cold_context": True},
    }
    if route == "/retrieve":
        body.update(scopes=["project"], limit=10)

    with _client(memory) as client:
        response = client.post(route, json=body)

    assert response.status_code == 200
    assert {record["id"] for record in response.json()["results"]} == {
        "cold", "archive-type", "archive-tier", "cold-tier", "legacy", "active"
    }
    assert all(
        "include_cold_context" not in call["filters"]
        for call in memory.search_calls + memory.list_calls
    )


@pytest.mark.parametrize("route", ["/search", "/retrieve"])
def test_archive_opt_in_requires_a_json_boolean(route: str) -> None:
    memory = MemoryWithRankedRecords(_mixed_records())
    body: dict[str, Any] = {
        "query": "Redis",
        "user_id": "alice",
        "filters": {"include_cold_context": "false"},
    }
    if route == "/retrieve":
        body["scopes"] = ["project"]
    with _client(memory) as client:
        response = client.post(route, json=body)
    assert response.status_code == 400
    assert "include_cold_context must be a boolean" in response.json()["detail"]
    assert memory.search_calls == []


def test_hybrid_filters_before_candidate_limit_and_reranking() -> None:
    memory = MemoryWithRankedRecords(
        [_record(f"archive-{i}", cold_context=True) for i in range(21)]
        + [_record("visible-1"), _record("visible-2")]
    )

    class RecordingReranker:
        def __init__(self) -> None:
            self.received: list[dict[str, Any]] = []

        def rerank(self, *, query: str, candidates: list[dict[str, Any]]) -> Any:
            self.received = candidates
            return candidates

    reranker = RecordingReranker()
    service = RetrievalService(
        lexical_candidate_limit=2,
        semantic_candidate_limit=2,
        rerank_shortlist_limit=2,
        reranker=reranker,
    )
    response = service.retrieve(
        memory, query="Redis", scopes=["project"], user_id="alice", limit=2
    )

    assert {record["id"] for record in response["results"]} == {
        "visible-1", "visible-2"
    }
    assert {record["id"] for record in reranker.received} == {
        "visible-1", "visible-2"
    }
    assert response["trace"]["retrieval"]["lexical_count"] == 2
    assert response["trace"]["retrieval"]["semantic_count"] == 2
    assert len(memory.search_calls) > 1
    assert len(memory.list_calls) > 1


@pytest.mark.parametrize("failed_stage", ["semantic", "lexical"])
def test_hybrid_fallback_still_enforces_eligibility(failed_stage: str) -> None:
    memory = MemoryWithRankedRecords(_mixed_records())

    def unavailable(**kwargs: Any) -> Any:
        raise RuntimeError("backend unavailable")

    if failed_stage == "semantic":
        memory.search = unavailable
    else:
        memory.get_all = unavailable

    response = RetrievalService().retrieve(
        memory, query="Redis", scopes=["project"], user_id="alice", limit=2
    )
    assert response["degraded"] is True
    assert {record["id"] for record in response["results"]} == {"legacy", "active"}


def test_flat_exclusions_and_metadata_exclusions_both_apply_without_mutation() -> None:
    records = [
        {**_record("flat-hidden"), "inject": False},
        {**_record("flat-noise"), "type": "noise"},
        {**_record("flat-archive"), "cold_context": True},
        {**_record("conflicting", inject=False), "inject": True},
        {"id": "legacy", "memory": "Redis", "score": 0.9},
    ]
    before = deepcopy(records)
    response = RetrievalService().search(
        MemoryWithRankedRecords(records), query="Redis", user_id="alice"
    )
    assert [record["id"] for record in response["results"]] == ["legacy"]
    assert records == before


def test_candidate_expansion_is_bounded_when_every_record_is_excluded() -> None:
    memory = MemoryWithRankedRecords(
        [_record(str(i), inject=False) for i in range(1000)]
    )
    response = RetrievalService().search(memory, query="Redis", user_id="alice", limit=2)
    assert response["results"] == []
    assert max(call["top_k"] for call in memory.search_calls + memory.list_calls) == 200
    assert len(memory.search_calls) <= 8
