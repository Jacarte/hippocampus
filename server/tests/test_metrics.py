"""Tests for the Prometheus metrics module and ``/metrics`` endpoint.

Every test creates a fresh app via ``create_app(startup_enabled=False)``
with a fake memory implementation — no live dependencies, no network
access, no Docker stack.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from pytest import MonkeyPatch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.metrics import query_hits_count
from services.query_service import QueryService


# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

_MINIMAL_CONFIG: dict[str, Any] = {
    "version": "v1.1",
    "vector_store": {"provider": "pgvector", "config": {}},
    "llm": {
        "provider": "openai",
        "config": {"model": "gpt-5", "api_key": "test-key"},
    },
    "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
    "history_db_path": "/tmp/history.db",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_client(monkeypatch: MonkeyPatch) -> TestClient:
    """Create a :class:`TestClient` backed by a fresh ``FakeMemory`` app."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config
            self.records: dict[str, dict[str, Any]] = {}

        def add(
            self, *, messages: list[dict[str, Any]], **params: Any
        ) -> dict[str, Any]:
            record: dict[str, Any] = {"id": "mem-1", "messages": messages, **params}
            self.records["mem-1"] = record
            return record

        def get(self, memory_id: str) -> dict[str, Any]:
            return self.records[memory_id]

        def get_all(self, **_: Any) -> list[dict[str, Any]]:
            return list(self.records.values())

        def search(self, *, query: str, **params: Any) -> dict[str, Any]:
            return {
                "query": query,
                "params": params,
                "results": list(self.records.values()),
            }

        def update(
            self, *, memory_id: str, data: dict[str, Any]
        ) -> dict[str, Any]:
            self.records[memory_id] = {**self.records[memory_id], **data}
            return self.records[memory_id]

        def delete(self, *, memory_id: str) -> None:
            self.records.pop(memory_id, None)

        def delete_all(self, **_: Any) -> None:
            self.records.clear()

        def reset(self) -> None:
            self.records.clear()

        def history(self, *, memory_id: str) -> list[dict[str, Any]]:
            return [{"memory_id": memory_id, "event": "created"}]

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)
    return TestClient(app)


def _configure(client: TestClient) -> None:
    """POST /configure with the minimal config."""
    resp = client.post("/configure", json=_MINIMAL_CONFIG)
    assert resp.status_code == 200, f"configure failed: {resp.text}"


def _query_hits_observation_totals() -> tuple[float, float]:
    """Return the count and sum for memory-store query-hit observations."""
    samples = {
        sample.name: sample.value
        for family in query_hits_count.collect()
        for sample in family.samples
        if sample.labels.get("corpus") == "memory_store"
    }
    return (
        samples.get("query_hits_count_count", 0.0),
        samples.get("query_hits_count_sum", 0.0),
    )


# ---------------------------------------------------------------------------
# 1. Module imports / types
# ---------------------------------------------------------------------------


def test_metrics_module_exports_all_expected_metrics() -> None:
    """All retained metric objects are importable from ``services.metrics``."""
    from services.metrics import (
        http_requests_total,
        http_request_duration_seconds,
        memory_operations_total,
        memory_operation_errors_total,
        retrieval_duration_seconds,
        retrieval_candidates_count,
        retrieval_degradations_total,
        memory_retrieve_total,
        memory_retrieve_hits,
        query_duration_seconds,
        query_hits_count,
    )

    # Every metric exposes a .labels() method — the lowest-common-denominator
    # check for Counter, Histogram, and Gauge.
    for metric in (
        http_requests_total,
        http_request_duration_seconds,
        memory_operations_total,
        memory_operation_errors_total,
        retrieval_duration_seconds,
        retrieval_candidates_count,
        retrieval_degradations_total,
        memory_retrieve_total,
        memory_retrieve_hits,
        query_duration_seconds,
        query_hits_count,
    ):
        assert callable(metric.labels), f"Metric lacks .labels(): {metric}"

# ---------------------------------------------------------------------------
# 2. /metrics endpoint format
# ---------------------------------------------------------------------------


def test_metrics_endpoint_returns_prometheus_plaintext(
    monkeypatch: MonkeyPatch,
) -> None:
    """``GET /metrics`` returns 200 with ``text/plain`` Prometheus exposition.

    The request uses ``follow_redirects=False`` to assert the exact
    non-redirect behaviour — Prometheus ``make_asgi_app()`` is mounted at
    ``/metrics`` and serves directly.
    """
    client = _build_client(monkeypatch)
    response = client.get("/metrics", follow_redirects=False)

    assert response.status_code == 200
    assert not response.history, "GET /metrics must not redirect"
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("text/plain") or "openmetrics" in content_type
    assert response.text, "Metrics body should not be empty"
    # At least one HELP or TYPE line signals valid Prometheus exposition
    assert "# HELP" in response.text or "# TYPE" in response.text, (
        "Expected Prometheus HELP/TYPE lines in /metrics output"
    )


def test_query_metrics_help_text_is_memory_only(monkeypatch: MonkeyPatch) -> None:
    """Query metric HELP text describes only the retained memory store."""
    metrics_text = _build_client(monkeypatch).get("/metrics").text

    assert (
        "# HELP query_duration_seconds Memory-store query latency in seconds."
        in metrics_text
    )
    assert (
        "# HELP query_hits_count Number of memory-store results returned per query."
        in metrics_text
    )


def test_query_metrics_emit_only_memory_store_corpus_label(
    monkeypatch: MonkeyPatch,
) -> None:
    """A memory query emits no alias or removed-corpus metric labels."""
    client = _build_client(monkeypatch)
    _configure(client)

    response = client.post(
        "/query", json={"query": "remember", "corpora": ["all"]}
    )
    assert response.status_code == 200

    query_metric_names = ("query_duration_seconds", "query_hits_count")
    corpus_labels = {
        sample.labels["corpus"]
        for family in text_string_to_metric_families(client.get("/metrics").text)
        for sample in family.samples
        if sample.name.startswith(query_metric_names) and "corpus" in sample.labels
    }
    assert corpus_labels == {"memory_store"}


def test_query_hits_metric_observes_limited_returned_hits() -> None:
    """The hit histogram observes the final filtered and limited result count."""
    class FakeRetrieval:
        def search(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
            return [
                {"id": "low", "memory": "low", "score": 0.2},
                {"id": "highest", "memory": "highest", "score": 0.95},
                {"id": "middle", "memory": "middle", "score": 0.75},
            ]

    before_count, before_sum = _query_hits_observation_totals()
    response = QueryService(FakeRetrieval()).query(
        "remember",
        corpora=["memory_store"],
        limit=1,
        memory_instance=object(),
        min_score_memory=0.5,
    )
    after_count, after_sum = _query_hits_observation_totals()

    assert response["total"] == 3
    assert response["available_hits_by_corpus"] == {"memory_store": 2}
    assert len(response["hits"]) == 1
    assert after_count - before_count == 1
    assert after_sum - before_sum == len(response["hits"])


def test_query_hits_metric_observes_zero_for_filtered_and_degraded_results() -> None:
    """Handled empty and degraded queries each emit a zero-hit observation."""
    class FilteredRetrieval:
        def search(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
            return [{"id": "low", "memory": "low", "score": 0.2}]

    class DegradedRetrieval:
        def search(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
            raise RuntimeError("memory unavailable")

    before_count, before_sum = _query_hits_observation_totals()
    filtered_response = QueryService(FilteredRetrieval()).query(
        "remember",
        corpora=["memory_store"],
        memory_instance=object(),
        min_score_memory=0.5,
    )
    degraded_response = QueryService(DegradedRetrieval()).query(
        "remember",
        corpora=["memory_store"],
        memory_instance=object(),
    )
    after_count, after_sum = _query_hits_observation_totals()

    assert filtered_response["hits"] == []
    assert filtered_response["total"] == 1
    assert degraded_response["hits"] == []
    assert degraded_response["degraded"] is True
    assert after_count - before_count == 2
    assert after_sum - before_sum == 0


# ---------------------------------------------------------------------------
# 3. Request counter increment
# ---------------------------------------------------------------------------


def test_http_request_counters_appear_after_request(
    monkeypatch: MonkeyPatch,
) -> None:
    """``http_requests_total`` and ``http_request_duration_seconds`` appear
    after a real HTTP request through the middleware."""
    client = _build_client(monkeypatch)
    # Trigger the correlation-id middleware which records metrics
    client.get("/health")

    metrics_text = client.get("/metrics").text

    # http_requests_total — labels are sorted alphabetically by prometheus_client
    assert (
        'http_requests_total{method="GET",path="/health",status_code="200"}'
    ) in metrics_text, "http_requests_total for /health not found"

    # http_request_duration_seconds — _count and _bucket series
    assert (
        'http_request_duration_seconds_count{method="GET",path="/health"}'
    ) in metrics_text, "http_request_duration_seconds_count not found"

    # A histogram bucket entry should also be present. (Labels are sorted
    # alphabetically by prometheus_client so ``le=`` comes before the method
    # and path labels — we assert only the prefix to avoid fragility.)
    assert (
        'http_request_duration_seconds_bucket{le='
    ) in metrics_text, "http_request_duration_seconds histogram bucket not found"


# ---------------------------------------------------------------------------
# 4. Memory-operation metrics
# ---------------------------------------------------------------------------


def test_memory_operation_metrics_appear_after_configure(
    monkeypatch: MonkeyPatch,
) -> None:
    """``memory_operations_total`` is emitted after POST /configure."""
    client = _build_client(monkeypatch)
    _configure(client)

    metrics_text = client.get("/metrics").text
    # Labels sorted alphabetically: agent_id, operation, user_id
    assert (
        'memory_operations_total{agent_id="",operation="configure",user_id=""}'
    ) in metrics_text, "memory_operations_total{operation='configure'} not found"


def test_memory_operation_error_metrics_on_missing_identifier(
    monkeypatch: MonkeyPatch,
) -> None:
    """``memory_operation_errors_total`` increments when ``delete_all``
    is called without any identifier."""
    client = _build_client(monkeypatch)
    _configure(client)

    # DELETE /memories without user_id/agent_id/run_id → ValueError in service
    resp = client.delete("/memories")
    assert resp.status_code == 400, (
        f"Expected 400 for missing identifiers, got {resp.status_code}"
    )

    metrics_text = client.get("/metrics").text
    # The error counter increments for the failing operation
    assert (
        'memory_operation_errors_total{operation="delete_all"}'
    ) in metrics_text, (
        "memory_operation_errors_total{operation='delete_all'} not found"
    )
    # The main operation counter also increments
    assert (
        'memory_operations_total{agent_id="",operation="delete_all",user_id=""}'
    ) in metrics_text, (
        "memory_operations_total{operation='delete_all'} not found"
    )


# ---------------------------------------------------------------------------
# 5. Retrieval metrics
# ---------------------------------------------------------------------------


def test_retrieval_pipeline_metrics_appear_after_search(
    monkeypatch: MonkeyPatch,
) -> None:
    """Retrieval pipeline metrics (duration, candidates) appear after
    ``POST /search``."""
    client = _build_client(monkeypatch)
    _configure(client)

    # Create a memory so the search returns results
    client.post(
        "/memories",
        json={
            "messages": [{"role": "user", "content": "remember this"}],
            "user_id": "user-1",
            "metadata": {"source": "chat"},
        },
    )

    # Search triggers RetrievalService which records pipeline metrics
    search_resp = client.post(
        "/search",
        json={
            "query": "remember",
            "user_id": "user-1",
            "filters": {"source": "chat"},
        },
    )
    assert search_resp.status_code == 200

    metrics_text = client.get("/metrics").text

    # -- retrieval pipeline histograms --
    # Each histogram emits _count, _sum, and _bucket series
    # Stages: lexical, semantic, total
    assert "retrieval_duration_seconds_count" in metrics_text, (
        "retrieval_duration_seconds histogram not found"
    )

    # At least one stage bucket exists (labels sorted alphabetically by
    # prometheus_client — le= comes before stage= in output).
    assert "retrieval_duration_seconds_bucket{le=" in metrics_text, (
        "No retrieval_duration_seconds histogram bucket found"
    )

    # -- retrieval candidates --
    assert "retrieval_candidates_count" in metrics_text, (
        "retrieval_candidates_count not found"
    )

    # -- memory retrieve tracking --
    assert (
        'memory_retrieve_total{agent_id="",user_id="user-1"}'
    ) in metrics_text, "memory_retrieve_total not found"

    # search returned at least one result → hits counter incremented.
    # Note: prometheus_client appends ``_total`` to Counter names in
    # exposition format, so ``memory_retrieve_hits`` appears as
    # ``memory_retrieve_hits_total`` in the /metrics output.
    assert (
        'memory_retrieve_hits_total{agent_id="",user_id="user-1"}'
    ) in metrics_text, "memory_retrieve_hits not found"
