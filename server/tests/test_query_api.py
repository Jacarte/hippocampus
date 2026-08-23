import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.file_corpus_service import FileCorpusService
from services.query_service import QueryService
from services.retrieval_service import RetrievalService


class FakeRetrieval:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = results

    def search(self, memory_instance: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return self._results


class ErrorRetrieval:
    def search(self, memory_instance: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("memory unavailable")


class FakeMemoryBackend:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = results
        self.last_search_kwargs: dict[str, Any] | None = None

    def search(
        self,
        query: str,
        *,
        top_k: int = 100,
        filters: dict[str, Any] | None = None,
        threshold: float | None = None,
        rerank: bool = True,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        # mem0 2.0.0 explicit kwargs: entity-id keys live INSIDE ``filters``,
        # ``top_k`` replaces the legacy ``limit`` kwarg.  Record what the
        # service actually passes so the QueryService contract tests below
        # assert against the 2.0.0 surface.
        self.last_search_kwargs = {
            "query": query,
            "top_k": top_k,
            "filters": filters,
            "threshold": threshold,
            "rerank": rerank,
        }
        return {"results": self._results}

    def get_all(
        self,
        *,
        user_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._results


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


def _fake_mem0_search_result() -> dict[str, Any]:
    return {
        "id": "mem-1",
        "memory": "hello from memory",
        "score": 0.9,
        "created_at": "2026-05-20T10:00:00Z",
        "metadata": None,
    }


def test_query_returns_fused_results() -> None:
    corpus = _make_corpus_with_chunks()
    retrieval = FakeRetrieval([_fake_memory_result()])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result = svc.query("hello", corpora=["all"], memory_instance=object())

    assert result["total"] == 3
    assert result["available_hits_by_corpus"] == {
        "file_corpus": 2,
        "memory_store": 1,
    }
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


def test_query_memory_store_works_with_real_retrieval_service() -> None:
    corpus = _make_corpus_with_chunks()
    memory_backend = FakeMemoryBackend([_fake_mem0_search_result()])
    svc = QueryService(corpus=corpus, retrieval_service=RetrievalService())

    result = svc.query(
        "hello", corpora=["memory_store"], memory_instance=memory_backend
    )

    assert result["degraded"] is False
    assert result["degradation_reasons"] == []
    assert result["available_hits_by_corpus"] == {"memory_store": 1}
    assert len(result["hits"]) == 1
    assert result["hits"][0]["corpus"] == "memory_store"
    assert result["hits"][0]["datetime"] == "2026-05-20T10:00:00Z"
    assert memory_backend.last_search_kwargs is not None
    assert memory_backend.last_search_kwargs["top_k"] == 10


def test_query_memory_hit_uses_updated_at_when_available() -> None:
    corpus = _make_corpus_with_chunks()
    retrieval = FakeRetrieval(
        [
            {
                "id": "mem-2",
                "memory": "newer memory",
                "score": 0.8,
                "created_at": "2026-05-20T10:00:00Z",
                "updated_at": "2026-05-21T10:00:00Z",
                "metadata": None,
            }
        ]
    )
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result = svc.query("newer", corpora=["memory_store"], memory_instance=object())

    assert result["hits"][0]["datetime"] == "2026-05-21T10:00:00Z"


def test_query_file_hit_returns_indexed_datetime_when_available() -> None:
    corpus = FileCorpusService()
    corpus.upsert_chunks(
        root="/repo",
        file_path="dated.py",
        chunks=[
            {
                "language": "python",
                "symbol_name": "dated_fn",
                "symbol_kind": "function",
                "line_start": 1,
                "line_end": 2,
                "content": "hello dated world",
                "indexed_at": "2026-05-19T12:00:00Z",
            }
        ],
    )
    svc = QueryService(corpus=corpus, retrieval_service=FakeRetrieval([]))

    result = svc.query("dated", corpora=["file_corpus"])

    assert result["hits"][0]["datetime"] == "2026-05-19T12:00:00Z"


def test_query_memory_store_forwards_custom_limit_to_real_retrieval_service() -> None:
    corpus = _make_corpus_with_chunks()
    memory_backend = FakeMemoryBackend([_fake_mem0_search_result()])
    svc = QueryService(corpus=corpus, retrieval_service=RetrievalService())

    result = svc.query(
        "hello",
        corpora=["memory_store"],
        memory_instance=memory_backend,
        limit=3,
    )

    assert result["hits"][0]["corpus"] == "memory_store"
    assert memory_backend.last_search_kwargs is not None
    assert memory_backend.last_search_kwargs["top_k"] == 3


def test_query_attaches_memory_hits_ahead_of_file_hits() -> None:
    corpus = FileCorpusService()
    corpus.upsert_chunks(
        root="/repo",
        file_path="foo.py",
        chunks=[
            {
                "language": "python",
                "symbol_name": f"func_{i}",
                "symbol_kind": "function",
                "line_start": i,
                "line_end": i,
                "content": "alpha beta gamma",
            }
            for i in range(1, 13)
        ],
    )
    memory_backend = FakeMemoryBackend(
        [
            {
                "id": "mem-1",
                "memory": "alpha memory",
                "score": 0.6,
                "metadata": None,
            }
        ]
    )
    svc = QueryService(corpus=corpus, retrieval_service=RetrievalService())

    result = svc.query(
        "alpha",
        corpora=["all"],
        memory_instance=memory_backend,
        limit=10,
        min_score_memory=0.0,
        min_score_files=0.0,
    )

    assert result["available_hits_by_corpus"] == {
        "file_corpus": 10,
        "memory_store": 1,
    }
    assert len(result["hits"]) == 11
    assert result["hits"][0]["corpus"] == "memory_store"
    assert all(hit["corpus"] == "file_corpus" for hit in result["hits"][1:])


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
    result_flag_off = svc.query(
        "hello", corpora=["file_corpus"], chunk_memory_enabled=False
    )

    assert result_without_flag["hits"] == result_flag_off["hits"]
    assert result_without_flag["total"] == result_flag_off["total"]


def test_chunk_memory_enabled_includes_summary_matched_chunks() -> None:
    """Enabled chunk memory retrieves a lexical miss via summary embedding."""
    corpus = _make_corpus_with_summaries()
    retrieval = FakeRetrieval([])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result = svc.query(
        "authentication",
        corpora=["file_corpus"],
        chunk_memory_enabled=True,
        query_embedding=[1.0, 0.0, 0.0],
    )

    assert [hit["symbol_name"] for hit in result["hits"]] == ["fn_with_summary"]


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
        def query_with_summaries(
            self, *args: Any, **kwargs: Any
        ) -> list[dict[str, Any]]:  # type: ignore[override]
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


def test_query_filters_hits_below_min_score() -> None:
    """Hits with score below min_score must be excluded from results."""
    corpus = FileCorpusService()
    corpus.upsert_chunks(
        root="/repo",
        file_path="bar.py",
        chunks=[
            {
                "language": "python",
                "symbol_name": "low_score_func",
                "symbol_kind": "function",
                "line_start": 1,
                "line_end": 3,
                "content": "threshold test low",
            },
        ],
    )
    low_mem = {
        "id": "mem-low",
        "memory": "threshold test low",
        "_retrieval": {"score": 0.3},
        "metadata": None,
    }
    ok_mem = {
        "id": "mem-ok",
        "memory": "threshold test ok",
        "_retrieval": {"score": 0.5},
        "metadata": None,
    }

    retrieval = FakeRetrieval([low_mem, ok_mem])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result = svc.query(
        "threshold test",
        corpora=["memory_store"],
        min_score_memory=0.5,
        memory_instance=object(),
    )

    scores = [h["score"] for h in result["hits"]]
    assert all(s >= 0.5 for s in scores), f"Expected all scores >= 0.5, got {scores}"
    assert any(h["memory_id"] == "mem-ok" for h in result["hits"])
    assert not any(h["memory_id"] == "mem-low" for h in result["hits"])


def test_query_all_filtered_returns_empty_hits() -> None:
    """When every hit is below min_score, hits must be an empty list."""
    retrieval = FakeRetrieval(
        [
            {
                "id": "m1",
                "memory": "low",
                "_retrieval": {"score": 0.1},
                "metadata": None,
            },
        ]
    )
    svc = QueryService(corpus=FileCorpusService(), retrieval_service=retrieval)

    result = svc.query(
        "low", corpora=["memory_store"], min_score_memory=0.9, memory_instance=object()
    )

    assert result["hits"] == []
    assert result["total"] == 1  # total reflects pre-filter count


def test_query_min_score_zero_returns_all_hits() -> None:
    """min_score=0.0 must not filter anything."""
    corpus = _make_corpus_with_chunks()
    retrieval = FakeRetrieval([_fake_memory_result()])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result = svc.query(
        "hello",
        corpora=["all"],
        min_score_memory=0.0,
        min_score_files=0.0,
        memory_instance=object(),
    )

    assert len(result["hits"]) == 3  # all three survive


def test_query_default_min_score_is_0_5() -> None:
    """Calling query() without min_score must apply the 0.5 default."""
    low_mem = {
        "id": "low",
        "memory": "hello low",
        "_retrieval": {"score": 0.2},
        "metadata": None,
    }
    high_mem = {
        "id": "high",
        "memory": "hello high",
        "_retrieval": {"score": 0.8},
        "metadata": None,
    }
    retrieval = FakeRetrieval([low_mem, high_mem])
    svc = QueryService(corpus=FileCorpusService(), retrieval_service=retrieval)

    result = svc.query("hello", corpora=["memory_store"], memory_instance=object())

    ids = [h["memory_id"] for h in result["hits"]]
    assert "high" in ids
    assert "low" not in ids


def test_query_files_filtered_by_min_score_files() -> None:
    """File hits below min_score_files must be excluded."""
    corpus = FileCorpusService()
    corpus.upsert_chunks(
        root="/repo",
        file_path="baz.py",
        chunks=[
            {
                "language": "python",
                "symbol_name": "high_func",
                "symbol_kind": "function",
                "line_start": 1,
                "line_end": 3,
                "content": (
                    "threshold file test extra padding words to dilute the tf score "
                    "so it stays well below half and we can filter it reliably"
                ),
            },
        ],
    )
    retrieval = FakeRetrieval([])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    result_all = svc.query(
        "threshold file test", corpora=["file_corpus"], min_score_files=0.0
    )
    result_none = svc.query(
        "threshold file test", corpora=["file_corpus"], min_score_files=0.9
    )

    assert len(result_all["hits"]) >= 1
    assert result_none["hits"] == []


def test_query_default_min_score_files_is_0_05() -> None:
    """Default min_score_files=0.05 filters file hits with score < 0.05."""
    corpus = FileCorpusService()
    retrieval = FakeRetrieval([])
    svc = QueryService(corpus=corpus, retrieval_service=retrieval)

    # Query for something with no match → score will be 0 or very low → filtered by default 0.05
    result = svc.query("zzz_no_match_at_all", corpora=["file_corpus"])
    # All hits should have score >= 0.05 (any zero-score hits are excluded)
    for h in result["hits"]:
        assert h["score"] >= 0.05, (
            f"File hit score {h['score']} below default min_score_files=0.05"
        )
