import importlib
import json
import logging
import sys
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
        assert list_response.json()[0]["params"] == {"user_id": "user-1"}

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

    assert fake_memory.get_all_calls == [{"user_id": "user-1"}]
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
            assert params == {"user_id": "user-1", "filters": {"source": "chat"}}
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
