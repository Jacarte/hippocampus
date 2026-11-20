"""``scope: null`` opts out of the scope filter that /retrieve derives from ``scopes``."""

from services.retrieval_service import RetrievalService


def _service() -> RetrievalService:
    return RetrievalService.__new__(RetrievalService)


def test_scopes_become_scope_filter_by_default() -> None:
    assert _service()._combine_filters(None, ["project"]) == {"scope": "project"}
    assert _service()._combine_filters({"source": "chat"}, ["a", "b"]) == {
        "source": "chat",
        "scope": ["a", "b"],
    }


def test_explicit_null_scope_removes_the_scope_filter() -> None:
    assert _service()._combine_filters({"scope": None}, ["project"]) is None
    assert _service()._combine_filters({"scope": None, "source": "chat"}, ["project"]) == {
        "source": "chat"
    }


def test_explicit_scope_filter_is_kept() -> None:
    assert _service()._combine_filters({"scope": "user"}, ["project"]) == {"scope": "user"}
