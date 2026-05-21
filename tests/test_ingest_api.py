from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_app(monkeypatch: MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config):
            self.config = config

    return server.create_app(memory_factory=FakeMemory, startup_enabled=False)


def _ingest_wait(client, payload: dict, timeout: float = 10.0) -> dict:
    resp = client.post("/index/ingest", json=payload)
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/index/jobs/{job_id}").json()
        if job["status"] == "completed":
            return job["result"]
        time.sleep(0.05)
    raise TimeoutError(f"ingest job {job_id} did not complete within {timeout}s")


def test_ingest_single_file_returns_files_indexed(monkeypatch: MonkeyPatch):
    app = _make_app(monkeypatch)
    client = TestClient(app)

    payload = {
        "root": "/client/myproject",
        "files": [
            {"file_path": "src/main.py", "content": "def hello():\n    return 'world'\n"}
        ],
        "generate_summaries": False,
    }
    data = _ingest_wait(client, payload)
    assert data["files_indexed"] == 1
    assert data["root"] == "/client/myproject"
    assert "ingested_at" in data
    assert "errors" in data


def test_ingest_multiple_files(monkeypatch: MonkeyPatch):
    app = _make_app(monkeypatch)
    client = TestClient(app)

    payload = {
        "root": "/client/project",
        "files": [
            {"file_path": "a.py", "content": "x = 1\n"},
            {"file_path": "b.py", "content": "y = 2\n"},
        ],
    }
    data = _ingest_wait(client, payload)
    assert data["files_indexed"] == 2


def test_ingest_then_query_returns_hit(monkeypatch: MonkeyPatch):
    app = _make_app(monkeypatch)
    client = TestClient(app)

    ingest_payload = {
        "root": "/client/querytest",
        "files": [
            {
                "file_path": "utils.py",
                "content": "def compute_hash(value):\n    import hashlib\n    return hashlib.sha256(value.encode()).hexdigest()\n",
            }
        ],
    }
    data = _ingest_wait(client, ingest_payload)
    assert data["files_indexed"] == 1

    query_payload = {
        "query": "compute_hash",
        "corpora": ["file_corpus"],
        "limit": 5,
    }
    query_resp = client.post("/query", json=query_payload)
    assert query_resp.status_code == 200
    hits = query_resp.json()["hits"]
    assert len(hits) >= 1
    paths = [h["path"] for h in hits]
    assert any("utils.py" in p for p in paths)


def test_ingest_empty_files_list_returns_422(monkeypatch: MonkeyPatch):
    app = _make_app(monkeypatch)
    client = TestClient(app)

    payload = {
        "root": "/client/empty",
        "files": [],
    }
    resp = client.post("/index/ingest", json=payload)
    assert resp.status_code == 422


def test_ingest_missing_root_returns_422(monkeypatch: MonkeyPatch):
    app = _make_app(monkeypatch)
    client = TestClient(app)

    payload = {
        "files": [{"file_path": "x.py", "content": "pass"}],
    }
    resp = client.post("/index/ingest", json=payload)
    assert resp.status_code == 422

def test_ingest_with_project_id_namespaces_correctly(monkeypatch: MonkeyPatch):
    app = _make_app(monkeypatch)
    client = TestClient(app)

    client.post("/index/ingest", json={
        "root": "/machine-a/myproject",
        "project_id": "proj-a",
        "files": [{"file_path": "alpha.py", "content": "UNIQUE_ALPHA = True\n"}],
    })

    client.post("/index/ingest", json={
        "root": "/machine-b/otherproject",
        "project_id": "proj-b",
        "files": [{"file_path": "beta.py", "content": "UNIQUE_BETA = True\n"}],
    })

    query_resp = client.post("/query", json={
        "query": "UNIQUE_ALPHA",
        "corpora": ["file_corpus"],
        "limit": 10,
    })
    assert query_resp.status_code == 200
    hits = query_resp.json()["hits"]
    paths = [h["path"] for h in hits]
    assert any("alpha.py" in p for p in paths)
    assert not any("beta.py" in p for p in paths)


def test_ingest_with_generate_summaries_and_memory_stores_summary_fields(
    monkeypatch: MonkeyPatch,
):
    from unittest.mock import MagicMock

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config):
            self.config = config
            self.llm = MagicMock()
            self.llm.generate_response.return_value = "A generated summary"
            self.embedding_model = MagicMock()
            self.embedding_model.embed.return_value = [0.1, 0.2, 0.3]

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    config = {
        "version": "v1.1",
        "vector_store": {"provider": "pgvector", "config": {}},
        "llm": {"provider": "openai", "config": {"model": "gpt-5", "api_key": "k"}},
        "embedder": {"provider": "openai", "config": {"api_key": "k"}},
        "history_db_path": "/tmp/history.db",
    }
    server.initialize_memory(app, config=config)

    client = TestClient(app)
    payload = {
        "root": "/client/proj",
        "files": [
            {"file_path": "src/foo.py", "content": "def greet():\n    return 'hi'\n"}
        ],
        "generate_summaries": True,
    }
    resp = client.post("/index/ingest", json=payload)
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    import time as _time
    deadline = _time.time() + 10.0
    while _time.time() < deadline:
        job = client.get(f"/index/jobs/{job_id}").json()
        if job["status"] == "completed":
            break
        _time.sleep(0.05)
    data = job["result"]
    assert data["files_indexed"] == 1

    chunks_resp = client.post(
        "/index/file",
        json={"file_path": "src/foo.py", "root": "/client/proj"},
    )
    assert chunks_resp.status_code == 200
    chunks = chunks_resp.json()["chunks"]
    assert len(chunks) >= 1
    assert all(c["summary_text"] == "A generated summary" for c in chunks), (
        f"Expected all chunks to have summary_text set, got: {chunks}"
    )
    assert all(c.get("summary_embedding") or c.get("has_summary_embedding") for c in chunks), (
        f"Expected all chunks to have summary_embedding, got: {chunks}"
    )


def test_ingest_with_generate_summaries_without_memory_logs_warning(
    monkeypatch: MonkeyPatch,
    caplog,
):
    import logging

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config):
            self.config = config

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)

    client = TestClient(app)
    payload = {
        "root": "/client/proj2",
        "files": [
            {"file_path": "src/bar.py", "content": "x = 1\n"}
        ],
        "generate_summaries": True,
    }
    with caplog.at_level(logging.WARNING, logger="services.indexing_service"):
        resp = client.post("/index/ingest", json=payload)

    assert resp.status_code == 200
    assert any(
        "generate_summaries=True but memory instance is not configured" in r.message
        for r in caplog.records
    )
