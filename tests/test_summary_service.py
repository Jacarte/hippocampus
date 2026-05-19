from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.summary_service import SummaryResult, SummaryService


def _make_service(llm_response=None, llm_raises=None, embed_response=None, embed_raises=None):
    memory = MagicMock()
    if llm_raises:
        memory.llm.generate_response.side_effect = llm_raises
    else:
        memory.llm.generate_response.return_value = llm_response if llm_response is not None else "good summary"
    if embed_raises:
        memory.embedding_model.embed.side_effect = embed_raises
    else:
        memory.embedding_model.embed.return_value = embed_response if embed_response is not None else [0.1, 0.2]
    return SummaryService(memory)


def test_summary_service_returns_text_and_embedding_on_success():
    svc = _make_service(llm_response="good summary", embed_response=[0.1, 0.2])
    result = svc.generate_summary("def foo(): pass", chunk_name="foo")
    assert result.summary_text == "good summary"
    assert result.summary_embedding == [0.1, 0.2]


def test_summary_service_returns_none_on_empty_summary():
    svc = _make_service(llm_response="")
    result = svc.generate_summary("def foo(): pass")
    assert result == SummaryResult()
    assert result.summary_text is None
    assert result.summary_embedding is None


def test_summary_service_returns_none_on_whitespace_summary():
    svc = _make_service(llm_response="   ")
    result = svc.generate_summary("def foo(): pass")
    assert result == SummaryResult()
    assert result.summary_text is None
    assert result.summary_embedding is None


def test_summary_service_returns_none_on_llm_failure():
    svc = _make_service(llm_raises=RuntimeError("LLM unavailable"))
    result = svc.generate_summary("def foo(): pass", chunk_name="foo")
    assert result == SummaryResult()
    assert result.summary_text is None
    assert result.summary_embedding is None


def test_summary_service_returns_none_on_embedding_failure():
    svc = _make_service(llm_response="good summary", embed_raises=RuntimeError("embed error"))
    result = svc.generate_summary("def bar(): pass", chunk_name="bar")
    assert result.summary_text is None
    assert result.summary_embedding is None
