from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from services.mcp_bridge import TOOLS, handle_request, run


def _req(method: str, req_id: int = 1, params: dict | None = None) -> dict:
    r: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        r["params"] = params
    return r


class TestInitialize:
    def test_returns_server_info(self):
        resp = handle_request(_req("initialize"))
        assert resp["result"]["serverInfo"]["name"] == "mgrep-mcp-bridge"
        assert "protocolVersion" in resp["result"]
        assert "capabilities" in resp["result"]

    def test_id_matches(self):
        resp = handle_request(_req("initialize", req_id=42))
        assert resp["id"] == 42


class TestInitialized:
    def test_notification_returns_none(self):
        req = {"jsonrpc": "2.0", "method": "initialized"}
        assert handle_request(req) is None


class TestToolsList:
    def test_returns_query_tool_only(self):
        resp = handle_request(_req("tools/list"))
        tools = resp["result"]["tools"]
        assert len(tools) == 1

    def test_tool_names(self):
        resp = handle_request(_req("tools/list"))
        names = {t["name"] for t in resp["result"]["tools"]}
        assert names == {"mgrep_query"}

    def test_each_tool_has_input_schema(self):
        resp = handle_request(_req("tools/list"))
        for tool in resp["result"]["tools"]:
            schema = tool["inputSchema"]
            assert schema["type"] == "object"
            assert "properties" in schema
            assert "required" in schema

    def test_mgrep_query_schema_requires_query(self):
        query_tool = next(t for t in TOOLS if t["name"] == "mgrep_query")
        assert "query" in query_tool["inputSchema"]["required"]

    def test_removed_index_tools_are_not_exposed(self):
        names = {tool["name"] for tool in TOOLS}
        assert names.isdisjoint({"mgrep_sync", "mgrep_status", "mgrep_reset"})


class TestToolsCall:
    def _mock_response(self, data: dict, status: int = 200):
        mock = MagicMock()
        mock.status_code = status
        mock.json.return_value = data
        mock.raise_for_status.return_value = None
        return mock

    def test_mgrep_query_forwards_to_backend(self):
        backend_data = {"hits": [], "total": 0, "corpora_queried": ["file_corpus"], "degraded": False, "degradation_reasons": []}
        with patch("services.mcp_bridge.httpx.post", return_value=self._mock_response(backend_data)) as mock_post:
            resp = handle_request(_req("tools/call", params={"name": "mgrep_query", "arguments": {"query": "foo"}}))

        assert resp["result"]["content"][0]["type"] == "text"
        result_data = json.loads(resp["result"]["content"][0]["text"])
        assert result_data["total"] == 0
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "query" in call_kwargs.kwargs["json"] or "query" in call_kwargs[1]["json"]

    @pytest.mark.parametrize("tool_name", ["mgrep_sync", "mgrep_status", "mgrep_reset"])
    def test_removed_index_tool_calls_return_unknown_tool(self, tool_name):
        resp = handle_request(
            _req("tools/call", params={"name": tool_name, "arguments": {}})
        )
        assert resp["error"] == {
            "code": -32601,
            "message": f"Unknown tool: {tool_name}",
        }

    def test_unknown_tool_returns_error(self):
        resp = handle_request(_req("tools/call", params={"name": "unknown_tool", "arguments": {}}))
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_http_error_returns_rpc_error(self):
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        exc = httpx.HTTPStatusError("err", request=MagicMock(), response=mock_resp)

        with patch("services.mcp_bridge.httpx.post", side_effect=exc):
            resp = handle_request(_req("tools/call", params={"name": "mgrep_query", "arguments": {"query": "x"}}))

        assert "error" in resp
        assert resp["error"]["code"] == -32000


class TestUnknownMethod:
    def test_returns_method_not_found(self):
        resp = handle_request(_req("nonexistent/method"))
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_notification_with_unknown_method_returns_none(self):
        req = {"jsonrpc": "2.0", "method": "some/notification"}
        assert handle_request(req) is None


class TestRunLoop:
    def test_tools_list_via_run(self):
        line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
        stdin = io.StringIO(line)
        stdout = io.StringIO()
        run(stdin=stdin, stdout=stdout)
        output = stdout.getvalue().strip()
        resp = json.loads(output)
        assert len(resp["result"]["tools"]) == 1

    def test_parse_error_returns_error_response(self):
        stdin = io.StringIO("not valid json\n")
        stdout = io.StringIO()
        run(stdin=stdin, stdout=stdout)
        output = stdout.getvalue().strip()
        resp = json.loads(output)
        assert "error" in resp
        assert resp["error"]["code"] == -32700

    def test_empty_lines_skipped(self):
        valid = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
        stdin = io.StringIO("\n\n" + valid + "\n")
        stdout = io.StringIO()
        run(stdin=stdin, stdout=stdout)
        lines = [l for l in stdout.getvalue().splitlines() if l.strip()]
        assert len(lines) == 1
