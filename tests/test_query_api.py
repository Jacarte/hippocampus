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

    def search(self, memory_instance: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return self._results


class ErrorRetrieval:
    def search(self, memory_instance: Any, **_kwargs: Any) -> list[dict[str, Any]]:
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

    result = svc.query("hello", corpora=["all"], memory_instance=object())

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

    result = svc.query("hello", corpora=["memory_store"], memory_instance=object())

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

    result = svc.query("hello", corpora=["all"], memory_instance=object())

    assert result["degraded"] is True
    assert any("file_corpus" in r for r in result["degradation_reasons"])
    assert len(result["hits"]) == 1
    assert result["hits"][0]["corpus"] == "memory_store"


def test_query_degrades_gracefully_on_memory_error() -> None:
    corpus = _make_corpus_with_chunks()
    svc = QueryService(corpus=corpus, retrieval_service=ErrorRetrieval())

    result = svc.query("hello", corpora=["all"], memory_instance=object())

    assert result["degraded"] is True
    assert any("memory_store" in r for r in result["degradation_reasons"])
    assert len(result["hits"]) == 2
    assert all(h["corpus"] == "file_corpus" for h in result["hits"])


def _make_corpus_with_summaries() -> FileCorpusService:
    corpus = FileCorpusService()
    corpus.upsert_chunks(
        root="/repo",
        file_path="bar.py",
        chunks=[
            {
                "language": "python",
                "symbol_name": "fn_with_summary",
                "symbol_kind": "function",
                "line_start": 1,
                "line_end": 5,
                "content": "irrelevant content",
                "summary_text": "This function does authentication",
                "summary_embedding": [1.0, 0.0, 0.0],
            },
            {
                "language": "python",
                "symbol_name": "fn_no_summary",
                "symbol_kind": "function",
                "line_start": 10,
                "line_end": 15,
                "content": "irrelevant content",
            },
            {
                "language": "python",
                "symbol_name": "fn_with_low_score",
                "symbol_kind": "function",
                "line_start": 20,
                "line_end": 25,
                "content": "irrelevant content",
                "summary_text": "This function does something unrelated",
                "summary_embedding": [0.0, 1.0, 0.0],
            },
        ],
    )
    return corpus


def test_chunk_memory_disabled_returns_lexical_only() -> None:
    """With chunk_memory_enabled=False, results are identical to baseline lexical."""
    corpus = FileCorpusService()
    corpus.upsert_chunks(
        root="/repo",
        file_path="x.py",
        chunks=[
            {"content": "hello world", "line_start": 1, "line_end": 1},
            {"content": "goodbye world", "line_start": 2, "line_end": 2},
        ],
    )
    retrieval = FakeRetrieval([])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result_without_flag = svc.query("hello", corpora=["file_corpus"])
    result_flag_off = svc.query("hello", corpora=["file_corpus"], chunk_memory_enabled=False)

    assert result_without_flag["hits"] == result_flag_off["hits"]
    assert result_without_flag["total"] == result_flag_off["total"]


def test_chunk_memory_enabled_includes_summary_matched_chunks() -> None:
    """With chunk_memory_enabled=True, chunks matching via summary_embedding are included."""
    corpus = _make_corpus_with_summaries()
    retrieval = FakeRetrieval([])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result = svc.query(
        "irrelevant content",
        corpora=["file_corpus"],
        chunk_memory_enabled=True,
        query_embedding=[1.0, 0.0, 0.0],
    )

    symbol_names = [h["symbol_name"] for h in result["hits"]]
    assert "fn_with_summary" in symbol_names


def test_chunk_memory_mixed_corpus_no_crash() -> None:
    """Chunks without summary_embedding do not cause errors when chunk_memory_enabled=True."""
    corpus = _make_corpus_with_summaries()
    retrieval = FakeRetrieval([])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result = svc.query(
        "irrelevant content",
        corpora=["file_corpus"],
        chunk_memory_enabled=True,
        query_embedding=[1.0, 0.0, 0.0],
    )
    assert not result["degraded"]


def test_chunk_memory_summary_error_falls_back_to_lexical() -> None:
    """If summary retrieval raises, result is not degraded and falls back to lexical."""

    class BustedSummaryCorpus(FileCorpusService):
        def query_with_summaries(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:  # type: ignore[override]
            raise RuntimeError("embedding service down")

    corpus = BustedSummaryCorpus()
    corpus.upsert_chunks(
        root="/repo",
        file_path="y.py",
        chunks=[
            {"content": "hello world", "line_start": 1, "line_end": 1},
        ],
    )
    retrieval = FakeRetrieval([])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result = svc.query(
        "hello",
        corpora=["file_corpus"],
        chunk_memory_enabled=True,
        query_embedding=[1.0, 0.0, 0.0],
    )

    assert len(result["hits"]) >= 1
    assert not result["degraded"]


def test_chunk_memory_disabled_flag_off_exact_baseline() -> None:
    """Results with chunk_memory_enabled=False must be byte-for-byte identical to baseline."""
    corpus = _make_corpus_with_chunks()
    retrieval = FakeRetrieval([_fake_memory_result()])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result_baseline = svc.query("hello", corpora=["all"])
    result_flag_off = svc.query("hello", corpora=["all"], chunk_memory_enabled=False)

    assert result_baseline == result_flag_off


def test_chunk_memory_enabled_derives_embedding_from_memory_instance() -> None:
    """When chunk_memory_enabled=True and no query_embedding is supplied,
    the embedding is derived automatically via memory_instance.embedding_model.embed."""
    from unittest.mock import MagicMock

    corpus = FileCorpusService()
    summary_vec = [1.0, 0.0, 0.0]
    corpus.upsert_chunks(
        root="/repo",
        file_path="bar.py",
        chunks=[
            {
                "id": "bar-1",
                "content": "def unit_test(): pass",
                "language": "python",
                "line_start": 1,
                "line_end": 1,
                "summary_text": "A unit test function",
                "summary_embedding": summary_vec,
            }
        ],
    )

    memory_instance = MagicMock()
    memory_instance.embedding_model.embed.return_value = summary_vec

    svc = QueryService(corpus=corpus, retrieval_service=FakeRetrieval([]))

    result = svc.query(
        "unit test",
        corpora=["file_corpus"],
        chunk_memory_enabled=True,
        memory_instance=memory_instance,
    )

    memory_instance.embedding_model.embed.assert_called_once_with("unit test")
    assert result["total"] >= 1
    assert any(h["path"] == "bar.py" for h in result["hits"])


def test_chunk_memory_enabled_embed_failure_falls_back_to_lexical() -> None:
    """When embedding derivation raises, the query falls back to lexical results without error."""
    from unittest.mock import MagicMock

    corpus = FileCorpusService()
    corpus.upsert_chunks(
        root="/repo",
        file_path="baz.py",
        chunks=[
            {
                "id": "baz-1",
                "content": "hello world lexical match",
                "language": "python",
                "line_start": 1,
                "line_end": 1,
            }
        ],
    )

    memory_instance = MagicMock()
    memory_instance.embedding_model.embed.side_effect = RuntimeError("embedder down")

    svc = QueryService(corpus=corpus, retrieval_service=FakeRetrieval([]))

    result = svc.query(
        "hello",
        corpora=["file_corpus"],
        chunk_memory_enabled=True,
        memory_instance=memory_instance,
    )

    assert result["degraded"] is False
    assert result["total"] >= 1
