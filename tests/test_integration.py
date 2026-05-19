from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_FIXTURES_ROOT = str(Path(__file__).parent / "fixtures" / "mgrep_repo")


def _make_app(monkeypatch: MonkeyPatch) -> Any:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

    return server.create_app(memory_factory=FakeMemory, startup_enabled=False)


# ---------------------------------------------------------------------------
# 1. Sync fixture root → status shows non-zero file and chunk counts
# ---------------------------------------------------------------------------

def test_sync_fixture_root_status_shows_nonzero_counts(monkeypatch: MonkeyPatch) -> None:
    app = _make_app(monkeypatch)

    with TestClient(app) as client:
        sync_resp = client.post("/index/sync", json={"root": _FIXTURES_ROOT})
        assert sync_resp.status_code == 200
        sync_body = sync_resp.json()
        assert sync_body["root"] == _FIXTURES_ROOT
        assert sync_body["files_indexed"] >= 2, (
            f"Expected at least parser.py + architecture.md, got {sync_body['files_indexed']}"
        )
        assert sync_body["chunks_indexed"] >= 2

        status_resp = client.get("/index/status")
        assert status_resp.status_code == 200
        status_body = status_resp.json()
        assert status_body["total_files"] >= 2
        assert status_body["total_chunks"] >= 2
        assert any(r.get("root_path") == _FIXTURES_ROOT for r in status_body["roots"])


# ---------------------------------------------------------------------------
# 2. node_modules/ is excluded from sync
# ---------------------------------------------------------------------------

def test_sync_excludes_node_modules(monkeypatch: MonkeyPatch) -> None:
    app = _make_app(monkeypatch)

    with TestClient(app) as client:
        sync_resp = client.post("/index/sync", json={"root": _FIXTURES_ROOT})
        assert sync_resp.status_code == 200

        query_resp = client.post(
            "/query",
            json={"query": "some_dep", "corpora": ["file_corpus"]},
        )
        assert query_resp.status_code == 200
        body = query_resp.json()
        paths = [hit.get("path", "") for hit in body["hits"]]
        assert not any("node_modules" in p for p in paths), (
            f"node_modules/ files must not be indexed; got paths: {paths}"
        )


# ---------------------------------------------------------------------------
# 3. Query after sync returns file hits with required provenance fields
# ---------------------------------------------------------------------------

def test_query_after_sync_returns_code_and_markdown_hits(monkeypatch: MonkeyPatch) -> None:
    app = _make_app(monkeypatch)

    with TestClient(app) as client:
        assert client.post("/index/sync", json={"root": _FIXTURES_ROOT}).status_code == 200

        code_resp = client.post(
            "/query",
            json={"query": "count_tokens", "corpora": ["file_corpus"]},
        )
        assert code_resp.status_code == 200
        code_body = code_resp.json()
        assert code_body["total"] >= 1, "Expected at least one hit for 'count_tokens'"
        assert code_body["degraded"] is False

        hit = code_body["hits"][0]
        assert hit["corpus"] == "file_corpus"
        assert "path" in hit
        assert "snippet" in hit
        assert "score" in hit
        assert "parser.py" in hit["path"]

        md_resp = client.post(
            "/query",
            json={"query": "IndexingService", "corpora": ["file_corpus"]},
        )
        assert md_resp.status_code == 200
        md_body = md_resp.json()
        assert md_body["total"] >= 1, "Expected at least one hit for 'IndexingService'"
        md_hit = next(h for h in md_body["hits"] if "architecture.md" in h.get("path", ""))
        assert md_hit["corpus"] == "file_corpus"
        assert "architecture.md" in md_hit["path"]


# ---------------------------------------------------------------------------
# 4. Watch start/stop — uses a mock WatchService (no real filesystem polling)
# ---------------------------------------------------------------------------

def test_watch_start_stop_uses_watch_service_without_real_polling(
    monkeypatch: MonkeyPatch,
) -> None:
    app = _make_app(monkeypatch)

    calls: list[tuple[str, str]] = []

    class FakeWatchService:
        def start(self, root: str) -> None:
            calls.append(("start", root))

        def stop(self, root: str) -> None:
            calls.append(("stop", root))

    app.state.watch_service = FakeWatchService()
    root = "/tmp/fake-watch-root"

    with TestClient(app) as client:
        start_resp = client.post("/index/watch/start", json={"root": root})
        assert start_resp.status_code == 200
        assert start_resp.json()["watching"] is True
        assert start_resp.json()["root"] == root

        stop_resp = client.post("/index/watch/stop", json={"root": root})
        assert stop_resp.status_code == 200
        assert stop_resp.json()["watching"] is False
        assert stop_resp.json()["root"] == root

    assert calls == [("start", root), ("stop", root)], (
        f"Expected exactly start then stop; got {calls}"
    )


# ---------------------------------------------------------------------------
# 5. Reset clears file corpus independently from memory reset
# ---------------------------------------------------------------------------

def test_index_reset_clears_file_corpus_independently_from_memory(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    server = importlib.import_module("server")
    server = importlib.reload(server)

    memory_was_reset = [False]

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

        def reset(self) -> None:
            memory_was_reset[0] = True

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {"provider": "openai", "config": {"model": "gpt-5", "api_key": "k"}},
        "embedder": {"provider": "openai", "config": {"api_key": "k"}},
        "history_db_path": "/tmp/history.db",
    }

    with TestClient(app) as client:
        assert client.post("/configure", json=config).status_code == 200
        assert client.post("/index/sync", json={"root": _FIXTURES_ROOT}).status_code == 200

        status_before = client.get("/index/status").json()
        assert status_before["total_files"] >= 2

        reset_resp = client.post("/index/reset", json={"confirm": True})
        assert reset_resp.status_code == 200
        body = reset_resp.json()
        assert "files_cleared" in body
        assert "chunks_cleared" in body
        assert body["files_cleared"] >= 2

        status_after = client.get("/index/status").json()
        assert status_after["total_files"] == 0
        assert status_after["total_chunks"] == 0

    assert memory_was_reset[0] is False, (
        "Index reset must NOT reset memory store"
    )


# ---------------------------------------------------------------------------
# 6. Degraded query: file corpus raises → returns memory hits + degraded flag
# ---------------------------------------------------------------------------

def test_degraded_query_file_corpus_raises_returns_memory_hits_and_degraded_flag(
    monkeypatch: MonkeyPatch,
) -> None:
    from services.file_corpus_service import FileCorpusService
    from services.query_service import QueryService
    from services.retrieval_service import RetrievalService

    app = _make_app(monkeypatch)

    class BrokenCorpus(FileCorpusService):
        def query(self, query_text: str, filters: Any = None, limit: int = 10) -> list:
            raise RuntimeError("corpus unavailable")

    class StubRetrieval:
        def search(self, memory_instance: Any, **_: Any) -> list[dict[str, Any]]:
            return [
                {
                    "id": "mem-stub-1",
                    "memory": "stub memory result for degraded test",
                    "metadata": {"source": "test"},
                    "_retrieval": {"score": 0.75},
                }
            ]

    app.state.query_service = QueryService(
        corpus=BrokenCorpus(),
        retrieval_service=StubRetrieval(),
    )
    app.state.memory = object()

    with TestClient(app) as client:
        resp = client.post(
            "/query",
            json={"query": "stub", "corpora": ["all"]},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True
    assert any("file_corpus" in r for r in body["degradation_reasons"])

    memory_hits = [h for h in body["hits"] if h.get("corpus") == "memory_store"]
    assert len(memory_hits) >= 1, "Must return memory hits even when file corpus is degraded"
    assert "memory_id" in memory_hits[0]
    assert "content" in memory_hits[0]
    assert "score" in memory_hits[0]


# ---------------------------------------------------------------------------
# 7. Capabilities endpoint returns both memory_store and file_corpus sections
# ---------------------------------------------------------------------------

def test_capabilities_endpoint_returns_both_sections(monkeypatch: MonkeyPatch) -> None:
    app = _make_app(monkeypatch)

    with TestClient(app) as client:
        resp = client.get("/query/capabilities")

    assert resp.status_code == 200
    body = resp.json()
    assert "memory_store" in body, "Capabilities must include memory_store section"
    assert "file_corpus" in body, "Capabilities must include file_corpus section"

    ms = body["memory_store"]
    fc = body["file_corpus"]
    assert isinstance(ms, dict)
    assert isinstance(fc, dict)
    assert "lexical" in ms
    assert "semantic" in ms
    assert "lexical" in fc
    assert isinstance(ms["lexical"], bool)
    assert isinstance(ms["semantic"], bool)
    assert isinstance(fc["lexical"], bool)

