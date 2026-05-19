from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_FIXTURES_ROOT = str(Path(__file__).parent / "fixtures" / "mgrep_repo")


def _make_app(monkeypatch: MonkeyPatch) -> Any:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    server = importlib.import_module("server")
    server = importlib.reload(server)

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

    return server.create_app(memory_factory=FakeMemory, startup_enabled=False)


def _requests_fake_post(client: TestClient):
    def fake_post(url: str, json: dict, timeout: int = 30):  # noqa: A002
        path = urlparse(url).path
        resp = client.post(path, json=json)
        mock = MagicMock()
        mock.status_code = resp.status_code
        mock.json.return_value = resp.json()
        mock.text = resp.text
        if resp.status_code >= 400:
            from requests.exceptions import HTTPError
            mock.raise_for_status.side_effect = HTTPError(response=mock)
        else:
            mock.raise_for_status.return_value = None
        return mock
    return fake_post


def _requests_fake_get(client: TestClient):
    def fake_get(url: str, timeout: int = 30):
        path = urlparse(url).path
        resp = client.get(path)
        mock = MagicMock()
        mock.status_code = resp.status_code
        mock.json.return_value = resp.json()
        mock.text = resp.text
        if resp.status_code >= 400:
            from requests.exceptions import HTTPError
            mock.raise_for_status.side_effect = HTTPError(response=mock)
        else:
            mock.raise_for_status.return_value = None
        return mock
    return fake_get


def _httpx_fake_post(client: TestClient):
    def fake_post(url: str, json: dict = None, timeout: int = 30):  # noqa: A002
        path = urlparse(url).path
        resp = client.post(path, json=json)
        mock = MagicMock()
        mock.status_code = resp.status_code
        try:
            mock.json.return_value = resp.json()
        except Exception:  # noqa: BLE001
            mock.json.return_value = {}
        mock.text = resp.text
        if resp.status_code >= 400:
            import httpx
            mock.raise_for_status.side_effect = httpx.HTTPStatusError(
                "error", request=MagicMock(), response=mock
            )
        else:
            mock.raise_for_status.return_value = None
        return mock
    return fake_post


def _httpx_fake_get(client: TestClient):
    def fake_get(url: str, timeout: int = 30):
        path = urlparse(url).path
        resp = client.get(path)
        mock = MagicMock()
        mock.status_code = resp.status_code
        try:
            mock.json.return_value = resp.json()
        except Exception:  # noqa: BLE001
            mock.json.return_value = {}
        mock.text = resp.text
        if resp.status_code >= 400:
            import httpx
            mock.raise_for_status.side_effect = httpx.HTTPStatusError(
                "error", request=MagicMock(), response=mock
            )
        else:
            mock.raise_for_status.return_value = None
        return mock
    return fake_get


def _mcp_req(method: str, req_id: int = 1, params: dict | None = None) -> dict:
    r: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        r["params"] = params
    return r


class TestCapabilitiesStatusParity:
    def test_http_capabilities_returns_expected_shape(self, monkeypatch: MonkeyPatch) -> None:
        app = _make_app(monkeypatch)
        with TestClient(app) as client:
            resp = client.get("/query/capabilities")
        assert resp.status_code == 200
        body = resp.json()
        assert "memory_store" in body
        assert "file_corpus" in body
        assert "lexical" in body["memory_store"]
        assert "semantic" in body["memory_store"]
        assert "lexical" in body["file_corpus"]

    def test_cli_status_returns_expected_shape(self, monkeypatch: MonkeyPatch) -> None:
        from mgrep_cli import app as cli_app

        server_app = _make_app(monkeypatch)
        with TestClient(server_app) as client:
            runner = CliRunner()
            with monkeypatch.context() as m:
                m.setattr("mgrep_cli.requests.get", _requests_fake_get(client))
                result = runner.invoke(cli_app, ["status"])

        assert result.exit_code == 0
        body = json.loads(result.output)
        assert "total_files" in body
        assert "total_chunks" in body
        assert "roots" in body

    def test_both_succeed_against_same_app(self, monkeypatch: MonkeyPatch) -> None:
        from mgrep_cli import app as cli_app

        server_app = _make_app(monkeypatch)
        with TestClient(server_app) as client:
            http_resp = client.get("/query/capabilities")
            runner = CliRunner()
            with monkeypatch.context() as m:
                m.setattr("mgrep_cli.requests.get", _requests_fake_get(client))
                cli_result = runner.invoke(cli_app, ["status"])

        assert http_resp.status_code == 200
        assert cli_result.exit_code == 0
        assert isinstance(http_resp.json(), dict)
        assert isinstance(json.loads(cli_result.output), dict)


class TestSyncParity:
    def test_http_sync_indexes_fixture_root(self, monkeypatch: MonkeyPatch) -> None:
        app = _make_app(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/index/sync", json={"root": _FIXTURES_ROOT})
        assert resp.status_code == 200
        body = resp.json()
        assert body["root"] == _FIXTURES_ROOT
        assert body["files_indexed"] >= 2
        assert body["chunks_indexed"] >= 2

    def test_cli_sync_indexes_fixture_root(self, monkeypatch: MonkeyPatch) -> None:
        from mgrep_cli import app as cli_app

        server_app = _make_app(monkeypatch)
        with TestClient(server_app) as client:
            runner = CliRunner()
            with monkeypatch.context() as m:
                m.setattr("mgrep_cli.requests.post", _requests_fake_post(client))
                result = runner.invoke(cli_app, ["sync", _FIXTURES_ROOT])

        assert result.exit_code == 0
        body = json.loads(result.output)
        assert body["root"] == _FIXTURES_ROOT
        assert body["files_indexed"] >= 2
        assert body["chunks_indexed"] >= 2

    def test_http_and_cli_sync_produce_equivalent_counts(self, monkeypatch: MonkeyPatch) -> None:
        from mgrep_cli import app as cli_app

        http_app = _make_app(monkeypatch)
        with TestClient(http_app) as http_client:
            http_body = http_client.post("/index/sync", json={"root": _FIXTURES_ROOT}).json()

        cli_server_app = _make_app(monkeypatch)
        with TestClient(cli_server_app) as cli_client:
            runner = CliRunner()
            with monkeypatch.context() as m:
                m.setattr("mgrep_cli.requests.post", _requests_fake_post(cli_client))
                cli_result = runner.invoke(cli_app, ["sync", _FIXTURES_ROOT])
        cli_body = json.loads(cli_result.output)

        assert http_body["files_indexed"] == cli_body["files_indexed"]
        assert http_body["chunks_indexed"] == cli_body["chunks_indexed"]


class TestStatusParity:
    def test_mcp_status_and_http_status_have_same_keys(self, monkeypatch: MonkeyPatch) -> None:
        from services.mcp_bridge import handle_request

        app = _make_app(monkeypatch)
        with TestClient(app) as client:
            http_body = client.get("/index/status").json()

            with monkeypatch.context() as m:
                m.setattr("services.mcp_bridge.httpx.get", _httpx_fake_get(client))
                mcp_resp = handle_request(
                    _mcp_req("tools/call", params={"name": "mgrep_status", "arguments": {}})
                )

        mcp_body = json.loads(mcp_resp["result"]["content"][0]["text"])

        for key in ("roots", "total_files", "total_chunks"):
            assert key in http_body, f"HTTP /index/status missing key: {key}"
            assert key in mcp_body, f"MCP mgrep_status missing key: {key}"

    def test_mcp_status_and_http_status_agree_after_sync(self, monkeypatch: MonkeyPatch) -> None:
        from services.mcp_bridge import handle_request

        app = _make_app(monkeypatch)
        with TestClient(app) as client:
            client.post("/index/sync", json={"root": _FIXTURES_ROOT})
            http_body = client.get("/index/status").json()

            with monkeypatch.context() as m:
                m.setattr("services.mcp_bridge.httpx.get", _httpx_fake_get(client))
                mcp_resp = handle_request(
                    _mcp_req("tools/call", params={"name": "mgrep_status", "arguments": {}})
                )

        mcp_body = json.loads(mcp_resp["result"]["content"][0]["text"])

        assert mcp_body["total_files"] == http_body["total_files"]
        assert mcp_body["total_chunks"] == http_body["total_chunks"]
        assert mcp_body["total_files"] >= 2
        assert any(r.get("root_path") == _FIXTURES_ROOT for r in http_body["roots"])
        assert any(r.get("root_path") == _FIXTURES_ROOT for r in mcp_body["roots"])


class TestQueryParity:
    _QUERY_TERM = "count_tokens"

    def test_http_and_mcp_query_return_same_corpus_labels(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        from services.mcp_bridge import handle_request

        app = _make_app(monkeypatch)
        with TestClient(app) as client:
            client.post("/index/sync", json={"root": _FIXTURES_ROOT})

            http_hits = client.post(
                "/query",
                json={"query": self._QUERY_TERM, "corpora": ["file_corpus"]},
            ).json()["hits"]

            with monkeypatch.context() as m:
                m.setattr("services.mcp_bridge.httpx.post", _httpx_fake_post(client))
                mcp_resp = handle_request(
                    _mcp_req(
                        "tools/call",
                        params={
                            "name": "mgrep_query",
                            "arguments": {"query": self._QUERY_TERM, "corpora": ["file_corpus"]},
                        },
                    )
                )

        mcp_hits = json.loads(mcp_resp["result"]["content"][0]["text"])["hits"]

        assert len(http_hits) >= 1
        assert len(mcp_hits) >= 1
        assert {h["corpus"] for h in http_hits} == {h["corpus"] for h in mcp_hits}

    def test_http_and_mcp_query_hits_have_required_provenance_fields(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        from services.mcp_bridge import handle_request

        app = _make_app(monkeypatch)
        with TestClient(app) as client:
            client.post("/index/sync", json={"root": _FIXTURES_ROOT})

            http_hits = client.post(
                "/query",
                json={"query": self._QUERY_TERM, "corpora": ["file_corpus"]},
            ).json()["hits"]

            with monkeypatch.context() as m:
                m.setattr("services.mcp_bridge.httpx.post", _httpx_fake_post(client))
                mcp_resp = handle_request(
                    _mcp_req(
                        "tools/call",
                        params={
                            "name": "mgrep_query",
                            "arguments": {"query": self._QUERY_TERM, "corpora": ["file_corpus"]},
                        },
                    )
                )

        mcp_hits = json.loads(mcp_resp["result"]["content"][0]["text"])["hits"]

        for label, hits in [("HTTP", http_hits), ("MCP", mcp_hits)]:
            assert hits, f"{label} returned no hits"
            for hit in hits:
                assert "path" in hit, f"{label} hit missing 'path'"
                assert "snippet" in hit, f"{label} hit missing 'snippet'"
                assert "score" in hit, f"{label} hit missing 'score'"
                assert "corpus" in hit, f"{label} hit missing 'corpus'"

    def test_http_and_mcp_query_agree_on_top_hit_path(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        from services.mcp_bridge import handle_request

        app = _make_app(monkeypatch)
        with TestClient(app) as client:
            client.post("/index/sync", json={"root": _FIXTURES_ROOT})

            http_paths = {
                h["path"]
                for h in client.post(
                    "/query",
                    json={"query": self._QUERY_TERM, "corpora": ["file_corpus"]},
                ).json()["hits"]
            }

            with monkeypatch.context() as m:
                m.setattr("services.mcp_bridge.httpx.post", _httpx_fake_post(client))
                mcp_resp = handle_request(
                    _mcp_req(
                        "tools/call",
                        params={
                            "name": "mgrep_query",
                            "arguments": {"query": self._QUERY_TERM, "corpora": ["file_corpus"]},
                        },
                    )
                )

        mcp_paths = {
            h["path"]
            for h in json.loads(mcp_resp["result"]["content"][0]["text"])["hits"]
        }

        overlap = http_paths & mcp_paths
        assert overlap, (
            f"HTTP and MCP query returned no overlapping paths.\n"
            f"HTTP paths: {http_paths}\n"
            f"MCP paths:  {mcp_paths}"
        )
