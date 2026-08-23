import importlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pytest import LogCaptureFixture
from pytest import MonkeyPatch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.retrieval_service import RetrievalService


def test_health_and_configure_smoke_without_live_dependencies(monkeypatch: MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    created_configs: list[dict[str, Any]] = []

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

    def fake_memory_factory(config: dict[str, Any]) -> FakeMemory:
        created_configs.append(config)
        return FakeMemory(config)

    app = server.create_app(memory_factory=fake_memory_factory, startup_enabled=False)

    with TestClient(app) as client:
        health_response = client.get("/health")
        assert health_response.status_code == 200
        assert health_response.json() == {"status": "healthy", "service": "mem0-api"}

        config = {
            "version": "v1.1",
            "vector_store": {
                "provider": "pgvector",
                "config": {
                    "host": "localhost",
                    "port": 5432,
                    "dbname": "postgres",
                    "user": "postgres",
                    "password": "postgres",
                    "collection_name": "mem0_memories",
                },
            },
            "llm": {
                "provider": "openai",
                "config": {"model": "gpt-5", "api_key": "test-key"},
            },
            "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
            "history_db_path": "/tmp/history.db",
        }

        configure_response = client.post("/configure", json=config)

    assert configure_response.status_code == 200
    assert configure_response.json() == {"message": "Configuration set successfully"}
    assert created_configs == [config]
    assert isinstance(app.state.memory, FakeMemory)
    assert app.state.memory.config == config
    assert app.state.memory_config == config


def test_configure_logs_redact_sensitive_runtime_config(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    config = {
        "version": "v1.1",
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "host": "localhost",
                "port": 5432,
                "password": "postgres-secret",
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": "gpt-5",
                "api_key": "test-key",
                "Authorization": "Bearer abc123",
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {"api_key": "embed-key", "token": "embed-token"},
        },
        "history_db_path": "/tmp/history.db",
    }

    with TestClient(app) as client:
        caplog.clear()
        with caplog.at_level(logging.INFO):
            configure_response = client.post("/configure", json=config)

    assert configure_response.status_code == 200
    rendered_logs = "\n".join(record.message for record in caplog.records)
    assert "Initializing mem0 with config:" in rendered_logs
    assert "test-key" not in rendered_logs
    assert "embed-key" not in rendered_logs
    assert "embed-token" not in rendered_logs
    assert "postgres-secret" not in rendered_logs
    assert "Bearer abc123" not in rendered_logs
    assert "[REDACTED]" in rendered_logs


def test_crud_search_history_routes_delegate_through_service_layer(
    monkeypatch: MonkeyPatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config: dict[str, Any] = config
            self.records: dict[str, dict[str, Any]] = {
                "memory-1": {
                    "id": "memory-1",
                    "memory": "stored memory",
                    "metadata": {"source": "chat"},
                }
            }
            self.deleted_ids: list[str] = []
            self.deleted_batches: list[dict[str, Any]] = []
            self.was_reset: bool = False

        def add(
            self, *, messages: list[dict[str, Any]], **params: Any
        ) -> dict[str, Any]:
            self.records["memory-2"] = {
                "id": "memory-2",
                "messages": messages,
                **params,
            }
            return self.records["memory-2"]

        def get_all(self, **params: Any) -> list[dict[str, Any]]:
            return [{"params": params, "items": list(self.records.values())}]

        def get(self, memory_id: str) -> dict[str, Any]:
            return self.records[memory_id]

        def search(self, *, query: str, **params: Any) -> dict[str, Any]:
            return {
                "query": query,
                "params": params,
                "results": list(self.records.values()),
            }

        def update(self, *, memory_id: str, data: dict[str, Any]) -> dict[str, Any]:
            self.records[memory_id] = {**self.records[memory_id], **data}
            return self.records[memory_id]

        def history(self, *, memory_id: str) -> list[dict[str, Any]]:
            return [{"memory_id": memory_id, "event": "created"}]

        def delete(self, *, memory_id: str) -> None:
            self.deleted_ids.append(memory_id)

        def delete_all(self, **params: Any) -> None:
            self.deleted_batches.append(params)

        def reset(self) -> None:
            self.was_reset = True

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-5", "api_key": "test-key"},
        },
        "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
        "history_db_path": "/tmp/history.db",
    }

    with TestClient(app) as client:
        assert client.post("/configure", json=config).status_code == 200

        create_response = client.post(
            "/memories",
            json={
                "messages": [{"role": "user", "content": "remember this"}],
                "user_id": "user-1",
                "metadata": {"source": "chat"},
            },
        )
        assert create_response.status_code == 200
        assert create_response.json()["id"] == "memory-2"

        list_response = client.get("/memories", params={"user_id": "user-1"})
        assert list_response.status_code == 200
        # mem0 2.0.0 explicit ``get_all`` kwargs: identifier lives INSIDE
        # ``filters`` and ``top_k`` is pinned to the SDK default.
        assert list_response.json()[0]["params"] == {
            "filters": {"user_id": "user-1"},
            "top_k": 20,
        }

        get_response = client.get("/memories/memory-1")
        assert get_response.status_code == 200
        assert get_response.json()["id"] == "memory-1"

        search_response = client.post(
            "/search",
            json={
                "query": "stored",
                "user_id": "user-1",
                "filters": {"source": "chat"},
            },
        )
        assert search_response.status_code == 200
        assert search_response.json()["params"] == {
            "user_id": "user-1",
            "filters": {"source": "chat"},
        }
        assert search_response.json()["results"][0]["_retrieval"] == {
            "stage": "semantic",
            "source": "memory_store",
            "strategy": "semantic",
        }

        update_response = client.put(
            "/memories/memory-1", json={"memory": "updated memory"}
        )
        assert update_response.status_code == 200
        assert update_response.json()["memory"] == "updated memory"

        history_response = client.get("/memories/memory-1/history")
        assert history_response.status_code == 200
        assert history_response.json() == {
            "memory_id": "memory-1",
            "results": [
                {
                    "memory_id": "memory-1",
                    "event": "created",
                    "anchor": None,
                }
            ],
            "backend_capabilities": {"anchors": True},
        }

        delete_response = client.delete("/memories/memory-1")
        assert delete_response.status_code == 200
        assert delete_response.json() == {"message": "Memory deleted successfully"}

        delete_all_response = client.delete("/memories", params={"user_id": "user-1"})
        assert delete_all_response.status_code == 200
        assert delete_all_response.json() == {
            "message": "All relevant memories deleted"
        }

        reset_response = client.post("/reset")
        assert reset_response.status_code == 200
        assert reset_response.json() == {"message": "All memories reset"}

    fake_memory = app.state.memory
    assert isinstance(fake_memory, FakeMemory)
    assert fake_memory.deleted_ids == ["memory-1"]
    assert fake_memory.deleted_batches == [{"user_id": "user-1"}]
    assert fake_memory.was_reset is True


def test_retrieval_service_lexical_search_hits_rare_metadata_keyword_without_network():
    retrieval_service = RetrievalService()

    class FakeMemory:
        def __init__(self) -> None:
            self.get_all_calls: list[dict[str, Any]] = []

        def get_all(self, **params: Any) -> list[dict[str, Any]]:
            self.get_all_calls.append(params)
            return [
                {
                    "id": "memory-common",
                    "memory": "General project planning notes",
                    "metadata": {"source": "chat", "topic": "planning"},
                },
                {
                    "id": "memory-rare",
                    "memory": "Deployment checklist for the release train",
                    "metadata": {
                        "source": "chat",
                        "anchor": "zephyr-rare-keyword-947",
                    },
                },
            ]

        def search(self, **_: Any) -> Any:
            raise AssertionError("lexical retrieval should not call semantic search")

    fake_memory = FakeMemory()

    response = retrieval_service.lexical_search(
        fake_memory,
        query="zephyr-rare-keyword-947",
        user_id="user-1",
        filters={"source": "chat"},
    )

    assert fake_memory.get_all_calls == [
        {"filters": {"user_id": "user-1"}, "top_k": 20}
    ]
    assert [result["id"] for result in response["results"]] == ["memory-rare"]
    assert response["results"][0]["_retrieval"] == {
        "stage": "lexical",
        "source": "memory_store",
        "strategy": "keyword",
        "score": 2,
    }
    assert response["params"] == {
        "user_id": "user-1",
        "filters": {"source": "chat"},
    }


def test_structured_anchor_metadata_is_persisted_and_canonicalized(
    monkeypatch: MonkeyPatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config
            self.records: dict[str, dict[str, Any]] = {}
            self.last_search_params: dict[str, Any] | None = None

        def add(
            self, *, messages: list[dict[str, Any]], **params: Any
        ) -> dict[str, Any]:
            record = {
                "id": "memory-1",
                "messages": messages,
                **params,
            }
            self.records["memory-1"] = record
            return record

        def get(self, memory_id: str) -> dict[str, Any]:
            return self.records[memory_id]

        def get_all(self, **_: Any) -> list[dict[str, Any]]:
            return list(self.records.values())

        def search(self, *, query: str, **params: Any) -> dict[str, Any]:
            self.last_search_params = params
            return {
                "query": query,
                "params": params,
                "results": list(self.records.values()),
            }

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-5", "api_key": "test-key"},
        },
        "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
        "history_db_path": "/tmp/history.db",
    }
    anchor_payload = {
        "type": "file",
        "repo": "github.com/acme/project",
        "locator": "./docs\\anchors.md",
        "ref": "refs/heads/main",
        "commit_sha": "abc123def456",
        "url": "https://github.com/acme/project/blob/abc123def456/docs/anchors.md",
        "title": "anchors.md",
        "created_at": "2026-05-04T12:00:00Z",
        "observed_at": "2026-05-04T12:00:00Z",
        "is_stale": False,
        "provenance": {
            "mode": "verified",
            "source": "observed",
            "commit_pinned": True,
        },
    }

    with TestClient(app) as client:
        assert client.post("/configure", json=config).status_code == 200

        create_response = client.post(
            "/memories",
            json={
                "messages": [{"role": "user", "content": "remember this file"}],
                "user_id": "user-1",
                "metadata": {"source": "chat", "anchor": anchor_payload},
            },
        )
        assert create_response.status_code == 200

        created_record = create_response.json()
        assert created_record["anchor"] == {
            **anchor_payload,
            "locator": "docs/anchors.md",
        }
        assert created_record["metadata"]["anchor"] == created_record["anchor"]
        assert created_record["metadata"]["anchor_locator"] == "docs/anchors.md"
        assert created_record["metadata"]["anchor_is_stale"] is False
        assert created_record["metadata"]["anchor_commit_pinned"] is True
        assert created_record["metadata"]["anchor_is_verified"] is True
        assert created_record["metadata"]["anchor_is_derived"] is False

        get_response = client.get("/memories/memory-1")
        assert get_response.status_code == 200
        assert get_response.json()["anchor"] == created_record["anchor"]

        search_response = client.post(
            "/search",
            json={
                "query": "anchors",
                "user_id": "user-1",
                "filters": {
                    "anchor": {
                        "type": "file",
                        "locator": "./docs\\anchors.md",
                        "provenance": {"mode": "verified"},
                    }
                },
            },
        )
        assert search_response.status_code == 200
        assert search_response.json()["params"]["filters"] == {
            "anchor": {
                "type": "file",
                "locator": "docs/anchors.md",
                "provenance": {"mode": "verified"},
            },
            "anchor_type": "file",
            "anchor_locator": "docs/anchors.md",
            "anchor_provenance_mode": "verified",
            "anchor_is_verified": True,
            "anchor_is_derived": False,
        }
        assert (
            search_response.json()["results"][0]["anchor"] == created_record["anchor"]
        )


def test_legacy_or_unresolved_memories_return_null_anchor_without_failure(
    monkeypatch: MonkeyPatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config
            self.records = {
                "legacy-memory": {
                    "id": "legacy-memory",
                    "memory": "old note",
                    "metadata": {"source": "chat", "anchor": "legacy-anchor-text"},
                },
                "unresolved-memory": {
                    "id": "unresolved-memory",
                    "memory": "new note without repo context",
                    "metadata": {"source": "chat", "anchor": None},
                },
            }

        def get(self, memory_id: str) -> dict[str, Any]:
            return self.records[memory_id]

        def get_all(self, **_: Any) -> list[dict[str, Any]]:
            return list(self.records.values())

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-5", "api_key": "test-key"},
        },
        "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
        "history_db_path": "/tmp/history.db",
    }

    with TestClient(app) as client:
        assert client.post("/configure", json=config).status_code == 200

        legacy_response = client.get("/memories/legacy-memory")
        assert legacy_response.status_code == 200
        assert legacy_response.json()["metadata"]["anchor"] == "legacy-anchor-text"
        assert legacy_response.json()["anchor"] is None

        unresolved_response = client.get("/memories/unresolved-memory")
        assert unresolved_response.status_code == 200
        assert unresolved_response.json()["metadata"]["anchor"] is None
        assert unresolved_response.json()["anchor"] is None

        list_response = client.get("/memories", params={"user_id": "user-1"})
        assert list_response.status_code == 200
        assert [item["anchor"] for item in list_response.json()] == [None, None]


def test_read_search_and_history_return_anchor_aware_generic_json(
    monkeypatch: MonkeyPatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    anchor_payload = {
        "type": "file",
        "repo": "github.com/acme/project",
        "locator": "docs/anchors.md",
        "ref": "refs/heads/main",
        "commit_sha": "abc123def456",
        "url": "https://github.com/acme/project/blob/abc123def456/docs/anchors.md",
        "title": "anchors.md",
        "created_at": "2026-05-04T12:00:00Z",
        "observed_at": "2026-05-04T12:00:00Z",
        "is_stale": False,
        "provenance": {
            "mode": "verified",
            "source": "observed",
            "commit_pinned": True,
        },
    }

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config
            self.records = {
                "anchored-memory": {
                    "id": "anchored-memory",
                    "memory": "Anchor-aware memory",
                    "metadata": {"source": "chat", "anchor": anchor_payload},
                },
                "legacy-memory": {
                    "id": "legacy-memory",
                    "memory": "Legacy memory",
                    "metadata": {"source": "chat", "anchor": "legacy-anchor-text"},
                },
            }

        def get(self, memory_id: str) -> dict[str, Any]:
            return self.records[memory_id]

        def get_all(self, **_: Any) -> list[dict[str, Any]]:
            return list(self.records.values())

        def search(self, *, query: str, **params: Any) -> dict[str, Any]:
            return {
                "query": query,
                "params": params,
                "results": [
                    self.records["anchored-memory"],
                    self.records["legacy-memory"],
                ],
            }

        def history(self, *, memory_id: str) -> list[dict[str, Any]]:
            return [
                {
                    "memory_id": memory_id,
                    "event": "created",
                    "metadata": {"source": "chat", "anchor": anchor_payload},
                },
                {
                    "memory_id": memory_id,
                    "event": "updated",
                    "metadata": {"source": "chat", "anchor": None},
                },
            ]

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-5", "api_key": "test-key"},
        },
        "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
        "history_db_path": "/tmp/history.db",
    }

    with TestClient(app) as client:
        assert client.post("/configure", json=config).status_code == 200

        read_response = client.get("/memories/anchored-memory")
        assert read_response.status_code == 200
        read_payload = read_response.json()
        assert read_payload["anchor"] == anchor_payload

        search_response = client.post(
            "/search",
            json={
                "query": "anchor-aware",
                "user_id": "user-1",
                "filters": {"source": "chat"},
            },
        )
        assert search_response.status_code == 200
        search_payload = search_response.json()
        assert search_payload["results"][0]["anchor"] == anchor_payload
        assert search_payload["results"][1]["anchor"] is None

        history_response = client.get("/memories/anchored-memory/history")
        assert history_response.status_code == 200
        history_payload = history_response.json()
        assert history_payload["memory_id"] == "anchored-memory"
        assert history_payload["backend_capabilities"] == {"anchors": True}
        assert history_payload["results"][0]["memory_id"] == "anchored-memory"
        assert history_payload["results"][0]["event"] == "created"
        assert history_payload["results"][0]["anchor"] == anchor_payload
        assert history_payload["results"][1]["memory_id"] == "anchored-memory"
        assert history_payload["results"][1]["event"] == "updated"
        assert history_payload["results"][1]["anchor"] is None

    for payload in (read_payload, search_payload, history_payload):
        assert "[MEM0 CONTEXT]" not in json.dumps(payload)


def test_write_derives_commit_pinned_anchor_from_anchor_context(
    monkeypatch: MonkeyPatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config
            self.records: dict[str, dict[str, Any]] = {}

        def add(
            self, *, messages: list[dict[str, Any]], **params: Any
        ) -> dict[str, Any]:
            record: dict[str, Any] = {
                "id": "memory-derived-1",
                "messages": messages,
                **params,
            }
            self.records[record["id"]] = record
            return record

        def get(self, memory_id: str) -> dict[str, Any]:
            return self.records[memory_id]

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-5", "api_key": "test-key"},
        },
        "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
        "history_db_path": "/tmp/history.db",
    }

    with TestClient(app) as client:
        assert client.post("/configure", json=config).status_code == 200

        create_response = client.post(
            "/memories",
            json={
                "messages": [{"role": "user", "content": "remember this file"}],
                "user_id": "user-1",
                "metadata": {
                    "source": "chat",
                    "anchor_context": {
                        "repo": "github.com/acme/project",
                        "path": "./docs\\anchors.md",
                        "ref": "refs/heads/main",
                        "commit_sha": "abc123def456",
                    },
                },
            },
        )
        assert create_response.status_code == 200

        created_record = create_response.json()
        assert created_record["anchor"] == {
            "type": "file",
            "repo": "github.com/acme/project",
            "locator": "docs/anchors.md",
            "ref": "refs/heads/main",
            "commit_sha": "abc123def456",
            "url": "https://github.com/acme/project/blob/abc123def456/docs/anchors.md",
            "title": "anchors.md",
            "created_at": created_record["anchor"]["created_at"],
            "observed_at": created_record["anchor"]["created_at"],
            "is_stale": False,
            "provenance": {
                "mode": "verified",
                "source": "observed",
                "commit_pinned": True,
            },
        }
        assert created_record["metadata"]["anchor"] == created_record["anchor"]
        assert "anchor_context" not in created_record["metadata"]
        assert created_record["metadata"]["anchor_type"] == "file"
        assert created_record["metadata"]["anchor_commit_sha"] == "abc123def456"
        assert created_record["metadata"]["anchor_commit_pinned"] is True
        assert created_record["metadata"]["anchor_is_verified"] is True
        assert created_record["metadata"]["anchor_is_derived"] is False

        get_response = client.get("/memories/memory-derived-1")
        assert get_response.status_code == 200
        assert get_response.json()["anchor"] == created_record["anchor"]


def test_write_with_incomplete_anchor_context_stays_derived_and_non_fatal(
    monkeypatch: MonkeyPatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config
            self.records: dict[str, dict[str, Any]] = {}

        def add(
            self, *, messages: list[dict[str, Any]], **params: Any
        ) -> dict[str, Any]:
            record: dict[str, Any] = {
                "id": "memory-derived-2",
                "messages": messages,
                **params,
            }
            self.records[record["id"]] = record
            return record

        def get(self, memory_id: str) -> dict[str, Any]:
            return self.records[memory_id]

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-5", "api_key": "test-key"},
        },
        "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
        "history_db_path": "/tmp/history.db",
    }

    with TestClient(app) as client:
        assert client.post("/configure", json=config).status_code == 200

        create_response = client.post(
            "/memories",
            json={
                "messages": [
                    {"role": "user", "content": "remember this weak file hint"}
                ],
                "user_id": "user-1",
                "metadata": {
                    "source": "chat",
                    "anchor_context": {
                        "repo": "github.com/acme/project",
                        "path": "notes/weak-anchor.md",
                    },
                },
            },
        )
        assert create_response.status_code == 200

        created_record = create_response.json()
        assert created_record["anchor"] == {
            "type": "file",
            "repo": "github.com/acme/project",
            "locator": "notes/weak-anchor.md",
            "ref": None,
            "commit_sha": None,
            "url": None,
            "title": "weak-anchor.md",
            "created_at": created_record["anchor"]["created_at"],
            "observed_at": None,
            "is_stale": False,
            "provenance": {
                "mode": "derived",
                "source": "inferred-from-context",
                "commit_pinned": False,
            },
        }
        assert created_record["metadata"]["anchor"] == created_record["anchor"]
        assert "anchor_context" not in created_record["metadata"]
        assert created_record["metadata"]["anchor_commit_sha"] is None
        assert created_record["metadata"]["anchor_commit_pinned"] is False
        assert created_record["metadata"]["anchor_is_verified"] is False
        assert created_record["metadata"]["anchor_is_derived"] is True

        get_response = client.get("/memories/memory-derived-2")
        assert get_response.status_code == 200
        assert get_response.json()["anchor"] == created_record["anchor"]


def test_search_propagates_correlation_id_and_structured_retrieval_trace(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config
            self.records = {
                "memory-1": {
                    "id": "memory-1",
                    "memory": "stored memory",
                    "metadata": {"source": "chat", "topic": "planning"},
                },
                "memory-2": {
                    "id": "memory-2",
                    "memory": "release checklist",
                    "metadata": {"source": "docs", "topic": "release"},
                },
            }

        def search(self, *, query: str, **params: Any) -> dict[str, Any]:
            return {
                "query": query,
                "params": params,
                "results": [self.records["memory-1"]],
            }

        def get_all(self, **_: Any) -> list[dict[str, Any]]:
            return list(self.records.values())

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-5", "api_key": "test-key"},
        },
        "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
        "history_db_path": "/tmp/history.db",
    }

    with TestClient(app) as client:
        assert client.post("/configure", json=config).status_code == 200

        correlation_id = "corr-search-123"
        caplog.clear()
        with caplog.at_level(logging.INFO):
            search_response = client.post(
                "/search",
                headers={"X-Correlation-ID": correlation_id},
                json={
                    "query": "stored",
                    "user_id": "user-1",
                    "filters": {"source": "chat"},
                },
            )

    assert search_response.status_code == 200
    assert search_response.headers["X-Correlation-ID"] == correlation_id

    response_payload = search_response.json()
    assert response_payload["trace"]["request_id"] == correlation_id
    assert response_payload["trace"]["retrieval"]["lexical_count"] == 1
    assert response_payload["trace"]["retrieval"]["semantic_count"] == 1
    assert response_payload["trace"]["retrieval"]["rerank_applied"] is False
    assert response_payload["trace"]["retrieval"]["degraded"] == {
        "lexical": False,
        "semantic": False,
        "rerank": False,
    }
    assert set(response_payload["trace"]["retrieval"]["latency_ms"].keys()) == {
        "lexical",
        "semantic",
        "rerank",
        "total",
    }
    assert all(
        isinstance(response_payload["trace"]["retrieval"]["latency_ms"][key], float)
        for key in ("lexical", "semantic", "rerank", "total")
    )

    structured_logs = []
    for record in caplog.records:
        try:
            structured_logs.append(json.loads(record.message))
        except json.JSONDecodeError:
            continue

    assert structured_logs
    assert any(log.get("event") == "request.started" for log in structured_logs)
    assert any(log.get("event") == "memory.search" for log in structured_logs)
    retrieval_logs = [
        log for log in structured_logs if log.get("event") == "retrieval.search"
    ]
    assert len(retrieval_logs) == 1
    assert retrieval_logs[0]["request_id"] == correlation_id
    assert retrieval_logs[0]["retrieval"]["lexical_count"] == 1
    assert retrieval_logs[0]["retrieval"]["semantic_count"] == 1
    assert retrieval_logs[0]["retrieval"]["rerank_applied"] is False
    assert retrieval_logs[0]["retrieval"]["degraded"] == {
        "lexical": False,
        "semantic": False,
        "rerank": False,
    }
    assert any(log.get("event") == "request.completed" for log in structured_logs)
    assert all(log.get("request_id") == correlation_id for log in structured_logs)
    rendered_logs = "\n".join(record.message for record in caplog.records)
    assert "stored memory" not in rendered_logs
    assert '"content"' not in rendered_logs


def test_retrieve_returns_fused_ranked_results_with_capabilities(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config
            self.records = {
                "memory-both": {
                    "id": "memory-both",
                    "memory": "Use POST /retrieve as the canonical backend retrieval endpoint.",
                    "metadata": {
                        "source": "chat",
                        "scope": "project",
                        "type": "decision",
                    },
                    "updated_at": "2026-05-04T12:00:00Z",
                },
                "memory-lexical": {
                    "id": "memory-lexical",
                    "memory": "The retrieve endpoint should keep truthful backend capability flags.",
                    "metadata": {
                        "source": "chat",
                        "scope": "project",
                        "type": "stable-fact",
                    },
                    "updated_at": "2026-05-04T11:00:00Z",
                },
                "memory-semantic": {
                    "id": "memory-semantic",
                    "memory": "Ranking decisions stay on the backend for compatibility.",
                    "metadata": {
                        "source": "chat",
                        "scope": "project",
                        "type": "stable-fact",
                    },
                    "updated_at": "2026-05-04T10:00:00Z",
                },
            }

        def get_all(self, **_: Any) -> list[dict[str, Any]]:
            return list(self.records.values())

        def search(self, *, query: str, **params: Any) -> dict[str, Any]:
            assert query == "canonical retrieve endpoint"
            # mem0 2.0.0 explicit kwargs: identifiers live INSIDE ``filters``;
            # ``top_k``/``threshold``/``rerank`` are explicit defaults.
            assert params == {
                "top_k": 20,
                "filters": {
                    "user_id": "user-1",
                    "source": "chat",
                    "scope": "project",
                },
                "threshold": 0.1,
                "rerank": False,
            }
            return {
                "results": [
                    {**self.records["memory-both"], "score": 0.91},
                    {**self.records["memory-semantic"], "score": 0.78},
                ]
            }

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-5", "api_key": "test-key"},
        },
        "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
        "history_db_path": "/tmp/history.db",
    }

    with TestClient(app) as client:
        assert client.post("/configure", json=config).status_code == 200

        caplog.clear()
        with caplog.at_level(logging.INFO):
            retrieve_response = client.post(
                "/retrieve",
                headers={"X-Correlation-ID": "corr-retrieve-123"},
                json={
                    "query": "canonical retrieve endpoint",
                    "scopes": ["project"],
                    "user_id": "user-1",
                    "limit": 3,
                    "filters": {"source": "chat"},
                },
            )

    assert retrieve_response.status_code == 200
    payload = retrieve_response.json()
    assert payload["request_id"] == "corr-retrieve-123"
    assert payload["backend_capabilities"] == {
        "lexical": True,
        "semantic": True,
        "rerank": True,
        "anchors": True,
    }
    assert payload["degraded"] is False
    assert payload["degradation_reasons"] == []
    assert [result["id"] for result in payload["results"]] == [
        "memory-both",
        "memory-lexical",
        "memory-semantic",
    ]
    assert payload["results"][0]["retrieval"] == {
        "matched_by": ["lexical", "semantic"],
        "lexical_score": 3.0,
        "semantic_score": 0.91,
        "reranked": True,
        "rank_position": 1,
    }
    assert payload["results"][0]["_retrieval"]["stage"] == "hybrid"
    assert payload["results"][1]["retrieval"]["matched_by"] == ["lexical"]
    assert payload["results"][2]["retrieval"]["matched_by"] == ["semantic"]
    assert payload["trace"]["retrieval"]["rerank_applied"] is True
    assert payload["trace"]["retrieval"]["backend_capabilities"]["rerank"] is True

    structured_logs = [
        json.loads(record.message)
        for record in caplog.records
        if record.message.startswith("{")
    ]
    retrieve_logs = [
        log for log in structured_logs if log.get("event") == "retrieval.retrieve"
    ]
    assert len(retrieve_logs) == 1
    assert retrieve_logs[0]["request_id"] == "corr-retrieve-123"
    assert retrieve_logs[0]["retrieval"]["result_count"] == 3


def test_retrieve_degrades_to_lexical_results_when_semantic_fails(
    monkeypatch: MonkeyPatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

        def get_all(self, **_: Any) -> list[dict[str, Any]]:
            return [
                {
                    "id": "memory-1",
                    "memory": "Use POST /retrieve as the canonical backend retrieval endpoint.",
                    "metadata": {"source": "chat", "scope": "project"},
                }
            ]

        def search(self, **_: Any) -> Any:
            raise RuntimeError("semantic offline")

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-5", "api_key": "test-key"},
        },
        "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
        "history_db_path": "/tmp/history.db",
    }

    with TestClient(app) as client:
        assert client.post("/configure", json=config).status_code == 200
        retrieve_response = client.post(
            "/retrieve",
            json={
                "query": "canonical retrieve endpoint",
                "scopes": ["project"],
                "user_id": "user-1",
                "filters": {"source": "chat"},
            },
        )

    assert retrieve_response.status_code == 200
    payload = retrieve_response.json()
    assert [result["id"] for result in payload["results"]] == ["memory-1"]
    assert payload["backend_capabilities"] == {
        "lexical": True,
        "semantic": False,
        "rerank": False,
        "anchors": True,
    }
    assert payload["degraded"] is True
    assert payload["degradation_reasons"] == ["semantic_unavailable", "rerank_skipped"]
    assert payload["results"][0]["retrieval"] == {
        "matched_by": ["lexical"],
        "lexical_score": 3.0,
        "semantic_score": None,
        "reranked": False,
        "rank_position": 1,
    }


def test_retrieve_ignores_include_cold_context_control_flag_for_filtering(
    monkeypatch: MonkeyPatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config
            self.last_search_params: dict[str, Any] | None = None

        def get_all(self, **_: Any) -> list[dict[str, Any]]:
            return [
                {
                    "id": "memory-1",
                    "memory": "Use POST /retrieve as the canonical backend retrieval endpoint.",
                    "metadata": {"source": "chat", "scope": "project"},
                }
            ]

        def search(self, **params: Any) -> dict[str, Any]:
            self.last_search_params = params
            return {
                "results": [
                    {
                        "id": "memory-1",
                        "memory": "Use POST /retrieve as the canonical backend retrieval endpoint.",
                        "metadata": {"source": "chat", "scope": "project"},
                        "score": 0.88,
                    }
                ]
            }

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-5", "api_key": "test-key"},
        },
        "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
        "history_db_path": "/tmp/history.db",
    }

    with TestClient(app) as client:
        assert client.post("/configure", json=config).status_code == 200
        retrieve_response = client.post(
            "/retrieve",
            json={
                "query": "canonical retrieve endpoint",
                "scopes": ["project"],
                "user_id": "user-1",
                "filters": {
                    "source": "chat",
                    "include_cold_context": False,
                },
            },
        )

    assert retrieve_response.status_code == 200
    payload = retrieve_response.json()
    assert [result["id"] for result in payload["results"]] == ["memory-1"]

    fake_memory = app.state.memory
    assert isinstance(fake_memory, FakeMemory)
    # mem0 2.0.0 explicit kwargs: identifiers live INSIDE ``filters``;
    # ``top_k``/``threshold``/``rerank`` are explicit defaults; the
    # ``include_cold_context`` control flag is stripped by the retrieval
    # service before reaching the backend.
    assert fake_memory.last_search_params == {
        "query": "canonical retrieve endpoint",
        "top_k": 20,
        "filters": {
            "user_id": "user-1",
            "source": "chat",
            "scope": "project",
        },
        "threshold": 0.1,
        "rerank": False,
    }


def test_retrieve_returns_fused_results_when_rerank_fails(monkeypatch: MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config
            self.records = {
                "memory-1": {
                    "id": "memory-1",
                    "memory": "Use POST /retrieve as the canonical backend retrieval endpoint.",
                    "metadata": {"source": "chat", "scope": "project"},
                },
                "memory-2": {
                    "id": "memory-2",
                    "memory": "Backend capabilities must stay truthful in degraded retrieval.",
                    "metadata": {"source": "chat", "scope": "project"},
                },
            }

        def get_all(self, **_: Any) -> list[dict[str, Any]]:
            return list(self.records.values())

        def search(self, **_: Any) -> dict[str, Any]:
            return {"results": [{**self.records["memory-1"], "score": 0.88}]}

    class FailingReranker:
        def rerank(
            self, *, query: str, candidates: list[dict[str, Any]]
        ) -> list[dict[str, Any]]:
            raise RuntimeError(
                f"rerank failed for {query} with {len(candidates)} candidates"
            )

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)
    app.state.memory_service._retrieval_service._reranker = FailingReranker()

    config: dict[str, Any] = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-5", "api_key": "test-key"},
        },
        "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
        "history_db_path": "/tmp/history.db",
    }

    with TestClient(app) as client:
        assert client.post("/configure", json=config).status_code == 200
        retrieve_response = client.post(
            "/retrieve",
            json={
                "query": "canonical retrieve endpoint",
                "scopes": ["project"],
                "user_id": "user-1",
                "filters": {"source": "chat"},
            },
        )

    assert retrieve_response.status_code == 200
    payload = retrieve_response.json()
    assert payload["backend_capabilities"] == {
        "lexical": True,
        "semantic": True,
        "rerank": False,
        "anchors": True,
    }
    assert payload["degraded"] is True
    assert payload["degradation_reasons"] == ["rerank_unavailable"]
    assert payload["results"][0]["id"] == "memory-1"
    assert payload["results"][0]["retrieval"]["matched_by"] == ["lexical", "semantic"]
    assert payload["results"][0]["retrieval"]["reranked"] is False


# ---------------------------------------------------------------------------
# Regression tests – explicit field-shape contracts
# These tests MUST NOT regress when cross-corpus or retrieval work lands.
# ---------------------------------------------------------------------------


def _make_fake_memory_class_with_records(
    records: "dict[str, dict[str, Any]]",
) -> "type":
    """Return a FakeMemory class pre-loaded with the supplied records."""

    class FakeMemory:
        def __init__(self, config: "dict[str, Any]") -> None:
            self.config = config
            self.records: "dict[str, dict[str, Any]]" = dict(records)

        def add(
            self, *, messages: "list[dict[str, Any]]", **params: "Any"
        ) -> "dict[str, Any]":
            new_id = "memory-new"
            record = {"id": new_id, "messages": messages, **params}
            self.records[new_id] = record
            return record

        def get(self, memory_id: str) -> "dict[str, Any]":
            return self.records[memory_id]

        def get_all(self, **_: "Any") -> "list[dict[str, Any]]":
            return list(self.records.values())

        def search(self, *, query: str, **params: "Any") -> "dict[str, Any]":
            return {
                "query": query,
                "params": params,
                "results": list(self.records.values()),
            }

        def update(
            self, *, memory_id: str, data: "dict[str, Any]"
        ) -> "dict[str, Any]":
            self.records[memory_id] = {**self.records[memory_id], **data}
            return self.records[memory_id]

        def delete(self, *, memory_id: str) -> None:
            self.records.pop(memory_id, None)

        def delete_all(self, **_: "Any") -> None:
            self.records.clear()

        def reset(self) -> None:
            self.records.clear()

        def history(self, *, memory_id: str) -> "list[dict[str, Any]]":
            return [{"memory_id": memory_id, "event": "created"}]

    return FakeMemory


_MINIMAL_CONFIG: "dict[str, Any]" = {
    "version": "v1.1",
    "vector_store": {"provider": "pgvector", "config": {}},
    "llm": {
        "provider": "openai",
        "config": {"model": "gpt-5", "api_key": "test-key"},
    },
    "embedder": {"provider": "openai", "config": {"api_key": "test-key"}},
    "history_db_path": "/tmp/history.db",
}

_SAMPLE_RECORDS: "dict[str, dict[str, Any]]" = {
    "mem-r1": {
        "id": "mem-r1",
        "memory": "regression memory one",
        "metadata": {"source": "chat"},
    },
    "mem-r2": {
        "id": "mem-r2",
        "memory": "regression memory two",
        "metadata": {"source": "chat"},
    },
}


def test_regression_search_result_retrieval_field_exact_shape(
    monkeypatch: MonkeyPatch,
) -> None:
    """Regression: every result in /search must carry a _retrieval dict with exactly
    {stage, source, strategy} and no extra or missing keys.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    FakeMemory = _make_fake_memory_class_with_records(_SAMPLE_RECORDS)
    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        assert client.post("/configure", json=_MINIMAL_CONFIG).status_code == 200

        resp = client.post(
            "/search",
            json={"query": "regression", "user_id": "user-1"},
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) >= 1, "Expected at least one search result"

    for result in results:
        assert "_retrieval" in result, "Each search result must have _retrieval"
        retrieval = result["_retrieval"]
        assert isinstance(retrieval, dict), "_retrieval must be a dict"
        # Exact required keys – no more, no less
        assert set(retrieval.keys()) == {
            "stage",
            "source",
            "strategy",
        }, f"_retrieval keys mismatch: {set(retrieval.keys())}"
        assert retrieval["stage"] == "semantic", (
            f"Default search stage must be 'semantic', got {retrieval['stage']!r}"
        )
        assert retrieval["source"] == "memory_store", (
            f"Default search source must be 'memory_store', got {retrieval['source']!r}"
        )
        assert retrieval["strategy"] == "semantic", (
            f"Default search strategy must be 'semantic', got {retrieval['strategy']!r}"
        )


def test_regression_search_trace_retrieval_nested_field_contract(
    monkeypatch: MonkeyPatch,
) -> None:
    """Regression: /search response must contain trace.retrieval with specific keys and
    value types that must not change between refactors.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    FakeMemory = _make_fake_memory_class_with_records(_SAMPLE_RECORDS)
    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        assert client.post("/configure", json=_MINIMAL_CONFIG).status_code == 200

        resp = client.post(
            "/search",
            headers={"X-Correlation-ID": "reg-trace-001"},
            json={"query": "regression", "user_id": "user-1"},
        )

    assert resp.status_code == 200
    payload = resp.json()

    # Top-level trace key must exist
    assert "trace" in payload, "Search response must include top-level 'trace'"
    trace = payload["trace"]
    assert isinstance(trace, dict), "'trace' must be a dict"

    # trace.request_id must echo the correlation header
    assert trace["request_id"] == "reg-trace-001", (
        "trace.request_id must echo X-Correlation-ID header"
    )

    # trace.retrieval must be a dict with the required keys
    assert "retrieval" in trace, "trace must include 'retrieval'"
    r = trace["retrieval"]
    assert isinstance(r, dict), "trace.retrieval must be a dict"

    required_scalar_keys = {
        "lexical_count",
        "semantic_count",
        "rerank_applied",
    }
    for key in required_scalar_keys:
        assert key in r, f"trace.retrieval must include '{key}'"

    assert isinstance(r["lexical_count"], int), "trace.retrieval.lexical_count is int"
    assert isinstance(r["semantic_count"], int), "trace.retrieval.semantic_count is int"
    assert isinstance(r["rerank_applied"], bool), "trace.retrieval.rerank_applied is bool"

    # degraded sub-dict with three boolean keys
    assert "degraded" in r, "trace.retrieval must include 'degraded'"
    degraded = r["degraded"]
    assert isinstance(degraded, dict), "trace.retrieval.degraded must be a dict"
    for sub_key in ("lexical", "semantic", "rerank"):
        assert sub_key in degraded, f"trace.retrieval.degraded must include '{sub_key}'"
        assert isinstance(degraded[sub_key], bool), (
            f"trace.retrieval.degraded.{sub_key} must be bool"
        )

    # latency_ms sub-dict with four float keys
    assert "latency_ms" in r, "trace.retrieval must include 'latency_ms'"
    latency = r["latency_ms"]
    assert isinstance(latency, dict), "trace.retrieval.latency_ms must be a dict"
    for lat_key in ("lexical", "semantic", "rerank", "total"):
        assert lat_key in latency, f"trace.retrieval.latency_ms must include '{lat_key}'"
        assert isinstance(latency[lat_key], float), (
            f"trace.retrieval.latency_ms.{lat_key} must be float"
        )


def test_regression_retrieve_backend_capabilities_field_contract(
    monkeypatch: MonkeyPatch,
) -> None:
    """Regression: /retrieve response must carry backend_capabilities with exactly the
    four boolean keys: lexical, semantic, rerank, anchors.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    FakeMemory = _make_fake_memory_class_with_records(_SAMPLE_RECORDS)
    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        assert client.post("/configure", json=_MINIMAL_CONFIG).status_code == 200

        resp = client.post(
            "/retrieve",
            json={
                "query": "regression memory",
                "scopes": ["project"],
                "user_id": "user-1",
            },
        )

    assert resp.status_code == 200
    payload = resp.json()

    assert "backend_capabilities" in payload, (
        "/retrieve response must include 'backend_capabilities'"
    )
    caps = payload["backend_capabilities"]
    assert isinstance(caps, dict), "backend_capabilities must be a dict"
    assert set(caps.keys()) == {
        "lexical",
        "semantic",
        "rerank",
        "anchors",
    }, f"backend_capabilities keys mismatch: {set(caps.keys())}"
    for key, value in caps.items():
        assert isinstance(value, bool), (
            f"backend_capabilities.{key} must be bool, got {type(value)}"
        )


def test_regression_retrieve_degradation_reasons_is_empty_list_when_healthy(
    monkeypatch: MonkeyPatch,
) -> None:
    """Regression: when /retrieve completes without errors, degradation_reasons must be
    an empty list and degraded must be False.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: "dict[str, Any]") -> None:
            self.config = config

        def get_all(self, **_: "Any") -> "list[dict[str, Any]]":
            return [
                {
                    "id": "mem-healthy",
                    "memory": "regression healthy memory canonical retrieve",
                    "metadata": {"source": "chat"},
                }
            ]

        def search(self, *, query: str, **params: "Any") -> "dict[str, Any]":
            return {
                "results": [
                    {
                        "id": "mem-healthy",
                        "memory": "regression healthy memory canonical retrieve",
                        "metadata": {"source": "chat"},
                        "score": 0.90,
                    }
                ]
            }

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        assert client.post("/configure", json=_MINIMAL_CONFIG).status_code == 200

        resp = client.post(
            "/retrieve",
            json={
                "query": "canonical retrieve",
                "scopes": ["project"],
                "user_id": "user-1",
            },
        )

    assert resp.status_code == 200
    payload = resp.json()

    assert "degraded" in payload, "/retrieve response must include 'degraded'"
    assert payload["degraded"] is False, (
        "degraded must be False when no retrieval stage failed"
    )

    assert "degradation_reasons" in payload, (
        "/retrieve response must include 'degradation_reasons'"
    )
    assert payload["degradation_reasons"] == [], (
        "degradation_reasons must be empty list when healthy"
    )


def test_regression_retrieve_degradation_reasons_semantic_unavailable(
    monkeypatch: MonkeyPatch,
) -> None:
    """Regression: when semantic search throws, degradation_reasons must contain
    'semantic_unavailable' and 'rerank_skipped', and degraded must be True.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: "dict[str, Any]") -> None:
            self.config = config

        def get_all(self, **_: "Any") -> "list[dict[str, Any]]":
            return [
                {
                    "id": "mem-lex-only",
                    "memory": "regression semantic unavailable canonical retrieve",
                    "metadata": {"source": "chat"},
                }
            ]

        def search(self, **_: "Any") -> "Any":
            raise RuntimeError("semantic backend down")

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        assert client.post("/configure", json=_MINIMAL_CONFIG).status_code == 200

        resp = client.post(
            "/retrieve",
            json={
                "query": "canonical retrieve",
                "scopes": ["project"],
                "user_id": "user-1",
            },
        )

    assert resp.status_code == 200
    payload = resp.json()

    assert payload["degraded"] is True
    assert "semantic_unavailable" in payload["degradation_reasons"], (
        "degradation_reasons must contain 'semantic_unavailable'"
    )
    assert "rerank_skipped" in payload["degradation_reasons"], (
        "degradation_reasons must contain 'rerank_skipped'"
    )
    assert payload["backend_capabilities"]["semantic"] is False
    assert payload["backend_capabilities"]["rerank"] is False
    assert payload["backend_capabilities"]["lexical"] is True


def test_regression_retrieve_result_retrieval_meta_field_contract(
    monkeypatch: MonkeyPatch,
) -> None:
    """Regression: each result in /retrieve must carry both 'retrieval' (summary) and
    '_retrieval' (raw stage marker) with the expected keys.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: "dict[str, Any]") -> None:
            self.config = config

        def get_all(self, **_: "Any") -> "list[dict[str, Any]]":
            return [
                {
                    "id": "mem-contract",
                    "memory": "regression result meta canonical retrieve",
                    "metadata": {"source": "chat", "scope": "project"},
                }
            ]

        def search(self, *, query: str, **params: "Any") -> "dict[str, Any]":
            return {
                "results": [
                    {
                        "id": "mem-contract",
                        "memory": "regression result meta canonical retrieve",
                        "metadata": {"source": "chat", "scope": "project"},
                        "score": 0.88,
                    }
                ]
            }

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        assert client.post("/configure", json=_MINIMAL_CONFIG).status_code == 200

        resp = client.post(
            "/retrieve",
            json={
                "query": "canonical retrieve",
                "scopes": ["project"],
                "user_id": "user-1",
            },
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) >= 1

    for result in results:
        # High-level retrieval summary block
        assert "retrieval" in result, "Each /retrieve result must have 'retrieval'"
        ret = result["retrieval"]
        assert isinstance(ret, dict)
        for key in ("matched_by", "lexical_score", "semantic_score", "reranked", "rank_position"):
            assert key in ret, f"result.retrieval must include '{key}'"
        assert isinstance(ret["matched_by"], list), "matched_by must be a list"
        assert isinstance(ret["reranked"], bool), "reranked must be bool"
        assert isinstance(ret["rank_position"], int), "rank_position must be int"

        # Raw stage marker
        assert "_retrieval" in result, "Each /retrieve result must have '_retrieval'"
        raw = result["_retrieval"]
        assert isinstance(raw, dict), "_retrieval must be a dict"
        assert "stage" in raw, "_retrieval must include 'stage'"
        assert raw["stage"] in ("semantic", "lexical", "hybrid"), (
            f"_retrieval.stage must be semantic/lexical/hybrid, got {raw['stage']!r}"
        )


def test_regression_memory_crud_create_list_get_delete_response_shapes(
    monkeypatch: MonkeyPatch,
) -> None:
    """Regression: basic CRUD HTTP response shapes for /memories must stay stable.
    Asserts status codes, id presence on create, list returns iterable, get returns
    id, update reflects change, delete returns expected message.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    FakeMemory = _make_fake_memory_class_with_records(dict(_SAMPLE_RECORDS))
    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        assert client.post("/configure", json=_MINIMAL_CONFIG).status_code == 200

        # CREATE
        create_resp = client.post(
            "/memories",
            json={
                "messages": [{"role": "user", "content": "regression create test"}],
                "user_id": "user-reg",
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert "id" in created, "Create response must include 'id'"

        # LIST
        list_resp = client.get("/memories", params={"user_id": "user-reg"})
        assert list_resp.status_code == 200
        listed = list_resp.json()
        assert isinstance(listed, list), "List memories must return a JSON array"

        # GET
        get_resp = client.get("/memories/mem-r1")
        assert get_resp.status_code == 200
        gotten = get_resp.json()
        assert gotten["id"] == "mem-r1", "Get memory must return correct id"

        # UPDATE
        update_resp = client.put(
            "/memories/mem-r1", json={"memory": "regression updated"}
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()
        assert updated["memory"] == "regression updated", (
            "Update must reflect new memory value"
        )

        # DELETE single
        delete_resp = client.delete("/memories/mem-r1")
        assert delete_resp.status_code == 200
        assert delete_resp.json() == {"message": "Memory deleted successfully"}, (
            "Delete single must return exact message"
        )

        # DELETE all
        delete_all_resp = client.delete("/memories", params={"user_id": "user-reg"})
        assert delete_all_resp.status_code == 200
        assert delete_all_resp.json() == {"message": "All relevant memories deleted"}, (
            "Delete all must return exact message"
        )

        # RESET
        reset_resp = client.post("/reset")
        assert reset_resp.status_code == 200
        assert reset_resp.json() == {"message": "All memories reset"}, (
            "Reset must return exact message"
        )


def test_regression_retrieve_trace_retrieval_contains_backend_capabilities(
    monkeypatch: MonkeyPatch,
) -> None:
    """Regression: /retrieve trace.retrieval must carry a backend_capabilities sub-dict
    mirroring the top-level backend_capabilities (all four boolean keys).
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: "dict[str, Any]") -> None:
            self.config = config

        def get_all(self, **_: "Any") -> "list[dict[str, Any]]":
            return [
                {
                    "id": "mem-trace-caps",
                    "memory": "retrieve trace capabilities regression canonical",
                    "metadata": {"source": "chat"},
                }
            ]

        def search(self, *, query: str, **params: "Any") -> "dict[str, Any]":
            return {
                "results": [
                    {
                        "id": "mem-trace-caps",
                        "memory": "retrieve trace capabilities regression canonical",
                        "metadata": {"source": "chat"},
                        "score": 0.92,
                    }
                ]
            }

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    with TestClient(app) as client:
        assert client.post("/configure", json=_MINIMAL_CONFIG).status_code == 200

        resp = client.post(
            "/retrieve",
            headers={"X-Correlation-ID": "reg-caps-trace-001"},
            json={
                "query": "canonical trace caps",
                "scopes": ["project"],
                "user_id": "user-1",
            },
        )

    assert resp.status_code == 200
    payload = resp.json()

    assert "trace" in payload, "/retrieve response must have 'trace'"
    trace = payload["trace"]
    assert "retrieval" in trace, "/retrieve trace must have 'retrieval'"
    trace_ret = trace["retrieval"]

    assert "backend_capabilities" in trace_ret, (
        "trace.retrieval must include 'backend_capabilities'"
    )
    caps_in_trace = trace_ret["backend_capabilities"]
    assert set(caps_in_trace.keys()) == {
        "lexical",
        "semantic",
        "rerank",
        "anchors",
    }, f"trace.retrieval.backend_capabilities keys mismatch: {set(caps_in_trace.keys())}"
    for key, val in caps_in_trace.items():
        assert isinstance(val, bool), (
            f"trace.retrieval.backend_capabilities.{key} must be bool"
        )

    # Must mirror top-level backend_capabilities
    assert caps_in_trace == payload["backend_capabilities"], (
        "trace.retrieval.backend_capabilities must mirror top-level backend_capabilities"
    )

    # rerank_applied must also be present
    assert "rerank_applied" in trace_ret, "trace.retrieval must include 'rerank_applied'"
    assert isinstance(trace_ret["rerank_applied"], bool)


def _make_app_no_live_deps(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    importlib.reload(importlib.import_module("api_models"))
    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config):
            self.config = config

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)
    return app


def test_query_capabilities_route_returns_expected_shape(monkeypatch):
    app = _make_app_no_live_deps(monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/query/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert "memory_store" in body
    assert "file_corpus" in body
    assert isinstance(body["memory_store"], dict)
    assert isinstance(body["file_corpus"], dict)
    assert "X-Correlation-ID" in resp.headers


def test_unified_query_route_returns_expected_shape(monkeypatch):
    app = _make_app_no_live_deps(monkeypatch)
    with TestClient(app) as client:
        resp = client.post("/query", json={"query": "hello world", "corpora": ["file_corpus"]})
    assert resp.status_code == 200
    body = resp.json()
    assert "hits" in body
    assert "total" in body
    assert "corpora_queried" in body
    assert "degraded" in body
    assert "X-Correlation-ID" in resp.headers


def test_unified_query_rejects_empty_query(monkeypatch):
    app = _make_app_no_live_deps(monkeypatch)
    with TestClient(app) as client:
        resp = client.post("/query", json={"query": ""})
    assert resp.status_code == 422


REMOVED_INDEX_REQUESTS = (
    ("POST", "/index/sync"),
    ("GET", "/index/jobs"),
    ("GET", "/index/jobs/removed-job-123"),
    ("POST", "/index/watch/start"),
    ("POST", "/index/watch/stop"),
    ("GET", "/index/status"),
    ("POST", "/index/reset"),
)

REMOVED_INDEX_PATHS = {
    "/index/sync",
    "/index/jobs",
    "/index/jobs/{job_id}",
    "/index/watch/start",
    "/index/watch/stop",
    "/index/status",
    "/index/reset",
}

REMOVED_INDEX_REQUEST_SCHEMAS = {
    "IndexSyncRequest",
    "WatchStartRequest",
    "WatchStopRequest",
    "IndexResetRequest",
}


def test_removed_index_routes_return_404(monkeypatch):
    app = _make_app_no_live_deps(monkeypatch)
    with TestClient(app) as client:
        responses = [
            client.request(method, path, json={}, follow_redirects=False)
            for method, path in REMOVED_INDEX_REQUESTS
        ]

    for response in responses:
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"detail": "Not Found"}


def test_removed_index_routes_are_absent_from_openapi(monkeypatch):
    app = _make_app_no_live_deps(monkeypatch)
    assert REMOVED_INDEX_PATHS.isdisjoint(app.openapi()["paths"])


def test_removed_index_schemas_are_absent_from_openapi(monkeypatch):
    app = _make_app_no_live_deps(monkeypatch)
    assert REMOVED_INDEX_REQUEST_SCHEMAS.isdisjoint(
        app.openapi()["components"]["schemas"]
    )


def test_query_contract_remains_in_openapi(monkeypatch):
    app = _make_app_no_live_deps(monkeypatch)
    query_contract = app.openapi()["paths"]["/query"]
    assert set(query_contract) == {"post"}
    request_schema = query_contract["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert request_schema == {"$ref": "#/components/schemas/UnifiedQueryRequest"}


def test_use_chunk_memory_env_parsing(monkeypatch):
    import importlib
    runtime = importlib.import_module("services.runtime")

    truthy_values = ["1", "true", "True", "TRUE", "yes", "Yes", "YES"]
    for val in truthy_values:
        monkeypatch.setenv("USE_CHUNK_MEMORY", val)
        assert runtime.is_chunk_memory_enabled() is True, f"Expected True for {val!r}"

    falsy_values = ["0", "false", "no", "off", "", "maybe"]
    for val in falsy_values:
        monkeypatch.setenv("USE_CHUNK_MEMORY", val)
        assert runtime.is_chunk_memory_enabled() is False, f"Expected False for {val!r}"

    monkeypatch.delenv("USE_CHUNK_MEMORY", raising=False)
    assert runtime.is_chunk_memory_enabled() is False, "Expected False when unset"


def test_unified_query_forwards_user_id_to_query_service(monkeypatch):
    """user_id from the request payload must reach query_service.query()."""
    app = _make_app_no_live_deps(monkeypatch)
    captured = {}

    def fake_query(**kwargs):
        captured.update(kwargs)
        return {"hits": [], "total": 0, "corpora_queried": [], "degraded": False, "degradation_reasons": []}

    with TestClient(app) as client:
        client.app.state.query_service.query = fake_query
        resp = client.post("/query", json={
            "query": "hello",
            "corpora": ["all"],
            "limit": 5,
            "user_id": "alice",
        })

    assert resp.status_code == 200
    assert captured.get("user_id") == "alice", (
        f"expected user_id='alice' forwarded to query_service.query, got: {captured}"
    )


def test_unified_query_omits_user_id_when_not_provided(monkeypatch):
    """When user_id is absent from the request, None is forwarded."""
    app = _make_app_no_live_deps(monkeypatch)
    captured = {}

    def fake_query(**kwargs):
        captured.update(kwargs)
        return {"hits": [], "total": 0, "corpora_queried": [], "degraded": False, "degradation_reasons": []}

    with TestClient(app) as client:
        client.app.state.query_service.query = fake_query
        resp = client.post("/query", json={"query": "hello"})

    assert resp.status_code == 200
    assert captured.get("user_id") is None, (
        f"expected user_id=None when not provided, got: {captured}"
    )


def test_initialize_memory_propagates_to_indexing_service(monkeypatch: MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config):
            self.config = config

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    assert app.state.indexing_service._memory is None

    config = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {"provider": "openai", "config": {"model": "gpt-5", "api_key": "k"}},
        "embedder": {"provider": "openai", "config": {"api_key": "k"}},
        "history_db_path": "/tmp/history.db",
    }
    server.initialize_memory(app, config=config)

    assert app.state.indexing_service._memory is app.state.memory
    assert isinstance(app.state.indexing_service._memory, FakeMemory)
