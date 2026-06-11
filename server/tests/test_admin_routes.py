"""Route-level tests for the additive ``/admin/*`` endpoints.

These tests prove that each admin route:
* Accepts the expected request shapes
* Returns the expected status codes and response envelopes
* Delegates to :class:`AdminService` through thin handlers
* Validates inputs at the FastAPI/Pydantic boundary (pagination, scope)
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from pytest import MonkeyPatch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


_MINIMAL_CONFIG: dict[str, Any] = {
    "version": "v1.1",
    "vector_store": {"provider": "pgvector", "config": {}},
    "llm": {
        "provider": "openai",
        "config": {"model": "gpt-5", "api_key": "test-key"},
    },
    "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
    "history_db_path": "/tmp/history.db",
}


class _FakeMemory:
    """Minimal in-memory mem0 stand-in for admin route tests.

    Mirrors the subset of mem0 methods that :class:`AdminService` calls:
    ``add``, ``get``, ``get_all``, ``update``, ``delete``.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.records: dict[str, dict[str, Any]] = {}

    def add(
        self, *, messages: list[dict[str, Any]], metadata: dict[str, Any] | None = None,
        user_id: str | None = None, agent_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        record_metadata = dict(metadata or {})
        if user_id is not None:
            record_metadata.setdefault("user_id", user_id)
        if agent_id is not None:
            record_metadata.setdefault("agent_id", agent_id)
        if run_id is not None:
            record_metadata.setdefault("run_id", run_id)
        memory_id = f"mem-{len(self.records) + 1}"
        record: dict[str, Any] = {
            "id": memory_id,
            "memory": messages[0]["content"] if messages else "",
            "messages": list(messages),
            "metadata": record_metadata,
        }
        if user_id is not None:
            record["user_id"] = user_id
        if agent_id is not None:
            record["agent_id"] = agent_id
        if run_id is not None:
            record["run_id"] = run_id
        self.records[memory_id] = record
        return record

    def get(self, memory_id: str) -> dict[str, Any] | None:
        return self.records.get(memory_id)

    def get_all(
        self, *, user_id: str | None = None, agent_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for record in self.records.values():
            if user_id is not None and record.get("user_id") == user_id:
                results.append(record)
            elif agent_id is not None and record.get("agent_id") == agent_id:
                results.append(record)
            elif run_id is not None and record.get("run_id") == run_id:
                results.append(record)
        return results

    def update(
        self, memory_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        record = self.records.get(memory_id)
        if record is None:
            return None
        record.update(data)
        if "metadata" in data:
            record["metadata"] = dict(data["metadata"])
        if "messages" in data:
            record["messages"] = list(data["messages"])
            record["memory"] = (
                data["messages"][0].get("content", "") if data["messages"] else ""
            )
        return record

    def delete(self, *, memory_id: str) -> None:
        self.records.pop(memory_id, None)


# -----------------------------------------------------------------------
# /admin/health
# -----------------------------------------------------------------------


def test_admin_health_returns_expected_payload(monkeypatch: MonkeyPatch) -> None:
    """Health endpoint must return status=ok, service=admin-cms, and the visit db path."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        response = client.get("/admin/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "admin-cms"
    assert payload["visit_db_path"] == ":memory:"


# -----------------------------------------------------------------------
# Admin memory CRUD flow
# -----------------------------------------------------------------------


def test_admin_create_memory_stamps_impersonated_by(monkeypatch: MonkeyPatch) -> None:
    """POST /admin/memories must return impersonated_by=admin."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)

        response = client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "test-user",
                "messages": [{"role": "user", "content": "remember alpha"}],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["impersonated_by"] == "admin"
    assert payload["scope"] == "user"
    assert payload["scope_id"] == "test-user"
    assert payload["memory_id"] is not None
    assert len(payload["messages"]) == 1


def test_admin_list_memories_returns_paginated_results(
    monkeypatch: MonkeyPatch,
) -> None:
    """GET /admin/memories returns paginated items with popularity/freshness fields."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "test-user",
                "messages": [{"role": "user", "content": "alpha"}],
            },
        )

        response = client.get(
            "/admin/memories",
            params={
                "scope": "user",
                "scope_id": "test-user",
                "page": 1,
                "page_size": 20,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 20
    assert payload["total_items"] >= 1
    assert payload["total_pages"] >= 1
    assert len(payload["items"]) >= 1
    item = payload["items"][0]
    assert item["scope"] == "user"
    assert item["scope_id"] == "test-user"
    assert "popularity" in item
    assert "freshness" in item


def test_admin_get_memory_returns_detail_with_audit(
    monkeypatch: MonkeyPatch,
) -> None:
    """GET /admin/memories/{id} returns detail + popularity/freshness/audit."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        create_resp = client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "test-user",
                "messages": [{"role": "user", "content": "detail check"}],
            },
        )
        memory_id = create_resp.json()["memory_id"]

        response = client.get(f"/admin/memories/{memory_id}")

    assert response.status_code == 200
    detail = response.json()
    assert detail["memory_id"] == memory_id
    assert detail["scope"] == "user"
    assert detail["scope_id"] == "test-user"
    assert detail["content"] == "detail check"
    assert "popularity" in detail
    assert "freshness" in detail
    assert "audit" in detail
    assert detail["audit"]["impersonated_by"] == "admin"


def test_admin_update_memory_returns_updated_detail(
    monkeypatch: MonkeyPatch,
) -> None:
    """PUT /admin/memories/{id} returns updated fields with re-stamped audit."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        create_resp = client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "test-user",
                "messages": [{"role": "user", "content": "original"}],
            },
        )
        memory_id = create_resp.json()["memory_id"]

        response = client.put(
            f"/admin/memories/{memory_id}",
            json={
                "messages": [{"role": "user", "content": "updated content"}],
                "metadata": {"source": "admin-test"},
            },
        )

    assert response.status_code == 200
    detail = response.json()
    assert detail["content"] == "updated content"
    assert detail["metadata"]["source"] == "admin-test"
    assert detail["audit"]["impersonated_by"] == "admin"


def test_admin_delete_memory_returns_confirmation(
    monkeypatch: MonkeyPatch,
) -> None:
    """DELETE /admin/memories/{id} deletes the memory and subsequent GET returns 400."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        create_resp = client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "test-user",
                "messages": [{"role": "user", "content": "delete me"}],
            },
        )
        memory_id = create_resp.json()["memory_id"]

        delete_resp = client.delete(f"/admin/memories/{memory_id}")

        assert delete_resp.status_code == 200
        assert delete_resp.json()["deleted"] is True

        # Verify the memory is gone.
        get_resp = client.get(f"/admin/memories/{memory_id}")
        assert get_resp.status_code == 400


def test_admin_copy_memory_returns_provenance(monkeypatch: MonkeyPatch) -> None:
    """POST /admin/memories/{id}/copy returns provenance and does not delete source."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        create_resp = client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "source-user",
                "messages": [{"role": "user", "content": "copy source"}],
            },
        )
        source_id = create_resp.json()["memory_id"]

        response = client.post(
            f"/admin/memories/{source_id}/copy",
            json={"target_scope": "user", "target_scope_id": "target-user"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_memory_id"] == source_id
    assert payload["target_memory_id"] is not None
    assert payload["target_scope"] == "user"
    assert payload["target_scope_id"] == "target-user"
    assert payload["impersonated_by"] == "admin"
    assert payload["copied_from"]["source_memory_id"] == source_id
    assert payload["copied_from"]["source_scope"] == "user"
    assert payload["copied_from"]["source_scope_id"] == "source-user"


def test_admin_copy_memory_preserves_source(monkeypatch: MonkeyPatch) -> None:
    """Copy must NOT delete or mutate the source memory.

    Locks the plan MUST-NOT: ``Do NOT delete the source memory during copy``.
    Asserts by reading the source back through ``GET /admin/memories/{id}``
    and confirming its content, metadata audit stamps, and visit counters
    are intact after a successful copy.
    """
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        create_resp = client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "source-user",
                "messages": [{"role": "user", "content": "do not lose me"}],
            },
        )
        source_id = create_resp.json()["memory_id"]

        # Record a visit on the source so we can prove the counter survives.
        visit_resp = client.post(
            f"/admin/memories/{source_id}/visits",
            json={"reason": "detail_open"},
        )
        assert visit_resp.status_code == 200

        copy_resp = client.post(
            f"/admin/memories/{source_id}/copy",
            json={"target_scope": "user", "target_scope_id": "target-user"},
        )
        assert copy_resp.status_code == 200

        # Re-fetch the source after the copy.
        source_detail = client.get(f"/admin/memories/{source_id}").json()

    # Source identity and content are unchanged.
    assert source_detail["memory_id"] == source_id
    assert source_detail["content"] == "do not lose me"
    assert source_detail["audit"]["impersonated_by"] == "admin"
    assert source_detail["audit"]["copied_from"] is None

    # Source visit counter is intact.
    assert source_detail["popularity"]["total_visits"] == 1
    assert source_detail["popularity"]["visit_ratio"] == 1.0

    # Target scope now contains a new memory carrying provenance.
    target_list = client.get(
        "/admin/memories",
        params={
            "scope": "user",
            "scope_id": "target-user",
            "page": 1,
            "page_size": 20,
        },
    )
    target_items = target_list.json()["items"]
    assert len(target_items) == 1
    target = target_items[0]
    assert target["scope_id"] == "target-user"
    assert target["content"] == "do not lose me"
    target_detail = client.get(f"/admin/memories/{target['memory_id']}").json()
    assert target_detail["audit"]["impersonated_by"] == "admin"
    assert target_detail["audit"]["copied_from"] is not None
    assert target_detail["audit"]["copied_from"]["source_memory_id"] == source_id
    assert target_detail["audit"]["copied_from"]["source_scope"] == "user"
    assert target_detail["audit"]["copied_from"]["source_scope_id"] == "source-user"


def test_admin_copy_memory_rejects_unknown_source(monkeypatch: MonkeyPatch) -> None:
    """Copy with a non-existent source memory id must return 4xx without partial writes."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        response = client.post(
            "/admin/memories/does-not-exist/copy",
            json={"target_scope": "user", "target_scope_id": "target-user"},
        )

    assert response.status_code == 400


def test_admin_copy_memory_rejects_missing_target_scope_id(
    monkeypatch: MonkeyPatch,
) -> None:
    """Copy without target_scope_id must be rejected at the Pydantic layer (422)."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        create_resp = client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "source-user",
                "messages": [{"role": "user", "content": "to be copied"}],
            },
        )
        source_id = create_resp.json()["memory_id"]

        response = client.post(
            f"/admin/memories/{source_id}/copy",
            json={"target_scope": "user"},
        )

    assert response.status_code == 422


def test_admin_copy_memory_rejects_invalid_target_scope(
    monkeypatch: MonkeyPatch,
) -> None:
    """Copy with an invalid target_scope literal must be rejected at the Pydantic layer."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        create_resp = client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "source-user",
                "messages": [{"role": "user", "content": "to be copied"}],
            },
        )
        source_id = create_resp.json()["memory_id"]

        response = client.post(
            f"/admin/memories/{source_id}/copy",
            json={"target_scope": "invalid_scope", "target_scope_id": "target-user"},
        )

    assert response.status_code == 422


def test_admin_record_visit_returns_counts(monkeypatch: MonkeyPatch) -> None:
    """POST /admin/memories/{id}/visits increments counters and returns updated state."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        create_resp = client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "test-user",
                "messages": [{"role": "user", "content": "track visits"}],
            },
        )
        memory_id = create_resp.json()["memory_id"]

        response = client.post(
            f"/admin/memories/{memory_id}/visits",
            json={"reason": "detail_open"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["memory_id"] == memory_id
    assert payload["total_visits"] == 1
    assert payload["reason"] == "detail_open"
    assert payload["last_visited_at"] is not None


def test_admin_repeated_visits_increment_counter_predictably(
    monkeypatch: MonkeyPatch,
) -> None:
    """Two POSTs to /admin/memories/{id}/visits must monotonically raise
    ``total_visits`` and refresh ``last_visited_at`` on every call.
    Locks the Task 6 QA scenario "Call the visit-recording endpoint twice
    for the same memory, then fetch admin detail" at the HTTP layer so a
    future route refactor cannot break the aggregation rule.
    """
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        create_resp = client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "test-user",
                "messages": [{"role": "user", "content": "two visits"}],
            },
        )
        memory_id = create_resp.json()["memory_id"]

        first = client.post(
            f"/admin/memories/{memory_id}/visits",
            json={"reason": "detail_open"},
        ).json()
        second = client.post(
            f"/admin/memories/{memory_id}/visits",
            json={"reason": "edit_save"},
        ).json()

        # Re-fetch detail to confirm the aggregate survives the
        # visit-recording round-trip on the wire.
        detail = client.get(f"/admin/memories/{memory_id}").json()

    # Each response reflects the post-call counter, not a stale snapshot.
    assert first["total_visits"] == 1
    assert second["total_visits"] == 2
    assert first["last_visited_at"] is not None
    assert second["last_visited_at"] is not None

    # The detail payload's popularity block mirrors the visit endpoint.
    assert detail["popularity"]["total_visits"] == 2
    # A single memory is the peak counter across the visit store, so the
    # ratio saturates at1.0.
    assert detail["popularity"]["visit_ratio"] == 1.0
    assert detail["freshness"]["never_visited"] is False
    assert detail["freshness"]["last_visited_at"] is not None


def test_admin_get_memory_never_visited_fresh_payload(
    monkeypatch: MonkeyPatch,
) -> None:
    """A memory fetched before any visit-recording action must surface
    ``freshness.never_visited=True`` and ``freshness.last_visited_at=None``
    without a fabricated timestamp or ratio.  Locks the Task 6 QA
    scenario "Create a new memory and fetch admin detail before any
    visit-recording action" at the HTTP layer.
    """
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        create_resp = client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "test-user",
                "messages": [{"role": "user", "content": "untouched"}],
            },
        )
        memory_id = create_resp.json()["memory_id"]

        response = client.get(f"/admin/memories/{memory_id}")

    assert response.status_code == 200
    detail = response.json()
    # Popularity is fully zeroed: no visits have been recorded yet.
    assert detail["popularity"]["total_visits"] == 0
    assert detail["popularity"]["visit_ratio"] == 0.0
    # Freshness explicitly flags the cold state and never fabricates a
    # timestamp.  This is the canonical never-visited wire shape the
    # CMS relies on — no client-side inference required.
    assert detail["freshness"]["never_visited"] is True
    assert detail["freshness"]["last_visited_at"] is None
    # Decay-input fields are surfaced as ``None`` when metadata omits them
    # so the CMS can apply ``deriveHalfLifeDays(type)`` fallback rules.
    assert detail["freshness"]["created_at"] is None
    assert detail["freshness"]["decay_half_life_days"] is None
    assert detail["freshness"]["ttl_expires_at"] is None


def test_admin_list_item_never_visited_zeroed_blocks(
    monkeypatch: MonkeyPatch,
) -> None:
    """A listing must surface a zeroed ``popularity`` block and a
    ``freshness.never_visited=True`` block for memories that have never
    been visited.  Locks the separation guarantee at the list
    endpoint so the CMS renders the cold color/intensity correctly on
    the memory table.
    """
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "test-user",
                "messages": [{"role": "user", "content": "alpha"}],
            },
        )
        client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "test-user",
                "messages": [{"role": "user", "content": "beta"}],
            },
        )

        # Touch "alpha" so the listing contains both visited and unvisited
        # items — the never-visited test then confirms each row reports
        # its own state, not the global max.
        list_resp = client.get(
            "/admin/memories",
            params={
                "scope": "user",
                "scope_id": "test-user",
                "page": 1,
                "page_size": 20,
            },
        )
        items = list_resp.json()["items"]
        alpha_id = next(
            item["memory_id"] for item in items if item["content"] == "alpha"
        )
        client.post(
            f"/admin/memories/{alpha_id}/visits",
            json={"reason": "detail_open"},
        )

        response = client.get(
            "/admin/memories",
            params={
                "scope": "user",
                "scope_id": "test-user",
                "page": 1,
                "page_size": 20,
            },
        )

    assert response.status_code == 200
    items = response.json()["items"]
    by_content = {item["content"]: item for item in items}
    # The visited memory: never_visited=False and total_visits=1.
    assert by_content["alpha"]["popularity"]["total_visits"] == 1
    assert by_content["alpha"]["popularity"]["visit_ratio"] == 1.0
    assert by_content["alpha"]["freshness"]["never_visited"] is False
    assert by_content["alpha"]["freshness"]["last_visited_at"] is not None
    # The unvisited memory: never_visited=True and zero popularity.
    assert by_content["beta"]["popularity"]["total_visits"] == 0
    assert by_content["beta"]["popularity"]["visit_ratio"] == 0.0
    assert by_content["beta"]["freshness"]["never_visited"] is True
    assert by_content["beta"]["freshness"]["last_visited_at"] is None


def test_admin_index_overview_returns_contract_shape(
    monkeypatch: MonkeyPatch,
) -> None:
    """GET /admin/index/overview returns the full AdminIndexOverviewResponse contract."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    # Replace real services with mocks for a deterministic test.
    mock_indexing = MagicMock()
    mock_indexing.status.return_value = {"roots": [], "total_chunks": 0}
    mock_indexing._manifest.get_status.return_value = {
        "roots": {},
        "total_files": 0,
    }
    app.state.indexing_service = mock_indexing
    mock_jobs = MagicMock()
    mock_jobs.list_jobs.return_value = []
    app.state.job_service = mock_jobs

    with TestClient(app) as client:
        response = client.get("/admin/index/overview")

    assert response.status_code == 200
    payload = response.json()
    assert "roots" in payload
    assert "jobs" in payload
    assert "files" in payload
    assert "limits" in payload
    assert payload["limits"]["current_process_state_only"] is True
    assert "visibility_inputs" in payload
    vis = payload["visibility_inputs"]
    assert "generated_at" in vis
    assert "root_count" in vis
    assert "file_count" in vis
    assert "chunk_count" in vis


def test_admin_index_overview_reports_truthful_empty_state(
    monkeypatch: MonkeyPatch,
) -> None:
    """Restarted/empty state must surface zero counts and the
    ``current_process_state_only=True`` limit rather than fabricating
    durable manifest data.  Locks the plan QA scenario
    "Restart the local server without resyncing and call the same
    overview endpoint" at the HTTP layer.
    """
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        response = client.get("/admin/index/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["limits"] == {"current_process_state_only": True}
    assert payload["roots"] == []
    assert payload["jobs"] == []
    assert payload["files"] == []
    vis = payload["visibility_inputs"]
    assert vis["root_count"] == 0
    assert vis["file_count"] == 0
    assert vis["chunk_count"] == 0
    assert isinstance(vis["generated_at"], str) and vis["generated_at"].endswith("Z")


def test_admin_index_overview_aggregates_populated_sync_state(
    monkeypatch: MonkeyPatch,
) -> None:
    """With a real (in-memory) manifest, corpus, watcher, and job
    service, the overview must surface truthful roots/files/jobs
    state — including ``language`` derived from file extensions and
    ``watcher_active`` sourced from the live :class:`WatchService`.
    Locks the plan QA scenario "Sync a fixture repo, then call
    ``curl -s http://localhost:8000/admin/index/overview``" at the
    HTTP layer.
    """
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    indexing = app.state.indexing_service
    job_service = app.state.job_service

    sync_root = "/srv/repo"
    py_path = "src/main.py"
    md_path = "README.md"
    indexing._manifest.update_file(
        root=sync_root, file_path=py_path, fingerprint="fp-py", chunk_ids=["c1", "c2"]
    )
    indexing._manifest.update_file(
        root=sync_root, file_path=md_path, fingerprint="fp-md", chunk_ids=["c3"]
    )
    indexing._corpus.upsert_chunks(
        sync_root,
        py_path,
        [
            {"id": "c1", "content": "def hello(): pass", "summary_embedding": [0.1]},
            {"id": "c2", "content": "def world(): pass"},
        ],
    )
    indexing._corpus.upsert_chunks(
        sync_root,
        md_path,
        [{"id": "c3", "content": "# README"}],
    )

    stub_watcher = MagicMock()
    stub_watcher.is_watching.side_effect = lambda r: r == sync_root
    app.state.watch_service = stub_watcher
    job_id = job_service.submit(
        lambda: {
            "root": sync_root,
            "files_indexed": 0,
            "chunks_indexed": 0,
            "synced_at": "1970-01-01T00:00:00Z",
            "errors": [],
        }
    )

    with TestClient(app) as client:
        response = client.get("/admin/index/overview")

    assert response.status_code == 200
    payload = response.json()

    assert payload["limits"] == {"current_process_state_only": True}

    assert len(payload["roots"]) == 1
    root_row = payload["roots"][0]
    assert root_row["root"] == sync_root
    assert root_row["total_files"] == 2
    assert root_row["total_chunks"] == 3
    assert root_row["watcher_active"] is True
    assert root_row["last_job_id"] == job_id

    files_by_path = {f["file_path"]: f for f in payload["files"]}
    assert set(files_by_path) == {py_path, md_path}
    py_row = files_by_path[py_path]
    assert py_row["chunk_count"] == 2
    assert py_row["language"] == "python"
    assert py_row["has_summary_embedding"] is True
    assert py_row["last_indexed_at"] is not None
    md_row = files_by_path[md_path]
    assert md_row["chunk_count"] == 1
    assert md_row["language"] == "markdown"
    assert md_row["has_summary_embedding"] is False
    assert md_row["last_indexed_at"] is not None

    job_ids = [j["job_id"] for j in payload["jobs"]]
    assert job_id in job_ids
    matching = next(j for j in payload["jobs"] if j["job_id"] == job_id)
    assert matching["status"] in ("queued", "running", "completed", "failed")
    assert matching["queued_at"] is not None

    vis = payload["visibility_inputs"]
    assert vis["root_count"] == 1
    assert vis["file_count"] == 2
    assert vis["chunk_count"] == 3
    assert isinstance(vis["generated_at"], str)


def test_admin_index_overview_uses_watch_service_for_watcher_active(
    monkeypatch: MonkeyPatch,
) -> None:
    """``watcher_active`` must reflect the live :class:`WatchService`
    state, not the manifest's ``RootManifest.watching`` field (which
    is a dataclass default and not a reliable source).  This is the
    narrow invariant that prevents the overview from showing a
    stale ``watcher_active=False`` for roots that are actively
    watched.
    """
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)
    indexing = app.state.indexing_service

    root_a = "/srv/a"
    root_b = "/srv/b"
    indexing._manifest.update_file(
        root=root_a, file_path="app.py", fingerprint="fp", chunk_ids=["c1"]
    )
    indexing._manifest.update_file(
        root=root_b, file_path="app.py", fingerprint="fp", chunk_ids=["c1"]
    )

    stub_watcher = MagicMock()
    stub_watcher.is_watching.side_effect = lambda r: r == root_a
    app.state.watch_service = stub_watcher

    with TestClient(app) as client:
        response = client.get("/admin/index/overview")

    assert response.status_code == 200
    rows = {r["root"]: r for r in response.json()["roots"]}
    assert rows[root_a]["watcher_active"] is True
    assert rows[root_b]["watcher_active"] is False


def test_admin_index_overview_tolerates_missing_watch_service(
    monkeypatch: MonkeyPatch,
) -> None:
    """When the app has no ``watch_service`` (e.g. in a stripped
    test harness), the overview must still respond 200 and report
    ``watcher_active=False`` for every root rather than 500-ing.
    """
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)
    app.state.indexing_service._manifest.update_file(
        root="/srv/nowatch", file_path="app.py", fingerprint="fp", chunk_ids=["c1"]
    )
    app.state.watch_service = MagicMock(spec=[])

    with TestClient(app) as client:
        response = client.get("/admin/index/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["limits"] == {"current_process_state_only": True}
    assert all(r["watcher_active"] is False for r in payload["roots"])


# -----------------------------------------------------------------------
# Validation errors
# -----------------------------------------------------------------------


def test_admin_list_memories_rejects_invalid_pagination(
    monkeypatch: MonkeyPatch,
) -> None:
    """Pagination params must be within bounds: page>=1, page_size 1-100."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        resp = client.get(
            "/admin/memories",
            params={"scope": "user", "scope_id": "test", "page": 0, "page_size": 20},
        )
    assert resp.status_code == 422

    with TestClient(app) as client:
        resp = client.get(
            "/admin/memories",
            params={"scope": "user", "scope_id": "test", "page": 1, "page_size": 0},
        )
    assert resp.status_code == 422

    with TestClient(app) as client:
        resp = client.get(
            "/admin/memories",
            params={
                "scope": "user",
                "scope_id": "test",
                "page": 1,
                "page_size": 101,
            },
        )
    assert resp.status_code == 422


def test_admin_list_memories_rejects_missing_scope(
    monkeypatch: MonkeyPatch,
) -> None:
    """scope and scope_id are required query params for list."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        resp = client.get(
            "/admin/memories", params={"page": 1, "page_size": 20}
        )
    assert resp.status_code == 422


def test_admin_list_memories_rejects_invalid_scope_value(
    monkeypatch: MonkeyPatch,
) -> None:
    """scope must be one of user, agent, or run."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        resp = client.get(
            "/admin/memories",
            params={
                "scope": "invalid_scope",
                "scope_id": "test",
                "page": 1,
                "page_size": 20,
            },
        )
    assert resp.status_code == 422


def test_admin_create_memory_rejects_invalid_scope(
    monkeypatch: MonkeyPatch,
) -> None:
    """POST /admin/memories rejects an invalid scope literal."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        response = client.post(
            "/admin/memories",
            json={
                "scope": "invalid_scope",
                "scope_id": "test-user",
                "messages": [{"role": "user", "content": "test"}],
            },
        )
    assert response.status_code == 422


def test_admin_create_memory_rejects_invalid_message_role(
    monkeypatch: MonkeyPatch,
) -> None:
    """Admin message roles must be 'user' or 'assistant' — not arbitrary strings."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        response = client.post(
            "/admin/memories",
            json={
                "scope": "user",
                "scope_id": "test-user",
                "messages": [{"role": "system", "content": "bad role"}],
            },
        )
    assert response.status_code == 422


def test_admin_get_unknown_memory_returns_400(monkeypatch: MonkeyPatch) -> None:
    """Accessing a nonexistent memory returns 400 (ValueError → HTTP 400)."""
    monkeypatch.setenv("MEM0_VISIT_DB_PATH", ":memory:")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    app = server.create_app(memory_factory=_FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        client.post("/configure", json=_MINIMAL_CONFIG)
        response = client.get("/admin/memories/non-existent")
    assert response.status_code == 400
