"""Tests for the admin service scaffold and dedicated visit telemetry store.

These tests prove two things the Task 2 plan requires:

* Persisted visit telemetry survives fresh service instances pointed at the
  same ``MEM0_VISIT_DB_PATH`` SQLite file (covers both
  :class:`services.visit_store.VisitStore` and
  :class:`services.admin_service.AdminService.record_visit`).
* :class:`AdminService` is the single place that attaches the
  ``impersonated_by=admin`` audit stamp and the
  ``copied_from={ source_memory_id, source_scope, source_scope_id }``
  provenance object to admin-initiated writes.

The retained list/detail/create/update/copy/visit methods are verified through
their service behavior and validation contracts below.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from api_models import (
    AdminAuditInfo,
    AdminFreshnessInfo,
    AdminMemoryCopyRequest,
    AdminMemoryCreateRequest,
    AdminMemoryUpdateRequest,
    AdminMessage,
    AdminPopularityInfo,
    VisitReason,
)

from services.admin_service import ADMIN_IMPERSONATOR, AdminService
from services.visit_store import VisitAggregates, VisitEvent, VisitStore


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_temp_db_path() -> str:
    """Return a writable path inside a fresh temporary directory."""
    tmp = tempfile.mkdtemp(prefix="mem0-admin-test-")
    return os.path.join(tmp, "visits.db")


class _FakeMemory:
    """Minimal in-memory mem0 stand-in for AdminService write paths.

    The admin service only requires the methods it actually calls; we
    record arguments so the test can assert audit-stamping behavior.
    """

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.adds: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.deletes: list[str] = []

    def add(self, *, messages, metadata=None, user_id=None, agent_id=None, run_id=None):
        # The admin service infers scope from metadata; mirror how mem0
        # records the scope identifier inside the metadata dict.
        record_metadata = dict(metadata or {})
        if user_id is not None:
            record_metadata.setdefault("user_id", user_id)
        if agent_id is not None:
            record_metadata.setdefault("agent_id", agent_id)
        if run_id is not None:
            record_metadata.setdefault("run_id", run_id)
        record = {
            "id": f"mem-{len(self.records) + 1}",
            "memory": messages[0].get("content", "") if messages else "",
            "messages": list(messages),
            "metadata": record_metadata,
            "user_id": user_id,
            "agent_id": agent_id,
            "run_id": run_id,
        }
        self.records[record["id"]] = record
        self.adds.append(record)
        return record

    def get(self, memory_id):
        return self.records.get(memory_id)

    def get_all(self, *, user_id=None, agent_id=None, run_id=None):
        results = []
        for record in self.records.values():
            if user_id is not None and record.get("user_id") == user_id:
                results.append(record)
                continue
            if agent_id is not None and record.get("agent_id") == agent_id:
                results.append(record)
                continue
            if run_id is not None and record.get("run_id") == run_id:
                results.append(record)
                continue
        return results

    def update(self, memory_id, data, metadata=None):
        record = self.records.get(memory_id)
        if record is None:
            return None
        record["memory"] = data if isinstance(data, str) else data.get("memory", "")
        if metadata is not None:
            record["metadata"] = dict(metadata)
        self.updates.append({"memory_id": memory_id, "data": data, "metadata": metadata})
        return record

    def delete(self, memory_id):
        if memory_id not in self.records:
            return
        self.deletes.append(memory_id)
        del self.records[memory_id]


# ---------------------------------------------------------------------------
# VisitStore: schema and persistence guarantees
# ---------------------------------------------------------------------------


def test_visit_store_records_event_and_returns_aggregates():
    """A single record_visit call must persist both the event and aggregate row."""
    store = VisitStore(path=":memory:")
    aggregate = store.record_visit("mem-1", reason="detail_open")
    assert isinstance(aggregate, VisitAggregates)
    assert aggregate.memory_id == "mem-1"
    assert aggregate.total_visits == 1
    assert aggregate.last_visited_at is not None
    assert aggregate.never_visited is False


def test_visit_store_total_visits_increment_across_calls():
    """Repeated visits on the same memory must increment the counter."""
    store = VisitStore(path=":memory:")
    for _ in range(3):
        store.record_visit("mem-1", reason="detail_open")
    aggregate = store.get_aggregates("mem-1")
    assert aggregate.total_visits == 3


def test_visit_store_unknown_memory_returns_zero_aggregate():
    store = VisitStore(path=":memory:")
    aggregate = store.get_aggregates("never-recorded")
    assert aggregate.total_visits == 0
    assert aggregate.last_visited_at is None
    assert aggregate.never_visited is True


def test_visit_store_list_events_orders_newest_first():
    store = VisitStore(path=":memory:")
    store.record_visit(
        "mem-1", reason="detail_open", visited_at="2026-01-01T00:00:00Z"
    )
    store.record_visit(
        "mem-1", reason="edit_save", visited_at="2026-01-02T00:00:00Z"
    )
    store.record_visit(
        "mem-1", reason="copy_source", visited_at="2026-01-03T00:00:00Z"
    )
    events = store.list_events("mem-1")
    assert [event.reason for event in events] == [
        "copy_source",
        "edit_save",
        "detail_open",
    ]
    for event in events:
        assert isinstance(event, VisitEvent)
        assert event.event_id is not None


def test_visit_store_rejects_empty_memory_id():
    store = VisitStore(path=":memory:")
    with pytest.raises(ValueError):
        store.record_visit("", reason="detail_open")


def test_visit_store_rejects_empty_reason():
    store = VisitStore(path=":memory:")
    with pytest.raises(ValueError):
        store.record_visit("mem-1", reason="")


def test_visit_store_defaults_to_mem0_visit_db_path(monkeypatch):
    """When no path is given, VisitStore must use MEM0_VISIT_DB_PATH."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", "/var/lib/mem0/from-env.db")
    store = VisitStore()
    assert store.path == "/var/lib/mem0/from-env.db"


def test_visit_store_persists_across_fresh_instances():
    """Two VisitStore instances at the same path must see the same data."""
    db_path = _make_temp_db_path()
    try:
        first = VisitStore(path=db_path)
        first.record_visit("mem-shared", reason="detail_open")
        first.record_visit("mem-shared", reason="edit_save")

        # A brand-new instance with no shared state must still see the data.
        second = VisitStore(path=db_path)
        aggregate = second.get_aggregates("mem-shared")
        assert aggregate.total_visits == 2
        assert aggregate.last_visited_at is not None

        events = second.list_events("mem-shared")
        assert [event.reason for event in events] == ["edit_save", "detail_open"]
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_visit_store_creates_missing_parent_directory():
    """Regression: a file-backed path under a non-existent parent directory
    must open without ``sqlite3.OperationalError: unable to open database
    file``.

    This reproduces the manual-QA failure observed when the default
    ``MEM0_VISIT_DB_PATH=/var/lib/mem0/visits.db`` is used on a fresh
    host that has no ``/var/lib/mem0`` directory.  The store is expected
    to create the parent directory transparently and still persist
    visits, just like an in-memory store would.
    """
    tmp_root = tempfile.mkdtemp(prefix="mem0-visit-parent-")
    try:
        nested_path = os.path.join(tmp_root, "deeply", "nested", "dir", "visits.db")
        # Sanity: the parent must not exist before we open the store.
        assert not os.path.exists(os.path.dirname(nested_path))

        store = VisitStore(path=nested_path)
        aggregate = store.record_visit("mem-mkdir", reason="detail_open")
        assert aggregate.total_visits == 1
        assert aggregate.last_visited_at is not None

        # The parent directory and database file must now exist on disk.
        assert os.path.isdir(os.path.dirname(nested_path))
        assert os.path.isfile(nested_path)

        # A second call must also succeed and increment the counter.
        store.record_visit("mem-mkdir", reason="edit_save")
        assert store.get_aggregates("mem-mkdir").total_visits == 2

        # A fresh store pointing at the same path must see the persisted data
        # — proving the file is durable, not just an in-process side effect.
        reopened = VisitStore(path=nested_path)
        assert reopened.get_aggregates("mem-mkdir").total_visits == 2
    finally:
        import shutil

        shutil.rmtree(tmp_root, ignore_errors=True)


def test_visit_store_ensure_parent_dir_no_op_for_special_paths():
    """``_ensure_parent_dir`` must short-circuit for special SQLite paths."""
    import sqlite3 as _sqlite3

    from services.visit_store import VisitStore

    # ``:memory:`` already short-circuits at the top of ``_open`` and never
    # reaches the helper — but calling the helper directly is still safe.
    VisitStore._ensure_parent_dir(":memory:")  # no exception
    VisitStore._ensure_parent_dir("")  # no exception
    VisitStore._ensure_parent_dir("visits.db")  # no parent component, no-op
    VisitStore._ensure_parent_dir("file::memory:")  # URI scheme guard


def test_visit_store_get_aggregates_for_memories_returns_zero_for_missing_ids():
    store = VisitStore(path=":memory:")
    store.record_visit("mem-known", reason="detail_open")
    aggregates = store.get_aggregates_for_memories(["mem-known", "mem-missing"])
    assert aggregates["mem-known"].total_visits == 1
    assert aggregates["mem-missing"].total_visits == 0
    assert aggregates["mem-missing"].never_visited is True


def test_visit_store_max_total_visits_reflects_peak_counter():
    store = VisitStore(path=":memory:")
    store.record_visit("mem-1", reason="detail_open")
    store.record_visit("mem-1", reason="detail_open")
    store.record_visit("mem-2", reason="detail_open")
    assert store.max_total_visits() == 2


# ---------------------------------------------------------------------------
# AdminService: audit-stamping rules
# ---------------------------------------------------------------------------


def _make_admin_service(path: str | None = None) -> AdminService:
    return AdminService(visit_store=VisitStore(path=path or ":memory:"))


def test_admin_service_stamps_impersonated_by_on_create():
    """create_memory must attach impersonated_by=admin to the persisted metadata."""
    service = _make_admin_service()
    memory = _FakeMemory()
    payload = AdminMemoryCreateRequest(
        scope="user",
        scope_id="test-user",
        messages=[AdminMessage(role="user", content="remember alpha")],
    )
    result = service.create_memory(memory, payload)
    assert result["impersonated_by"] == ADMIN_IMPERSONATOR
    assert result["impersonated_by"] == "admin"
    assert memory.adds, "FakeMemory.add should have been called"
    stored_metadata = memory.adds[0]["metadata"]
    assert stored_metadata["impersonated_by"] == "admin"


def test_admin_service_stamps_impersonated_by_on_update():
    service = _make_admin_service()
    memory = _FakeMemory()
    payload = AdminMemoryCreateRequest(
        scope="user",
        scope_id="test-user",
        messages=[AdminMessage(role="user", content="original")],
    )
    created = service.create_memory(memory, payload)
    memory_id = created["memory_id"]

    update_payload = AdminMemoryUpdateRequest(
        messages=[AdminMessage(role="user", content="updated")],
        metadata={"source": "admin-edit"},
    )
    detail = service.update_memory(memory, memory_id, update_payload)
    assert detail["audit"].impersonated_by == "admin"
    assert memory.updates, "FakeMemory.update should have been called"
    stored_metadata = memory.updates[0]["metadata"]
    assert stored_metadata["impersonated_by"] == "admin"


def test_admin_service_stamps_copied_from_with_provenance_object():
    service = _make_admin_service()
    memory = _FakeMemory()
    create_payload = AdminMemoryCreateRequest(
        scope="user",
        scope_id="source-user",
        messages=[AdminMessage(role="user", content="remember alpha")],
    )
    created = service.create_memory(memory, create_payload)
    source_id = created["memory_id"]

    copy_request = AdminMemoryCopyRequest(
        target_scope="user", target_scope_id="target-user"
    )
    copied = service.copy_memory(memory, source_id, copy_request)
    assert copied["impersonated_by"] == "admin"
    assert copied["source_memory_id"] == source_id
    assert copied["target_scope"] == "user"
    assert copied["target_scope_id"] == "target-user"
    copied_from = copied["copied_from"]
    assert copied_from.source_memory_id == source_id
    assert copied_from.source_scope == "user"
    assert copied_from.source_scope_id == "source-user"

    # The new memory should be the latest .add() call, and its metadata
    # must carry both impersonated_by=admin and copied_from provenance.
    new_add = memory.adds[-1]
    assert new_add["metadata"]["impersonated_by"] == "admin"
    persisted_copied_from = new_add["metadata"]["copied_from"]
    assert persisted_copied_from["source_memory_id"] == source_id
    assert persisted_copied_from["source_scope"] == "user"
    assert persisted_copied_from["source_scope_id"] == "source-user"


def test_admin_service_copy_unknown_memory_raises():
    service = _make_admin_service()
    memory = _FakeMemory()
    copy_request = AdminMemoryCopyRequest(
        target_scope="user", target_scope_id="target-user"
    )
    with pytest.raises(ValueError):
        service.copy_memory(memory, "does-not-exist", copy_request)


def test_admin_service_record_visit_persists_and_survives_fresh_service():
    """Visits recorded through AdminService must survive a fresh service at the same path."""
    db_path = _make_temp_db_path()
    try:
        memory = _FakeMemory()
        payload = AdminMemoryCreateRequest(
            scope="user",
            scope_id="test-user",
            messages=[AdminMessage(role="user", content="alpha")],
        )
        created = _make_admin_service(path=db_path).create_memory(memory, payload)
        memory_id = created["memory_id"]

        # First service instance records visits.
        first = _make_admin_service(path=db_path)
        first.record_visit(memory, memory_id, reason="detail_open")
        first.record_visit(memory, memory_id, reason="edit_save")

        # A brand-new service instance with a brand-new visit store must still
        # see the same persisted counters.
        second = _make_admin_service(path=db_path)
        aggregate = second.visit_store.get_aggregates(memory_id)
        assert aggregate.total_visits == 2
        assert aggregate.last_visited_at is not None

        response = second.record_visit(memory, memory_id, reason="copy_source")
        assert response["memory_id"] == memory_id
        assert response["total_visits"] == 3
        assert response["reason"] == "copy_source"
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_admin_service_record_visit_unknown_memory_raises():
    """Recording a visit against an unknown memory must raise ValueError."""
    service = _make_admin_service()
    memory = _FakeMemory()
    with pytest.raises(ValueError):
        service.record_visit(memory, "nope", reason="detail_open")


def test_admin_service_repeated_visits_increment_aggregate_predictably():
    """Repeated record_visit calls must monotonically increase total_visits and
    refresh last_visited_at on every call. The QA scenario for Task6
    ("Call the visit-recording endpoint twice for the same memory, then
    fetch admin detail") is exercised here at the service layer to prove
    the aggregation rule before any HTTP plumbing enters the picture.
    """
    service = _make_admin_service()
    memory = _FakeMemory()
    created = service.create_memory(
    memory,
    AdminMemoryCreateRequest(
    scope="user",
    scope_id="test-user",
    messages=[AdminMessage(role="user", content="track visits")],
    ),
    )
    memory_id = created["memory_id"]

    first = service.record_visit(memory, memory_id, reason="detail_open")
    second = service.record_visit(memory, memory_id, reason="edit_save")
    third = service.record_visit(memory, memory_id, reason="copy_source")

    # Each response must reflect the post-call counter, not a stale snapshot.
    assert first["total_visits"] ==1
    assert second["total_visits"] ==2
    assert third["total_visits"] ==3
    # last_visited_at must be non-null after the first visit and present on
    # every subsequent response — the backend never fabricates a timestamp.
    assert first["last_visited_at"] is not None
    assert second["last_visited_at"] is not None
    assert third["last_visited_at"] is not None


def test_admin_service_detail_marks_freshly_created_memory_as_never_visited():
    """A memory with no recorded visits must surface ``never_visited=True``
    and ``last_visited_at=None`` on the detail payload without any client
    inference. The QA scenario for Task6 ("Create a new memory and fetch
    admin detail before any visit-recording action") is locked at the
    service layer here.
    """
    service = _make_admin_service()
    memory = _FakeMemory()
    created = service.create_memory(
    memory,
    AdminMemoryCreateRequest(
    scope="user",
    scope_id="test-user",
    messages=[AdminMessage(role="user", content="untouched")],
    ),
    )
    memory_id = created["memory_id"]

    detail = service.get_memory(memory, memory_id)

    freshness = detail["freshness"]
    popularity = detail["popularity"]
    assert freshness.never_visited is True
    assert freshness.last_visited_at is None
    assert popularity.total_visits ==0
    assert popularity.visit_ratio ==0.0
    # The freshness payload must surface every raw decay input the CMS needs
    # to compute display values; ``None`` is fine when metadata omits them.
    assert hasattr(freshness, "created_at")
    assert hasattr(freshness, "decay_half_life_days")
    assert hasattr(freshness, "ttl_expires_at")


def test_admin_service_record_visit_flips_detail_to_visited():
    """After a single record_visit call, the next detail fetch must report
    ``never_visited=False`` and the recorded timestamp. This is the
    end-to-end shape the CMS relies on for the
    detail-open → record-visit → re-render cycle.
    """
    service = _make_admin_service()
    memory = _FakeMemory()
    created = service.create_memory(
    memory,
    AdminMemoryCreateRequest(
    scope="user",
    scope_id="test-user",
    messages=[AdminMessage(role="user", content="visited once")],
    ),
    )
    memory_id = created["memory_id"]

    service.record_visit(memory, memory_id, reason="detail_open")

    detail = service.get_memory(memory, memory_id)
    freshness = detail["freshness"]
    popularity = detail["popularity"]
    assert freshness.never_visited is False
    assert freshness.last_visited_at is not None
    assert popularity.total_visits ==1
    # A single visit on a single memory must saturate the ratio at1.0.
    assert popularity.visit_ratio ==1.0


def test_admin_service_list_exposes_separate_popularity_and_freshness_blocks():
    """List items must carry popularity and freshness as independent
    Pydantic blocks — never collapsed into a single combined score. This
    locks the separation-of-concerns rule from the Task6 plan at the
    service layer.
    """
    service = _make_admin_service()
    memory = _FakeMemory()
    service.create_memory(
    memory,
    AdminMemoryCreateRequest(
    scope="user",
    scope_id="test-user",
    messages=[AdminMessage(role="user", content="alpha")],
    ),
    )
    page = service.list_memories(
    memory, scope="user", scope_id="test-user", page=1, page_size=20
    )
    item = page["items"][0]

    # Both blocks must be present and typed independently.
    assert isinstance(item["popularity"], AdminPopularityInfo)
    assert isinstance(item["freshness"], AdminFreshnessInfo)
    # The blocks must not leak into each other: popularity has visit counts,
    # freshness has decay inputs — neither field crosses the boundary.
    popularity_fields = set(AdminPopularityInfo.model_fields.keys())
    freshness_fields = set(AdminFreshnessInfo.model_fields.keys())
    assert popularity_fields.isdisjoint(freshness_fields)
    # No combined "score" field exists at the API surface — the backend
    # exposes raw fields only.
    assert "score" not in popularity_fields
    assert "score" not in freshness_fields
    assert "recency_score" not in popularity_fields
    assert "recency_score" not in freshness_fields


# ---------------------------------------------------------------------------
# AdminService: thin placeholders for the full CRUD surface
# ---------------------------------------------------------------------------


def test_admin_service_exposes_thin_placeholder_methods():
    """All required method seams must exist with the expected signatures.

    The plan calls for thin placeholders over existing services for
    list/detail/create/update/delete/copy/visit.  We assert each
    method exists and is callable on a constructed service.
    """
    service = _make_admin_service()
    memory = _FakeMemory()
    for method_name in (
        "list_memories",
        "get_memory",
        "create_memory",
        "update_memory",
        "delete_memory",
        "copy_memory",
        "record_visit",
    ):
        assert hasattr(service, method_name), f"missing method: {method_name}"
        assert callable(getattr(service, method_name)), (
            f"{method_name} must be callable"
        )


def test_admin_service_list_memories_returns_paginated_envelope():
    service = _make_admin_service()
    memory = _FakeMemory()
    service.create_memory(
        memory,
        AdminMemoryCreateRequest(
            scope="user",
            scope_id="test-user",
            messages=[AdminMessage(role="user", content="alpha")],
        ),
    )
    page = service.list_memories(
        memory, scope="user", scope_id="test-user", page=1, page_size=20
    )
    assert page["page"] == 1
    assert page["page_size"] == 20
    assert page["total_items"] >= 1
    assert page["total_pages"] >= 1
    item = page["items"][0]
    assert item["scope"] == "user"
    assert item["scope_id"] == "test-user"
    assert "popularity" in item
    assert "freshness" in item


def test_admin_service_list_memories_rejects_invalid_pagination():
    service = _make_admin_service()
    memory = _FakeMemory()
    with pytest.raises(ValueError):
        service.list_memories(
            memory, scope="user", scope_id="test-user", page=0, page_size=20
        )
    with pytest.raises(ValueError):
        service.list_memories(
            memory, scope="user", scope_id="test-user", page=1, page_size=0
        )


def test_admin_service_list_memories_filters_by_query_substring():
    """When a query is given, list_memories must filter by case-insensitive content substring."""
    service = _make_admin_service()
    memory = _FakeMemory()
    for content in ("remember alpha", "remember beta", "unrelated note"):
        service.create_memory(
            memory,
            AdminMemoryCreateRequest(
                scope="user",
                scope_id="test-user",
                messages=[AdminMessage(role="user", content=content)],
            ),
        )

    page = service.list_memories(
        memory,
        scope="user",
        scope_id="test-user",
        page=1,
        page_size=20,
        query="REMEMBER",
    )

    assert page["total_items"] == 2
    assert {item["content"] for item in page["items"]} == {
        "remember alpha",
        "remember beta",
    }


def test_admin_service_list_memories_empty_query_means_no_filter():
    """An empty/whitespace-only query must not filter the listing."""
    service = _make_admin_service()
    memory = _FakeMemory()
    service.create_memory(
        memory,
        AdminMemoryCreateRequest(
            scope="user",
            scope_id="test-user",
            messages=[AdminMessage(role="user", content="alpha")],
        ),
    )
    for query_value in ("", "   ", None):
        page = service.list_memories(
            memory,
            scope="user",
            scope_id="test-user",
            page=1,
            page_size=20,
            query=query_value,
        )
        assert page["total_items"] == 1


def test_admin_service_list_memories_filters_before_pagination():
    """The query filter must apply before paging so total_items reflects the filtered set."""
    service = _make_admin_service()
    memory = _FakeMemory()
    for index in range(5):
        service.create_memory(
            memory,
            AdminMemoryCreateRequest(
                scope="user",
                scope_id="test-user",
                messages=[AdminMessage(role="user", content=f"match-{index}")],
            ),
        )
    for index in range(5):
        service.create_memory(
            memory,
            AdminMemoryCreateRequest(
                scope="user",
                scope_id="test-user",
                messages=[AdminMessage(role="user", content=f"other-{index}")],
            ),
        )

    page = service.list_memories(
        memory,
        scope="user",
        scope_id="test-user",
        page=1,
        page_size=3,
        query="match",
    )

    assert page["total_items"] == 5
    assert page["total_pages"] == 2
    assert len(page["items"]) == 3


def test_admin_service_copy_does_not_mutate_source():
    """copy_memory must leave the source record's content and metadata untouched."""
    service = _make_admin_service()
    memory = _FakeMemory()
    created = service.create_memory(
        memory,
        AdminMemoryCreateRequest(
            scope="user",
            scope_id="source-user",
            messages=[AdminMessage(role="user", content="immutable source")],
        ),
    )
    source_id = created["memory_id"]
    source_snapshot = {
        "memory": memory.records[source_id]["memory"],
        "messages": [dict(m) for m in memory.records[source_id]["messages"]],
        "metadata": dict(memory.records[source_id]["metadata"]),
    }

    service.copy_memory(
        memory,
        source_id,
        AdminMemoryCopyRequest(target_scope="user", target_scope_id="target-user"),
    )

    current = memory.records[source_id]
    assert current["memory"] == source_snapshot["memory"]
    assert [dict(m) for m in current["messages"]] == source_snapshot["messages"]
    assert current["metadata"] == source_snapshot["metadata"]


def test_admin_service_list_item_returns_pydantic_aggregates():
    """popularity/freshness on list items must be Pydantic instances, not raw dicts."""
    service = _make_admin_service()
    memory = _FakeMemory()
    service.create_memory(
        memory,
        AdminMemoryCreateRequest(
            scope="user",
            scope_id="test-user",
            messages=[AdminMessage(role="user", content="typed check")],
        ),
    )
    page = service.list_memories(
        memory, scope="user", scope_id="test-user", page=1, page_size=20
    )
    item = page["items"][0]
    assert isinstance(item["popularity"], AdminPopularityInfo)
    assert isinstance(item["freshness"], AdminFreshnessInfo)


def test_admin_service_detail_item_returns_pydantic_aggregates():
    """popularity/freshness/audit on detail items must be Pydantic instances."""
    service = _make_admin_service()
    memory = _FakeMemory()
    created = service.create_memory(
        memory,
        AdminMemoryCreateRequest(
            scope="user",
            scope_id="test-user",
            messages=[AdminMessage(role="user", content="typed detail check")],
        ),
    )
    detail = service.get_memory(memory, created["memory_id"])
    assert isinstance(detail["popularity"], AdminPopularityInfo)
    assert isinstance(detail["freshness"], AdminFreshnessInfo)
    assert isinstance(detail["audit"], AdminAuditInfo)


def test_admin_service_get_memory_unknown_raises():
    service = _make_admin_service()
    memory = _FakeMemory()
    with pytest.raises(ValueError):
        service.get_memory(memory, "missing")


def test_admin_service_get_memory_returns_audit_block():
    service = _make_admin_service()
    memory = _FakeMemory()
    created = service.create_memory(
        memory,
        AdminMemoryCreateRequest(
            scope="user",
            scope_id="test-user",
            messages=[AdminMessage(role="user", content="alpha")],
        ),
    )
    detail = service.get_memory(memory, created["memory_id"])
    assert "audit" in detail
    assert detail["audit"].impersonated_by == "admin"


def test_admin_service_delete_memory_removes_record():
    service = _make_admin_service()
    memory = _FakeMemory()
    created = service.create_memory(
        memory,
        AdminMemoryCreateRequest(
            scope="user",
            scope_id="test-user",
            messages=[AdminMessage(role="user", content="alpha")],
        ),
    )
    memory_id = created["memory_id"]
    response = service.delete_memory(memory, memory_id)
    assert response["memory_id"] == memory_id
    assert response["deleted"] is True
    assert memory_id in memory.deletes


def test_admin_service_delete_unknown_memory_raises():
    service = _make_admin_service()
    memory = _FakeMemory()
    with pytest.raises(ValueError):
        service.delete_memory(memory, "missing")


def test_admin_service_delete_empty_memories_removes_only_empty_records():
    service = _make_admin_service()
    class _WrappedGetAllMemory(_FakeMemory):
        def get_all(self, *, user_id=None, agent_id=None, run_id=None) -> Any:
            assert user_id is None
            assert agent_id is None
            assert run_id is None
            return {"results": list(self.records.values())}

    memory = _WrappedGetAllMemory()
    memory.records = {
        "mem-1": {"id": "mem-1", "memory": "", "messages": []},
        "mem-2": {"id": "mem-2", "content": "   ", "messages": []},
        "mem-3": {
            "id": "mem-3",
            "messages": [
                {"role": "user", "content": ""},
                {"role": "assistant", "content": "   "},
            ],
        },
        "mem-4": {"id": "mem-4", "memory": "keep me", "messages": []},
    }

    response = service.delete_empty_memories(memory)

    assert response == {
        "deleted_count": 3,
        "message": "Deleted 3 empty memories",
    }
    assert memory.deletes == ["mem-1", "mem-2", "mem-3"]
    assert set(memory.records) == {"mem-4"}


def test_admin_service_delete_empty_memories_falls_back_to_postgres():
    service = _make_admin_service()

    class _GetAllEmptyMemory(_FakeMemory):
        def get_all(self, *, user_id=None, agent_id=None, run_id=None):
            return []

    memory = _GetAllEmptyMemory()
    rows = [
        ("mem-1", {"memory": ""}),
        ("mem-2", {"messages": [{"role": "user", "content": "still here"}]}),
        ("mem-3", {"content": "   "}),
        ("ignore-me",),
    ]
    memory.records = {
        "mem-1": {"id": "mem-1", "memory": ""},
        "mem-2": {
            "id": "mem-2",
            "messages": [{"role": "user", "content": "still here"}],
        },
        "mem-3": {"id": "mem-3", "content": "   "},
    }

    service._postgres_fallback_query = MagicMock(return_value=rows)

    response = service.delete_empty_memories(memory)

    service._postgres_fallback_query.assert_called_once_with(
        "SELECT id, payload FROM {table}"
    )
    assert response == {
        "deleted_count": 2,
        "message": "Deleted 2 empty memories",
    }
    assert memory.deletes == ["mem-1", "mem-3"]
    assert set(memory.records) == {"mem-2"}


def test_admin_service_delete_empty_memories_uses_postgres_row_uuid_when_payload_lacks_id():
    service = _make_admin_service()

    class _GetAllEmptyMemory(_FakeMemory):
        def get_all(self, *, user_id=None, agent_id=None, run_id=None):
            return []

    memory = _GetAllEmptyMemory()
    memory.records = {
        "pg-row-1": {"id": "pg-row-1", "memory": ""},
        "pg-row-2": {"id": "pg-row-2", "memory": "keep me"},
    }
    service._postgres_fallback_query = MagicMock(
        return_value=[
            ("pg-row-1", {"memory": "", "user_id": "user-1"}),
            ("pg-row-2", {"memory": "keep me", "user_id": "user-1"}),
        ]
    )

    response = service.delete_empty_memories(memory)

    assert response == {
        "deleted_count": 1,
        "message": "Deleted 1 empty memories",
    }
    assert memory.deletes == ["pg-row-1"]
    assert set(memory.records) == {"pg-row-2"}


def test_admin_service_delete_empty_memories_continues_after_delete_failure():
    service = _make_admin_service()

    class _PartiallyFailingMemory(_FakeMemory):
        def get_all(self, *, user_id=None, agent_id=None, run_id=None):
            return list(self.records.values())

        def delete(self, memory_id):
            if memory_id == "mem-2":
                raise RuntimeError("boom")
            super().delete(memory_id)

    memory = _PartiallyFailingMemory()
    memory.records = {
        "mem-1": {"id": "mem-1", "memory": ""},
        "mem-2": {"id": "mem-2", "content": ""},
        "mem-3": {"id": "mem-3", "messages": [{"role": "user", "content": ""}]},
    }

    response = service.delete_empty_memories(memory)

    assert response == {
        "deleted_count": 2,
        "message": "Deleted 2 empty memories",
    }
    assert memory.deletes == ["mem-1", "mem-3"]
    assert set(memory.records) == {"mem-2"}


def test_admin_service_list_memories_uses_postgres_row_uuid_when_payload_lacks_id():
    service = _make_admin_service()

    class _GetAllEmptyMemory(_FakeMemory):
        def get_all(self, *, user_id=None, agent_id=None, run_id=None):
            return []

    memory = _GetAllEmptyMemory()
    service._postgres_fallback_query = MagicMock(
        return_value=[
            (
                "pg-row-1",
                {
                    "memory": "visible fallback memory",
                    "user_id": "user-1",
                    "anchor": {"created_at": "2026-06-01T12:00:00Z"},
                },
            ),
        ]
    )

    page = service.list_memories(memory, page=1, page_size=20)

    assert page["total_items"] == 1
    assert page["total_pages"] == 1
    item = page["items"][0]
    assert item["memory_id"] == "pg-row-1"
    assert item["scope"] == "user"
    assert item["scope_id"] == "user-1"
    assert item["content"] == "visible fallback memory"
    assert item["freshness"].created_at == "2026-06-01T12:00:00Z"


def test_admin_service_extract_content_fallback_for_top_level_data():
    """Regression: ``_extract_content`` must surface ``payload.data`` when the
    fallback row carries the memory text in a top-level ``"data"`` field
    (i.e. none of the canonical ``memory`` / ``content`` / ``messages``
    fields are populated).

    The test builds a fallback row with the exact failing shape observed in
    production (``data``, ``type``, ``anchor``, ``created_at``, ``user_id``)
    and asserts the list item's ``content`` is the non-empty string held in
    ``"data"``.  See the notepad ``.omo/notepads/cms-memory-cards-empty/
    issues.md`` 2026-06-16 entry for the live QA evidence and the matching
    CMS symptom (empty memory cards).
    """
    service = _make_admin_service()

    class _GetAllEmptyMemory(_FakeMemory):
        def get_all(self, *, user_id=None, agent_id=None, run_id=None):
            return []

    memory = _GetAllEmptyMemory()
    service._postgres_fallback_query = MagicMock(
        return_value=[
            (
                "pg-row-data-1",
                {
                    "data": "remember this from the payload data field",
                    "type": "note",
                    "anchor": {"created_at": "2026-06-01T12:00:00Z"},
                    "created_at": "2026-06-01T12:00:00Z",
                    "user_id": "user-1",
                },
            ),
        ]
    )

    page = service.list_memories(memory, page=1, page_size=20)

    assert page["total_items"] == 1
    assert page["total_pages"] == 1
    item = page["items"][0]
    assert item["memory_id"] == "pg-row-data-1"
    # The regression: the content must come from the top-level "data"
    # field on the fallback row, not be empty because none of the
    # canonical "memory" / "content" / "messages" fields are populated.
    assert item["content"] == "remember this from the payload data field"
    assert item["scope"] == "user"
    assert item["scope_id"] == "user-1"
    assert item["freshness"].created_at == "2026-06-01T12:00:00Z"


def test_admin_service_fallback_row_synthesizes_memory_id_and_metadata():
    """Regression: a fallback row whose top-level payload carries
    ``type``, ``anchor``, ``created_at``, and ``decay_half_life_days``
    (and **no** nested ``metadata`` key) must surface a non-empty
    ``memory_id`` and a synthesized ``metadata`` dict so the list UI
    can render type/anchor/half-life for these rows.

    Background
    ----------
    The PostgreSQL fallback rows produced by ``SELECT id, payload FROM
    {table}`` carry the actual memory attributes as **top-level**
    payload fields (``type``, ``anchor``, ``created_at``,
    ``decay_half_life_days``, ``user_id``) and lack a nested
    ``metadata`` dict.  Live evidence (see
    ``.omo/notepads/cms-memory-cards-empty/issues.md`` 2026-06-16):
    ``has_data=2044, has_memory=0, has_metadata=0, has_id=0`` — every
    fallback row in production has zero of the canonical
    ``memory``/``content``/``messages`` fields and zero rows have a
    ``metadata`` key.

    Contract (Task 2 follow-up)
    ----------------------------
    The admin list must return, for each fallback row:

    * ``memory_id`` — the SQL row UUID (already locked by
      :func:`test_admin_service_list_memories_uses_postgres_row_uuid_when_payload_lacks_id`).
      Re-locked here so a refactor cannot silently drop the
      row-id-as-``memory_id`` step.
    * ``metadata`` — a dict (never ``None``) containing at least
      ``type``, ``anchor``, ``created_at``, and ``decay_half_life_days``
      synthesised from the top-level payload fields.  The current list
      UI reads ``metadata.type`` and ``metadata.anchor`` to render the
      type pill and anchor context, and ``metadata.decay_half_life_days``
      to apply the plugin-authority decay formula.
    * ``freshness.created_at`` — the row's ``created_at`` so the CMS
      can compute the recency display value.

    Contrast with :func:`test_admin_service_extract_content_fallback_for_top_level_data`
    ------------------------------------------------------------------------------------
    The earlier regression locks the **content** path: ``_extract_content``
    must read ``payload.data``.  This test locks the **identity and
    metadata** path: the fallback loader must surface
    ``type``/``anchor``/``decay_half_life_days`` from the top-level
    payload, and the SQL row UUID must become ``memory_id``.  The two
    regressions fail independently — fixing one does not satisfy the
    other — so each is locked at the service layer to prevent silent
    regressions during the production normalisation work.

    Failure mode (red state)
    ------------------------
    Today, ``_load_postgres_fallback_records`` only synthesises a
    ``metadata`` dict when the payload's ``anchor.created_at`` is set,
    and even then it stores **only** ``created_at`` (not ``type``,
    ``anchor``, or ``decay_half_life_days``).  The test therefore fails
    on the first ``metadata[key]`` assertion that the current code
    cannot satisfy.
    """
    service = _make_admin_service()

    class _GetAllEmptyMemory(_FakeMemory):
        def get_all(self, *, user_id=None, agent_id=None, run_id=None):
            return []

    memory = _GetAllEmptyMemory()
    fallback_anchor = {"created_at": "2026-06-01T12:00:00Z"}
    fallback_type = "note"
    fallback_decay_half_life_days = 30
    fallback_created_at = "2026-06-01T12:00:00Z"
    service._postgres_fallback_query = MagicMock(
        return_value=[
            (
                "pg-row-meta-1",
                {
                    "data": "remember this from the payload data field",
                    "type": fallback_type,
                    "anchor": fallback_anchor,
                    "created_at": fallback_created_at,
                    "decay_half_life_days": fallback_decay_half_life_days,
                    "user_id": "user-1",
                },
            ),
        ]
    )

    page = service.list_memories(memory, page=1, page_size=20)

    assert page["total_items"] == 1
    assert page["total_pages"] == 1
    item = page["items"][0]

    # The SQL row UUID must surface as memory_id when the payload
    # carries no ``id`` field.  This is the identity contract the CMS
    # relies on for per-row actions (visit, copy, edit, delete).
    assert item["memory_id"] == "pg-row-meta-1"
    assert item["memory_id"], "memory_id must be non-empty for fallback rows"

    # The list UI must never receive ``metadata=None`` for a fallback
    # row — the type pill and anchor context both read from this dict.
    # A None here is exactly the failure mode that renders empty
    # memory cards in the CMS.
    assert isinstance(item["metadata"], dict), (
        "fallback rows must surface a synthesized metadata dict, "
        "not None"
    )

    # The synthesized metadata must contain the type the CMS renders
    # in the type pill.  Top-level ``type`` on the payload is the only
    # source for fallback rows.
    assert item["metadata"].get("type") == fallback_type, (
        "metadata.type must be synthesized from the top-level payload "
        "type so the CMS type pill can render for fallback rows"
    )

    # The synthesized metadata must contain the anchor object the CMS
    # uses for anchor context (timestamp, source reference, etc.).
    assert item["metadata"].get("anchor") == fallback_anchor, (
        "metadata.anchor must be synthesized from the top-level "
        "payload anchor object"
    )

    # The freshness block must reflect the row's created_at so the CMS
    # can compute the recency display value.  This is the only field
    # the current production code happens to surface correctly (it
    # reads it from the anchor-synthesized metadata); we lock it
    # explicitly so a future refactor cannot drop the wiring.
    assert item["freshness"].created_at == fallback_created_at, (
        "freshness.created_at must be the payload's top-level "
        "created_at so the CMS can render the recency display"
    )

    # The synthesized metadata must surface decay_half_life_days so
    # the CMS can apply the plugin-authority decay formula without
    # having to fall back to deriveHalfLifeDays(type) for every
    # fallback row.
    assert item["metadata"].get("decay_half_life_days") == (
        fallback_decay_half_life_days
    ), (
        "metadata.decay_half_life_days must be synthesized from the "
        "top-level payload decay_half_life_days"
    )


def test_admin_service_health_payload_includes_visit_db_path():
    db_path = _make_temp_db_path()
    try:
        service = _make_admin_service(path=db_path)
        payload = service.health()
        assert payload["status"] == "ok"
        assert payload["service"] == "admin-cms"
        assert payload["visit_db_path"] == db_path
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
