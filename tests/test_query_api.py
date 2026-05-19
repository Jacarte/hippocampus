import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.file_corpus_service import FileCorpusService
from services.query_service import QueryService


class FakeRetrieval:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = results

    def search(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return self._results


class ErrorRetrieval:
    def search(self, **_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("memory unavailable")


def _make_corpus_with_chunks() -> FileCorpusService:
    corpus = FileCorpusService()
    corpus.upsert_chunks(
        root="/repo",
        file_path="foo.py",
        chunks=[
            {
                "language": "python",
                "symbol_name": "func_a",
                "symbol_kind": "function",
                "line_start": 1,
                "line_end": 5,
                "content": "hello world",
            },
            {
                "language": "python",
                "symbol_name": "func_b",
                "symbol_kind": "function",
                "line_start": 10,
                "line_end": 15,
                "content": "hello again",
            },
        ],
    )
    return corpus


def _fake_memory_result() -> dict[str, Any]:
    return {
        "id": "mem-1",
        "memory": "hello from memory",
        "_retrieval": {"score": 0.9},
        "metadata": None,
    }


def test_query_returns_fused_results() -> None:
    corpus = _make_corpus_with_chunks()
    retrieval = FakeRetrieval([_fake_memory_result()])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result = svc.query("hello", corpora=["all"])

    assert result["total"] == 3
    assert len(result["hits"]) == 3
    corpora_in_hits = {h["corpus"] for h in result["hits"]}
    assert "file_corpus" in corpora_in_hits
    assert "memory_store" in corpora_in_hits
    assert not result["degraded"]


def test_query_file_corpus_only() -> None:
    corpus = _make_corpus_with_chunks()
    retrieval = FakeRetrieval([_fake_memory_result()])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result = svc.query("hello", corpora=["file_corpus"])

    assert len(result["hits"]) == 2
    assert all(h["corpus"] == "file_corpus" for h in result["hits"])
    assert result["corpora_queried"] == ["file_corpus"]


def test_query_memory_only() -> None:
    corpus = _make_corpus_with_chunks()
    retrieval = FakeRetrieval([_fake_memory_result()])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result = svc.query("hello", corpora=["memory_store"])

    assert len(result["hits"]) == 1
    assert result["hits"][0]["corpus"] == "memory_store"
    assert result["hits"][0]["memory_id"] == "mem-1"
    assert result["corpora_queried"] == ["memory_store"]


def test_query_degrades_gracefully_on_file_corpus_error() -> None:
    class ErrorCorpus:
        def query(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError("disk error")

    retrieval = FakeRetrieval([_fake_memory_result()])
    svc = QueryService(corpus=ErrorCorpus(), retrieval_service=retrieval)  # type: ignore[arg-type]

    result = svc.query("hello", corpora=["all"])

    assert result["degraded"] is True
    assert any("file_corpus" in r for r in result["degradation_reasons"])
    assert len(result["hits"]) == 1
    assert result["hits"][0]["corpus"] == "memory_store"


def test_query_degrades_gracefully_on_memory_error() -> None:
    corpus = _make_corpus_with_chunks()
    svc = QueryService(corpus=corpus, retrieval_service=ErrorRetrieval())

    result = svc.query("hello", corpora=["all"])

    assert result["degraded"] is True
    assert any("memory_store" in r for r in result["degradation_reasons"])
    assert len(result["hits"]) == 2
    assert all(h["corpus"] == "file_corpus" for h in result["hits"])
