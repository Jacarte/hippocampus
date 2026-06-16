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

The service methods that Task 5/6/9 will fully implement (list/detail/create/
update/copy/visit/index) are verified only as *thin placeholders*: they must
exist with the right signatures and not silently swallow validation errors.
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

    def update(self, memory_id, data):
        record = self.records.get(memory_id)
        if record is None:
            return None
        record.update(data)
        if "metadata" in data:
            record["metadata"] = dict(data["metadata"])
        if "messages" in data:
            record["messages"] = list(data["messages"])
            record["memory"] = data["messages"][0].get("content", "") if data["messages"] else ""
        self.updates.append({"memory_id": memory_id, "data": data})
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
    stored_metadata = memory.updates[0]["data"]["metadata"]
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
# AdminService: thin placeholders for the full CRUD/index surface
# ---------------------------------------------------------------------------


def test_admin_service_exposes_thin_placeholder_methods():
    """All required method seams must exist with the expected signatures.

    The plan calls for thin placeholders over existing services for
    list/detail/create/update/delete/copy/visit/index.  We assert each
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
        "index_overview",
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


def test_admin_service_index_overview_returns_contract_shape():
    service = _make_admin_service()
    indexing_service = MagicMock()
    indexing_service.status.return_value = {"roots": [], "total_chunks": 0}
    indexing_service._manifest.get_status.return_value = {"roots": {}, "total_files": 0}
    job_service = MagicMock()
    job_service.list_jobs.return_value = []

    response = service.index_overview(indexing_service, job_service)
    assert hasattr(response, "roots")
    assert hasattr(response, "jobs")
    assert hasattr(response, "files")
    assert hasattr(response, "limits")
    assert hasattr(response, "visibility_inputs")
    assert response.limits.current_process_state_only is True


def test_admin_service_index_overview_empty_state_is_truthful():
    """With no manifest, no corpus, no watcher, and no jobs, the
    overview must surface an empty-but-valid response with
    ``current_process_state_only=True`` rather than fabricating
    data.  Locks the plan MUST-NOT "Do not imply durable persisted
    history" at the service layer so a future refactor of
    :meth:`AdminService.index_overview` cannot drift back to
    optimistic defaults.
    """
    service = _make_admin_service()
    indexing_service = MagicMock()
    indexing_service.status.return_value = {"roots": [], "total_chunks": 0}
    indexing_service._manifest.get_status.return_value = {"roots": {}, "total_files": 0}
    indexing_service.iter_manifest_files.return_value = []
    indexing_service._corpus.get_status.return_value = {"total_chunks": 0, "total_files": 0}
    job_service = MagicMock()
    job_service.list_jobs.return_value = []
    watch_service = MagicMock()
    watch_service.is_watching.return_value = False

    response = service.index_overview(
        indexing_service=indexing_service,
        job_service=job_service,
        watch_service=watch_service,
    )

    assert response.roots == []
    assert response.jobs == []
    assert response.files == []
    assert response.limits.current_process_state_only is True
    assert response.visibility_inputs.root_count == 0
    assert response.visibility_inputs.file_count == 0
    assert response.visibility_inputs.chunk_count == 0
    assert response.visibility_inputs.generated_at.endswith("Z")


def test_admin_service_index_overview_aggregates_real_services():
    """With a real :class:`IndexManifestService` and
    :class:`FileCorpusService` populated as if a sync had completed,
    the overview must surface truthful roots/files/jobs state —
    ``language`` from :meth:`FileScanner.language_for`,
    ``has_summary_embedding`` from the live corpus, and
    ``watcher_active`` from the live :class:`WatchService`.  Locks
    the contract at the service layer so the HTTP-level tests
    (test_admin_routes.py) only need to verify the wire shape.
    """
    from services.file_corpus_service import FileCorpusService
    from services.file_scanner import FileScanner
    from services.index_manifest_service import IndexManifestService
    from services.indexing_service import IndexingService
    from services.background_job_service import BackgroundJobService
    from services.watch_service import WatchService

    corpus = FileCorpusService()
    manifest = IndexManifestService()
    scanner = FileScanner()
    indexing_service = IndexingService(
        corpus=corpus, manifest=manifest, scanner=scanner
    )
    job_service = BackgroundJobService(max_workers=1)

    sync_root = "/srv/repo"
    py_path = "src/main.py"
    md_path = "README.md"
    indexing_service._manifest.update_file(
        root=sync_root, file_path=py_path, fingerprint="fp-py", chunk_ids=["c1", "c2"]
    )
    indexing_service._manifest.update_file(
        root=sync_root, file_path=md_path, fingerprint="fp-md", chunk_ids=["c3"]
    )
    indexing_service._corpus.upsert_chunks(
        sync_root,
        py_path,
        [
            {"id": "c1", "content": "def hello(): pass", "summary_embedding": [0.1]},
            {"id": "c2", "content": "def world(): pass"},
        ],
    )
    indexing_service._corpus.upsert_chunks(
        sync_root, md_path, [{"id": "c3", "content": "# README"}]
    )

    job_id = job_service.submit(
        lambda: {
            "root": sync_root,
            "files_indexed": 0,
            "chunks_indexed": 0,
            "synced_at": "1970-01-01T00:00:00Z",
            "errors": [],
        }
    )

    watch_service = MagicMock()
    watch_service.is_watching.side_effect = lambda r: r == sync_root

    service = _make_admin_service()
    response = service.index_overview(
        indexing_service=indexing_service,
        job_service=job_service,
        watch_service=watch_service,
    )

    assert len(response.roots) == 1
    root_row = response.roots[0]
    assert root_row.root == sync_root
    assert root_row.total_files == 2
    assert root_row.total_chunks == 3
    assert root_row.watcher_active is True
    assert root_row.last_job_id == job_id

    files_by_path = {f.file_path: f for f in response.files}
    assert files_by_path[py_path].language == "python"
    assert files_by_path[py_path].has_summary_embedding is True
    assert files_by_path[md_path].language == "markdown"
    assert files_by_path[md_path].has_summary_embedding is False

    job_ids = [j.job_id for j in response.jobs]
    assert job_id in job_ids
    assert response.visibility_inputs.root_count == 1
    assert response.visibility_inputs.file_count == 2
    assert response.visibility_inputs.chunk_count == 3


def test_admin_service_index_overview_uses_watcher_for_watcher_active():
    """``watcher_active`` must be sourced from
    :meth:`WatchService.is_watching` rather than the manifest's
    ``RootManifest.watching`` field (which is a dataclass default
    and not a reliable source).  A bare manifest with no watcher
    service must report ``watcher_active=False`` for every root.
    """
    service = _make_admin_service()
    indexing_service = MagicMock()
    indexing_service.status.return_value = {
        "roots": [
            {
                "root_path": "/srv/a",
                "indexed_at": "1970-01-01T00:00:00Z",
                "file_count": 1,
                "chunk_count": 1,
                "watching": True,
            }
        ],
        "total_chunks": 1,
    }
    indexing_service._manifest.get_status.return_value = {
        "roots": {
            "/srv/a": {
                "root_path": "/srv/a",
                "indexed_at": "1970-01-01T00:00:00Z",
                "file_count": 1,
                "chunk_count": 1,
                "watching": True,
            }
        },
        "total_files": 1,
    }
    indexing_service.iter_manifest_files.return_value = []
    indexing_service._corpus.get_status.return_value = {"total_chunks": 0, "total_files": 0}
    job_service = MagicMock()
    job_service.list_jobs.return_value = []

    watch_service = MagicMock()
    watch_service.is_watching.return_value = False
    response = service.index_overview(
        indexing_service=indexing_service,
        job_service=job_service,
        watch_service=watch_service,
    )

    assert len(response.roots) == 1
    # Manifest's watching=True is ignored; the live watch service wins.
    assert response.roots[0].watcher_active is False


def test_admin_service_index_overview_handles_no_watch_service():
    """When no watch service is provided, the overview must
    default ``watcher_active`` to ``False`` and still surface all
    the other fields truthfully.
    """
    service = _make_admin_service()
    indexing_service = MagicMock()
    indexing_service.status.return_value = {"roots": [], "total_chunks": 0}
    indexing_service._manifest.get_status.return_value = {
        "roots": {
            "/srv/a": {
                "root_path": "/srv/a",
                "indexed_at": "1970-01-01T00:00:00Z",
                "file_count": 1,
                "chunk_count": 1,
                "watching": False,
            }
        },
        "total_files": 1,
    }
    indexing_service.iter_manifest_files.return_value = []
    indexing_service._corpus.get_status.return_value = {"total_chunks": 0, "total_files": 0}
    job_service = MagicMock()
    job_service.list_jobs.return_value = []

    response = service.index_overview(
        indexing_service=indexing_service, job_service=job_service, watch_service=None
    )
    assert response.roots[0].watcher_active is False


def test_admin_service_index_overview_falls_back_to_corpus_for_totals():
    """When the manifest is empty but the corpus has chunks, the
    ``visibility_inputs.chunk_count`` must come from the corpus
    status rather than defaulting to 0.  This is the contracted
    fall-back path for processes that have called ``sync`` without
    materialising per-file manifest records yet.
    """
    service = _make_admin_service()
    indexing_service = MagicMock()
    indexing_service.status.return_value = {"roots": [], "total_chunks": 0}
    indexing_service._manifest.get_status.return_value = {"roots": {}, "total_files": 0}
    indexing_service.iter_manifest_files.return_value = []
    indexing_service._corpus.get_status.return_value = {
        "total_chunks": 7,
        "total_files": 3,
        "roots": {"/srv/edge": 7},
    }
    job_service = MagicMock()
    job_service.list_jobs.return_value = []

    response = service.index_overview(
        indexing_service=indexing_service, job_service=job_service, watch_service=None
    )
    assert response.visibility_inputs.file_count == 3
    assert response.visibility_inputs.chunk_count == 7


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
