"""HTTP write contracts, durable retries, and compatibility with old clients."""
import sys
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import create_app
from services.mem0_compat import MemoryWriteError


CONFIG = {
    "vector_store": {"provider": "qdrant", "config": {"collection_name": "test"}},
    "llm": {"provider": "ollama", "config": {}},
}


class FakeMemory:
    def __init__(self, config):
        self.config = config
        self.calls = []
        self.error = None
        self.skip = False

    def add(self, *, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error is not None:
            raise self.error
        return {"results": [] if self.skip else [
            {"id": f"memory-{len(self.calls)}", "memory": messages[0]["content"], "event": "ADD"}
        ]}


@pytest.fixture
def client_and_app(monkeypatch, tmp_path):
    monkeypatch.setenv("MEM0_WRITE_DB_PATH", str(tmp_path / "writes.db"))
    app = create_app(memory_factory=FakeMemory, startup_enabled=False)
    with TestClient(app) as client:
        assert client.post("/configure", json=CONFIG).status_code == 200
        yield client, app


def capture(**changes):
    return {
        "messages": [{"role": "user", "content": "We chose Redis for project A."}],
        "user_id": "user-a", "capture_id": "session-a:event-1", **changes,
    }


@pytest.mark.parametrize("infer", [True, False])
def test_infer_reaches_backend_and_provenance_is_preserved(client_and_app, infer):
    client, app = client_and_app
    response = client.post("/memories", json=capture(
        infer=infer, metadata={"source": "pepi", "observed_at": "2026-01-01T12:00:00Z"},
    ))
    assert response.status_code == 200
    assert response.json()["write_status"] == "saved"
    messages, params = app.state.memory.calls[0]
    assert params["infer"] is infer
    assert params["metadata"]["capture_id"] == "session-a:event-1"
    assert params["metadata"]["observed_at"] == "2026-01-01T12:00:00Z"
    assert messages == capture()["messages"]


def test_legacy_client_defaults_to_inference_without_creating_receipt(client_and_app, tmp_path):
    client, app = client_and_app
    payload = capture()
    del payload["capture_id"]
    response = client.post("/memories", json=payload)
    assert response.status_code == 200
    assert response.json()["results"][0]["id"] == "memory-1"
    assert app.state.memory.calls[0][1]["infer"] is True
    assert "capture_id" not in response.json()
    assert not (tmp_path / "writes.db").exists()


def test_completed_capture_replays_after_app_restart(client_and_app):
    client, app = client_and_app
    first = client.post("/memories", json=capture()).json()
    assert client.post("/memories", json=capture()).json() == first
    assert len(app.state.memory.calls) == 1
    restarted = create_app(memory_factory=FakeMemory, startup_enabled=False)
    with TestClient(restarted) as second_client:
        second_client.post("/configure", json=CONFIG)
        replay = second_client.post("/memories", json=capture())
    assert replay.status_code == 200
    assert replay.json() == first
    assert restarted.state.memory.calls == []


def test_capture_acknowledgements_do_not_retain_memory_content(client_and_app, tmp_path):
    client, app = client_and_app
    response = client.post("/memories", json=capture()).json()
    assert response == {
        "results": [{"id": "memory-1", "event": "ADD"}],
        "write_status": "saved", "capture_id": "session-a:event-1",
    }
    with sqlite3.connect(tmp_path / "writes.db") as connection:
        stored = connection.execute("SELECT response_json FROM memory_write_receipts").fetchone()[0]
    assert capture()["messages"][0]["content"] not in stored
    assert capture()["capture_id"] not in stored


def test_deliberate_skip_is_acknowledged_and_replayed(client_and_app):
    client, app = client_and_app
    app.state.memory.skip = True
    first = client.post("/memories", json=capture())
    assert first.status_code == 200
    assert first.json()["write_status"] == "skipped"
    assert first.json()["skip_reason"] == "no_new_memories"
    assert first.json()["results"] == []
    app.state.memory.skip = False
    assert client.post("/memories", json=capture()).json() == first.json()
    assert len(app.state.memory.calls) == 1


def test_capture_id_reuse_with_different_content_is_conflict(client_and_app):
    client, app = client_and_app
    client.post("/memories", json=capture())
    response = client.post("/memories", json=capture(
        messages=[{"role": "user", "content": "Different decision"}],
    ))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "capture_conflict"
    assert len(app.state.memory.calls) == 1


def test_safe_extraction_failure_can_retry_same_capture(client_and_app):
    client, app = client_and_app
    app.state.memory.error = MemoryWriteError("Extraction unavailable", retryable=True)
    response = client.post("/memories", json=capture())
    assert response.status_code == 503
    assert response.json()["detail"]["retryable"] is True
    app.state.memory.error = None
    assert client.post("/memories", json=capture()).json()["write_status"] == "saved"
    assert len(app.state.memory.calls) == 2


def test_uncertain_failure_is_retained_and_never_blindly_retried(client_and_app):
    client, app = client_and_app
    app.state.memory.error = MemoryWriteError("Write outcome unknown")
    response = client.post("/memories", json=capture())
    assert response.status_code == 503
    assert response.json()["detail"]["retryable"] is False
    app.state.memory.error = None
    retry = client.post("/memories", json=capture())
    assert retry.status_code == 409
    assert retry.json()["detail"]["code"] == "capture_pending"
    assert retry.json()["detail"]["state"] == "unknown"
    assert len(app.state.memory.calls) == 1


def test_capture_ids_are_isolated_by_scope_and_backend(client_and_app):
    client, app = client_and_app
    assert client.post("/memories", json=capture()).status_code == 200
    assert client.post("/memories", json=capture(user_id="user-b")).status_code == 200
    assert len(app.state.memory.calls) == 2
    changed = {**CONFIG, "vector_store": {
        "provider": "qdrant", "config": {"collection_name": "other-project"},
    }}
    client.post("/configure", json=changed)
    assert client.post("/memories", json=capture()).status_code == 200
    assert len(app.state.memory.calls) == 1


def test_backend_tuning_and_credentials_do_not_change_receipt_identity(client_and_app):
    client, app = client_and_app
    first = client.post("/memories", json=capture()).json()
    changed = {**CONFIG, "vector_store": {
        "provider": "qdrant", "config": {
            "collection_name": "test", "hnsw": True,
            "embedding_model_dims": 768, "api_key": "rotated-test-key",
        },
    }}
    client.post("/configure", json=changed)
    assert client.post("/memories", json=capture()).json() == first
    assert app.state.memory.calls == []


@pytest.mark.parametrize("capture_id", ["", "   ", "x" * 201])
def test_invalid_capture_id_is_rejected_before_backend(client_and_app, capture_id):
    client, app = client_and_app
    assert client.post("/memories", json=capture(capture_id=capture_id)).status_code == 422
    assert app.state.memory.calls == []
