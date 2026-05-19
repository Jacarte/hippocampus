from __future__ import annotations

import importlib
import sys
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
    resp = client.post("/index/ingest", json=payload)
    assert resp.status_code == 200
    data = resp.json()
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
    resp = client.post("/index/ingest", json=payload)
    assert resp.status_code == 200
    data = resp.json()
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
    ingest_resp = client.post("/index/ingest", json=ingest_payload)
    assert ingest_resp.status_code == 200
    assert ingest_resp.json()["files_indexed"] == 1

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
