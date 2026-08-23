from __future__ import annotations

import importlib
from typing import Any

from fastapi.testclient import TestClient
from pytest import MonkeyPatch


def _make_memory_app(monkeypatch: MonkeyPatch) -> Any:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    server = importlib.reload(importlib.import_module("server"))

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config
            self.search_calls: list[dict[str, Any]] = []

        def search(self, **kwargs: Any) -> dict[str, Any]:
            self.search_calls.append(kwargs)
            return {
                "results": [
                    {
                        "id": "memory-1",
                        "memory": "production-wired memory result",
                        "score": 0.88,
                        "created_at": "2026-05-20T10:00:00Z",
                        "metadata": {"source": "test"},
                    }
                ]
            }

        def get_all(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)
    app.state.memory = FakeMemory({})
    return app


def test_memory_only_query_uses_production_wiring(monkeypatch: MonkeyPatch) -> None:
    app = _make_memory_app(monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/query",
            json={
                "query": "production wired",
                "corpora": ["memory_store"],
                "user_id": "alice",
                "limit": 4,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["corpora_queried"] == ["memory_store"]
    assert body["available_hits_by_corpus"] == {"memory_store": 1}
    assert body["hits"][0]["memory_id"] == "memory-1"
    assert body["hits"][0]["corpus"] == "memory_store"
    assert app.state.memory.search_calls[0]["top_k"] == 4
    assert app.state.memory.search_calls[0]["filters"] == {"user_id": "alice"}


def test_memory_only_query_reports_backend_degradation(monkeypatch: MonkeyPatch) -> None:
    app = _make_memory_app(monkeypatch)

    def fail_search(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("memory unavailable")

    app.state.memory.search = fail_search
    with TestClient(app) as client:
        response = client.post("/query", json={"query": "hello"})

    assert response.status_code == 200
    assert response.json()["degraded"] is True
    assert response.json()["degradation_reasons"] == [
        "memory_store: memory unavailable"
    ]


def test_capabilities_endpoint_is_memory_only(monkeypatch: MonkeyPatch) -> None:
    app = _make_memory_app(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/query/capabilities")

    assert response.status_code == 200
    assert set(response.json()) == {"memory_store"}
