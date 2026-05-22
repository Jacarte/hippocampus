from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from pydantic import ValidationError

from api_models import (
    CapabilitiesResponse,
    FileHit,
    IndexResetRequest,
    IndexResetResponse,
    IndexStatusResponse,
    IndexSyncRequest,
    IndexSyncResponse,
    MemoryHit,
    UnifiedQueryRequest,
    UnifiedQueryResponse,
    WatchStartRequest,
    WatchStopRequest,
)


def test_file_hit_valid():
    hit = FileHit(
        path="/src/main.py",
        language="python",
        line_start=1,
        line_end=5,
        snippet="def foo(): pass",
        score=0.9,
    )
    assert hit.corpus == "file_corpus"
    assert hit.symbol_name is None


def test_memory_hit_valid():
    hit = MemoryHit(memory_id="abc123", content="some memory", score=0.8)
    assert hit.corpus == "memory_store"
    assert hit.metadata is None


def test_unified_query_request_defaults():
    req = UnifiedQueryRequest(query="test")
    assert req.limit == 10
    assert req.corpora == ["all"]


def test_unified_query_request_empty_string_raises():
    with pytest.raises(ValidationError):
        UnifiedQueryRequest(query="")


def test_unified_query_request_limit_bounds():
    with pytest.raises(ValidationError):
        UnifiedQueryRequest(query="x", limit=0)
    with pytest.raises(ValidationError):
        UnifiedQueryRequest(query="x", limit=51)


def test_unified_query_response_valid():
    resp = UnifiedQueryResponse(
        hits=[],
        total=0,
        corpora_queried=["memory_store"],
    )
    assert resp.available_hits_by_corpus == {}
    assert resp.degraded is False
    assert resp.degradation_reasons == []


def test_index_sync_request_valid():
    req = IndexSyncRequest(root="/workspace")
    assert req.root == "/workspace"


def test_index_sync_response_valid():
    now = datetime.now(timezone.utc)
    resp = IndexSyncResponse(
        root="/workspace", files_indexed=3, chunks_indexed=12, synced_at=now
    )
    assert resp.files_indexed == 3


def test_index_status_response_valid():
    resp = IndexStatusResponse(roots=[{"path": "/x"}], total_files=1, total_chunks=5)
    assert resp.total_chunks == 5


def test_index_reset_request_confirm_false_raises():
    with pytest.raises(ValidationError):
        IndexResetRequest(confirm=False)


def test_index_reset_request_default_raises():
    with pytest.raises(ValidationError):
        IndexResetRequest()


def test_index_reset_request_confirm_true_ok():
    req = IndexResetRequest(confirm=True)
    assert req.confirm is True


def test_index_reset_response_valid():
    now = datetime.now(timezone.utc)
    resp = IndexResetResponse(files_cleared=2, chunks_cleared=8, reset_at=now)
    assert resp.files_cleared == 2


def test_watch_start_request_valid():
    req = WatchStartRequest(root="/workspace")
    assert req.root == "/workspace"


def test_watch_stop_request_valid():
    req = WatchStopRequest(root="/workspace")
    assert req.root == "/workspace"


def test_capabilities_response_valid():
    resp = CapabilitiesResponse(
        memory_store={"enabled": True},
        file_corpus={"enabled": False},
    )
    assert resp.memory_store["enabled"] is True
