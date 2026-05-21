"""Tests for background job routes: GET /index/jobs, GET /index/jobs/{job_id},
and the recent_errors field in GET /index/status.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from server import create_app


def _make_client():
    mock_memory = MagicMock()
    app = create_app(
        memory_factory=lambda cfg: mock_memory,
        startup_enabled=False,
    )
    return TestClient(app), mock_memory


def test_submit_sync_returns_job_immediately():
    client, _ = _make_client()
    with patch.object(
        client.app.state.indexing_service, "sync", return_value={"synced": 0, "errors": []}
    ):
        response = client.post("/index/sync", json={"root": "/tmp", "generate_summaries": False})
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert data["status"] in ("queued", "running", "completed")


def test_get_job_returns_job_record():
    client, _ = _make_client()
    with patch.object(
        client.app.state.indexing_service, "sync", return_value={"synced": 0, "errors": []}
    ):
        post_resp = client.post("/index/sync", json={"root": "/tmp", "generate_summaries": False})
    job_id = post_resp.json()["job_id"]
    get_resp = client.get(f"/index/jobs/{job_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["job_id"] == job_id


def test_get_job_404_for_unknown():
    client, _ = _make_client()
    resp = client.get("/index/jobs/nonexistent-id")
    assert resp.status_code == 404


def test_list_jobs_returns_list():
    client, _ = _make_client()
    resp = client.get("/index/jobs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_status_includes_recent_errors():
    client, _ = _make_client()
    resp = client.get("/index/status")
    assert resp.status_code == 200
    assert "recent_errors" in resp.json()


def test_completed_job_has_completed_status():
    client, _ = _make_client()
    with patch.object(
        client.app.state.indexing_service, "sync", return_value={"synced": 1, "errors": []}
    ):
        post_resp = client.post("/index/sync", json={"root": "/tmp", "generate_summaries": False})
    job_id = post_resp.json()["job_id"]
    deadline = time.time() + 5.0
    status_resp = None
    while time.time() < deadline:
        status_resp = client.get(f"/index/jobs/{job_id}")
        if status_resp.json()["status"] == "completed":
            break
        time.sleep(0.05)
    assert status_resp is not None
    assert status_resp.json()["status"] == "completed"
    assert status_resp.json()["result"] == {"synced": 1, "errors": []}
