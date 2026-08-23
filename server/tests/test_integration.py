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


def _populate_file_corpus(app: Any) -> None:
    """Load deterministic fixture chunks without an indexing control plane."""
    corpus = app.state.query_service._corpus
    for relative_path, language, content in (
        (
            "src/parser.py",
            "python",
            'def count_tokens(text: str) -> int: return len(text.split())',
        ),
        (
            "docs/architecture.md",
            "markdown",
            "FileCorpusService stores prepared chunks for QueryService retrieval.",
        ),
    ):
        corpus.upsert_chunks(
            root=_FIXTURES_ROOT,
            file_path=relative_path,
            chunks=[{"content": content, "language": language}],
        )


# ---------------------------------------------------------------------------
# 2. Query over a populated corpus returns required provenance fields
# ---------------------------------------------------------------------------

def test_query_over_prepopulated_corpus_returns_code_and_markdown_hits(
    monkeypatch: MonkeyPatch,
) -> None:
    app = _make_app(monkeypatch)
    _populate_file_corpus(app)

    with TestClient(app) as client:
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
            json={"query": "FileCorpusService", "corpora": ["file_corpus"]},
        )
        assert md_resp.status_code == 200
        md_body = md_resp.json()
        assert md_body["total"] >= 1, "Expected a FileCorpusService documentation hit"
        md_hit = next(h for h in md_body["hits"] if "architecture.md" in h.get("path", ""))
        assert md_hit["corpus"] == "file_corpus"
        assert "architecture.md" in md_hit["path"]


# ---------------------------------------------------------------------------
# 3. Degraded query: file corpus raises → returns memory hits + degraded flag
# ---------------------------------------------------------------------------

def test_degraded_query_file_corpus_raises_returns_memory_hits_and_degraded_flag(
    monkeypatch: MonkeyPatch,
) -> None:
    from services.file_corpus_service import FileCorpusService
    from services.query_service import QueryService
    from services.retrieval_service import RetrievalService

    app = _make_app(monkeypatch)

    class BrokenCorpus(FileCorpusService):
        def query(
            self,
            query_text: str,
            filters: dict[str, Any] | None = None,
            limit: int = 10,
            chunk_memory_enabled: bool = False,
            query_embedding: list[float] | None = None,
        ) -> list[dict[str, Any]]:
            """Simulate an unavailable file corpus for degradation coverage."""
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
# 4. Capabilities endpoint returns both memory_store and file_corpus sections
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
