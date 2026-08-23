from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BACKEND_URL: str = os.environ.get("MEM0_SERVER_URL", "http://localhost:8000")
_MAX_DIAGNOSTIC_CHARS = 500

TOOLS: list[dict[str, Any]] = [
    {
        "name": "mgrep_query",
        "description": (
            "Search the memory store using a natural-language or keyword query. "
            "The 'all' compatibility alias also selects only the memory store."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (required).",
                },
                "corpora": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["memory_store", "all"]},
                    "description": (
                        "Memory-store selector. Defaults to the ['all'] "
                        "compatibility alias."
                    ),
                    "default": ["all"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (1-50). Defaults to 10.",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": ["query"],
        },
    },
]


def _call_query(args: dict[str, Any]) -> dict[str, Any]:
    """Forward the MCP memory query allowlist to ``POST /query``.

    ``all`` is forwarded unchanged and the backend resolves it to
    ``memory_store``. Unknown arguments, including removed file filters, are
    ignored. An explicit unsupported corpus remains in ``corpora`` so backend
    validation can return HTTP 422, which :func:`handle_request` translates to
    a JSON-RPC backend error.
    """
    payload: dict[str, Any] = {"query": args["query"]}
    for key in ("corpora", "limit"):
        if key in args:
            payload[key] = args[key]
    resp = httpx.post(f"{BACKEND_URL}/query", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


_TOOL_HANDLERS = {
    "mgrep_query": _call_query,
}


def _ok(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _write(obj: dict[str, Any], out=None) -> None:
    if out is None:
        out = sys.stdout
    out.write(json.dumps(obj) + "\n")
    out.flush()


def handle_request(req: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one MCP JSON-RPC request without reflecting bridge failures.

    Backend HTTP failures expose only their status, while transport and internal
    failures use fixed messages; bounded details are retained in server logs.
    Requests whose method is ``initialized`` and unknown methods without an
    ``id`` remain response-free; other idless calls follow their method-specific
    path.
    """
    method: str = req.get("method", "")
    req_id = req.get("id")

    if method == "initialize":
        return _ok(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mgrep-mcp-bridge", "version": "1.0.0"},
            },
        )

    if method == "initialized":
        return None

    if method == "tools/list":
        return _ok(req_id, {"tools": TOOLS})

    if method == "tools/call":
        params: dict[str, Any] = req.get("params", {})
        tool_name: str = params.get("name", "")
        arguments: dict[str, Any] = params.get("arguments", {})

        handler = _TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return _error(req_id, -32601, f"Unknown tool: {tool_name}")

        try:
            result = handler(arguments)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            logger.warning(
                "Backend HTTP error status=%s detail=%r",
                status_code,
                exc.response.text[:_MAX_DIAGNOSTIC_CHARS],
            )
            return _error(req_id, -32000, f"Backend error {status_code}")
        except httpx.RequestError as exc:
            logger.warning(
                "Backend request failed type=%s detail=%r",
                type(exc).__name__,
                str(exc)[:_MAX_DIAGNOSTIC_CHARS],
            )
            return _error(req_id, -32000, "Backend request failed")
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "MCP bridge error type=%s detail=%r",
                type(exc).__name__,
                str(exc)[:_MAX_DIAGNOSTIC_CHARS],
            )
            return _error(req_id, -32000, "Bridge error")

        return _ok(
            req_id,
            {"content": [{"type": "text", "text": json.dumps(result, default=str)}]},
        )

    if req_id is not None:
        return _error(req_id, -32601, f"Method not found: {method}")

    return None


def run(*, stdin=None, stdout=None) -> None:
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(_error(None, -32700, f"Parse error: {exc}"), stdout)
            continue

        response = handle_request(req)
        if response is not None:
            _write(response, stdout)


if __name__ == "__main__":
    run()
