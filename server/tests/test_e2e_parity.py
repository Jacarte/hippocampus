from __future__ import annotations

import importlib
import json
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from pytest import MonkeyPatch


def _make_app(monkeypatch: MonkeyPatch) -> Any:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    server = importlib.reload(importlib.import_module("server"))

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

        def search(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "results": [
                    {
                        "id": "memory-parity",
                        "memory": "HTTP and MCP parity",
                        "score": 0.91,
                        "metadata": {"source": "test"},
                    }
                ]
            }

        def get_all(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)
    app.state.memory = FakeMemory({})
    return app


def _httpx_fake_post(client: TestClient):
    def fake_post(url: str, json: dict | None = None, timeout: int = 30):  # noqa: A002
        response = client.post(urlparse(url).path, json=json)
        mock = MagicMock()
        mock.status_code = response.status_code
        mock.json.return_value = response.json()
        mock.text = response.text
        if response.status_code >= 400:
            import httpx

            mock.raise_for_status.side_effect = httpx.HTTPStatusError(
                "error", request=MagicMock(), response=mock
            )
        return mock

    return fake_post


class TestQueryParity:
    def test_http_and_mcp_all_alias_mean_memory_store_only(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        from services.mcp_bridge import handle_request

        app = _make_app(monkeypatch)
        with TestClient(app) as client:
            memory_response = client.post(
                "/query",
                json={"query": "parity", "corpora": ["memory_store"]},
            ).json()
            all_response = client.post(
                "/query", json={"query": "parity", "corpora": ["all"]}
            ).json()
            monkeypatch.setattr(
                "services.mcp_bridge.httpx.post", _httpx_fake_post(client)
            )
            mcp_response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "mgrep_query",
                        "arguments": {"query": "parity", "corpora": ["all"]},
                    },
                }
            )

        mcp_result = json.loads(mcp_response["result"]["content"][0]["text"])
        assert all_response == memory_response == mcp_result
        assert mcp_result["corpora_queried"] == ["memory_store"]
        assert {hit["corpus"] for hit in mcp_result["hits"]} == {"memory_store"}
