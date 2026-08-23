from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from pydantic import ValidationError

from api_models import (
    AdminMemoryCopyRequest,
    AdminMemoryCopyResponse,
    AdminMemoryCreateRequest,
    AdminMemoryCreateResponse,
    AdminMemoryDeleteResponse,
    AdminMemoryDetailResponse,
    AdminMemoryListItem,
    AdminMemoryListResponse,
    AdminMemoryUpdateRequest,
    AdminMemoryVisitRequest,
    AdminMemoryVisitResponse,
    AdminPopularityInfo,
    AdminFreshnessInfo,
    AdminAuditInfo,
    CopiedFromInfo,
    CapabilitiesResponse,
    FileHit,
    IndexResetResponse,
    IndexStatusResponse,
    IndexSyncResponse,
    MemoryHit,
    UnifiedQueryRequest,
    UnifiedQueryResponse,
)


def test_file_hit_valid():
    hit = FileHit(
        path="/src/main.py",
        language="python",
        line_start=1,
        line_end=5,
        snippet="def foo(): pass",
        score=0.9,
        datetime="2026-05-22T09:00:00Z",
    )
    assert hit.corpus == "file_corpus"
    assert hit.datetime == "2026-05-22T09:00:00Z"
    assert hit.symbol_name is None


def test_memory_hit_valid():
    hit = MemoryHit(
        memory_id="abc123",
        content="some memory",
        score=0.8,
        datetime="2026-05-21T11:00:00Z",
    )
    assert hit.corpus == "memory_store"
    assert hit.datetime == "2026-05-21T11:00:00Z"
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


def test_index_sync_response_valid():
    now = datetime.now(timezone.utc)
    resp = IndexSyncResponse(
        root="/workspace", files_indexed=3, chunks_indexed=12, synced_at=now
    )
    assert resp.files_indexed == 3


def test_index_status_response_valid():
    resp = IndexStatusResponse(roots=[{"path": "/x"}], total_files=1, total_chunks=5)
    assert resp.total_chunks == 5


def test_index_reset_response_valid():
    now = datetime.now(timezone.utc)
    resp = IndexResetResponse(files_cleared=2, chunks_cleared=8, reset_at=now)
    assert resp.files_cleared == 2


def test_removed_index_request_models_are_not_exported():
    import api_models

    removed_models = {
        "IndexSyncRequest",
        "WatchStartRequest",
        "WatchStopRequest",
        "IndexResetRequest",
    }
    assert all(not hasattr(api_models, name) for name in removed_models)


def test_capabilities_response_valid():
    resp = CapabilitiesResponse(
        memory_store={"enabled": True},
        file_corpus={"enabled": False},
    )
    assert resp.memory_store["enabled"] is True


# ---------------------------------------------------------------------------
# Admin model contract tests
# ---------------------------------------------------------------------------


def test_admin_popularity_info_defaults():
    pop = AdminPopularityInfo()
    assert pop.total_visits == 0
    assert pop.visit_ratio == 0.0


def test_admin_popularity_info_valid():
    pop = AdminPopularityInfo(total_visits=5, visit_ratio=0.5)
    assert pop.total_visits == 5
    assert pop.visit_ratio == 0.5


def test_admin_popularity_info_bounds():
    with pytest.raises(ValidationError):
        AdminPopularityInfo(total_visits=-1)
    with pytest.raises(ValidationError):
        AdminPopularityInfo(visit_ratio=-0.1)
    with pytest.raises(ValidationError):
        AdminPopularityInfo(visit_ratio=1.1)


def test_admin_freshness_info_defaults():
    f = AdminFreshnessInfo()
    assert f.last_visited_at is None
    assert f.never_visited is True
    assert f.created_at is None
    assert f.decay_half_life_days is None
    assert f.ttl_expires_at is None


def test_admin_freshness_info_visited():
    f = AdminFreshnessInfo(
        last_visited_at="2026-06-10T12:00:00Z",
        never_visited=False,
        created_at="2026-01-01T00:00:00Z",
        decay_half_life_days=30,
    )
    assert f.last_visited_at == "2026-06-10T12:00:00Z"
    assert f.never_visited is False
    assert f.decay_half_life_days == 30


def test_admin_freshness_info_negative_half_life_raises():
    with pytest.raises(ValidationError):
        AdminFreshnessInfo(decay_half_life_days=0)


def test_admin_audit_info_defaults():
    a = AdminAuditInfo()
    assert a.impersonated_by is None
    assert a.copied_from is None


def test_admin_audit_info_admin_action():
    a = AdminAuditInfo(
        impersonated_by="admin",
        copied_from={
            "source_memory_id": "mem-source-1",
            "source_scope": "user",
            "source_scope_id": "source-user",
        },
    )
    assert a.impersonated_by == "admin"
    assert a.copied_from is not None
    assert a.copied_from.source_memory_id == "mem-source-1"
    assert a.copied_from.source_scope == "user"
    assert a.copied_from.source_scope_id == "source-user"


def test_admin_memory_list_item_valid():
    item = AdminMemoryListItem(
        memory_id="mem-1",
        scope="user",
        scope_id="test-user",
        content="remember this",
        metadata={"type": "decision"},
        popularity=AdminPopularityInfo(total_visits=3, visit_ratio=0.6),
        freshness=AdminFreshnessInfo(
            last_visited_at="2026-06-10T12:00:00Z",
            never_visited=False,
            created_at="2026-01-01T00:00:00Z",
            decay_half_life_days=30,
        ),
    )
    assert item.memory_id == "mem-1"
    assert item.scope == "user"
    assert item.popularity.total_visits == 3
    assert item.freshness.never_visited is False


def test_admin_memory_list_response_valid():
    resp = AdminMemoryListResponse(
        items=[],
        page=1,
        page_size=20,
        total_items=0,
        total_pages=0,
    )
    assert resp.page == 1
    assert resp.total_items == 0


def test_admin_memory_list_response_page_bounds():
    with pytest.raises(ValidationError):
        AdminMemoryListResponse(
            items=[], page=0, page_size=20, total_items=0, total_pages=0
        )


def test_admin_memory_create_request_valid():
    req = AdminMemoryCreateRequest(
        scope="user",
        scope_id="test-user",
        messages=[{"role": "user", "content": "hello"}],
        metadata={"source": "admin"},
    )
    assert req.scope == "user"
    assert req.messages[0].role == "user"
    assert req.metadata == {"source": "admin"}


def test_admin_memory_create_request_invalid_scope():
    with pytest.raises(ValidationError):
        AdminMemoryCreateRequest(
            scope="invalid",
            scope_id="test-user",
            messages=[{"role": "user", "content": "hello"}],
        )


def test_admin_memory_create_request_empty_messages_raises():
    with pytest.raises(ValidationError):
        AdminMemoryCreateRequest(
            scope="user",
            scope_id="test-user",
            messages=[],
        )


def test_admin_memory_create_request_invalid_message_role_raises():
    with pytest.raises(ValidationError):
        AdminMemoryCreateRequest(
            scope="user",
            scope_id="test-user",
            messages=[{"role": "system", "content": "invalid"}],
        )


def test_admin_memory_update_request_invalid_message_role_raises():
    with pytest.raises(ValidationError):
        AdminMemoryUpdateRequest(
            messages=[{"role": "system", "content": "invalid"}],
            metadata={},
        )


def test_admin_memory_create_response_valid():
    resp = AdminMemoryCreateResponse(
        memory_id="mem-new",
        scope="user",
        scope_id="test-user",
        messages=[{"role": "user", "content": "hello"}],
        metadata={"source": "admin"},
    )
    assert resp.impersonated_by == "admin"


def test_admin_memory_detail_response_valid():
    resp = AdminMemoryDetailResponse(
        memory_id="mem-1",
        scope="agent",
        scope_id="agent-42",
        content="agent memory content",
        metadata={"type": "stable-fact"},
        popularity=AdminPopularityInfo(total_visits=1, visit_ratio=0.1),
        freshness=AdminFreshnessInfo(),
        audit=AdminAuditInfo(impersonated_by="admin"),
    )
    assert resp.memory_id == "mem-1"
    assert resp.scope == "agent"
    assert resp.audit.impersonated_by == "admin"
    assert resp.freshness.never_visited is True


def test_admin_memory_update_request_valid():
    req = AdminMemoryUpdateRequest(
        messages=[{"role": "assistant", "content": "updated"}],
        metadata={"source": "admin-edit"},
    )
    assert req.messages[0].role == "assistant"


def test_admin_memory_update_request_empty_messages_raises():
    with pytest.raises(ValidationError):
        AdminMemoryUpdateRequest(messages=[], metadata={})


def test_admin_memory_delete_response_valid():
    resp = AdminMemoryDeleteResponse(memory_id="mem-1")
    assert resp.memory_id == "mem-1"
    assert resp.deleted is True


def test_admin_copied_from_info_valid():
    info = CopiedFromInfo(
        source_memory_id="mem-src",
        source_scope="user",
        source_scope_id="source-user",
    )
    assert info.source_memory_id == "mem-src"
    assert info.source_scope == "user"
    assert info.source_scope_id == "source-user"


def test_admin_copied_from_info_invalid_scope_raises():
    with pytest.raises(ValidationError):
        CopiedFromInfo(
            source_memory_id="mem-src",
            source_scope="invalid",
            source_scope_id="x",
        )


def test_admin_memory_copy_request_valid():
    req = AdminMemoryCopyRequest(target_scope="user", target_scope_id="target-user")
    assert req.target_scope == "user"
    assert req.target_scope_id == "target-user"


def test_admin_memory_copy_request_invalid_scope_raises():
    with pytest.raises(ValidationError):
        AdminMemoryCopyRequest(target_scope="invalid", target_scope_id="x")


def test_admin_memory_copy_response_valid():
    resp = AdminMemoryCopyResponse(
        source_memory_id="mem-src",
        target_memory_id="mem-tgt",
        target_scope="user",
        target_scope_id="target-user",
        copied_from={
            "source_memory_id": "mem-src",
            "source_scope": "user",
            "source_scope_id": "source-user",
        },
    )
    assert resp.impersonated_by == "admin"
    assert resp.source_memory_id == "mem-src"
    assert resp.copied_from.source_memory_id == "mem-src"
    assert resp.copied_from.source_scope == "user"
    assert resp.copied_from.source_scope_id == "source-user"


def test_admin_memory_visit_request_valid():
    req = AdminMemoryVisitRequest(reason="detail_open")
    assert req.reason == "detail_open"


def test_admin_memory_visit_request_invalid_reason_raises():
    with pytest.raises(ValidationError):
        AdminMemoryVisitRequest(reason="invalid_reason")


def test_admin_memory_visit_response_valid():
    resp = AdminMemoryVisitResponse(
        memory_id="mem-1",
        total_visits=5,
        last_visited_at="2026-06-10T12:00:00Z",
        reason="detail_open",
    )
    assert resp.total_visits == 5
    assert resp.reason == "detail_open"


def test_removed_admin_index_models_are_not_exported():
    import api_models

    removed_models = {
        "AdminIndexRootInfo",
        "AdminIndexJobInfo",
        "AdminIndexFileInfo",
        "AdminIndexLimits",
        "AdminIndexVisibilityInputs",
        "AdminIndexOverviewResponse",
    }
    assert all(not hasattr(api_models, name) for name in removed_models)
