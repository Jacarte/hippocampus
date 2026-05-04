from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from collections.abc import Callable, Iterator, Mapping
from typing import Any
from uuid import uuid4


LOGGER = logging.getLogger(__name__)
_REQUEST_ID: ContextVar[str | None] = ContextVar("mem0_request_id", default=None)


def assign_request_id(request_id: str | None = None) -> str:
    resolved_request_id = request_id or uuid4().hex
    _ = _REQUEST_ID.set(resolved_request_id)
    return resolved_request_id


def bind_request_id(request_id: str) -> Token[str | None]:
    return _REQUEST_ID.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _REQUEST_ID.reset(token)


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def resolve_request_id(headers: Mapping[str, str] | None = None) -> str:
    header_request_id = None
    if headers is not None:
        header_request_id = headers.get("x-correlation-id") or headers.get(
            "x-request-id"
        )

    normalized_request_id = _normalize_request_id(header_request_id)
    return assign_request_id(normalized_request_id)


def trace_backend_operation(operation: str, **fields: Any) -> None:
    _trace_event(logging.INFO, operation, **fields)


def trace_backend_error(operation: str, exc: Exception, **fields: Any) -> None:
    _trace_event(logging.ERROR, operation, error=str(exc), **fields)
    LOGGER.exception("%s failed", operation)


def trace_backend_request_start(method: str, path: str) -> None:
    trace_backend_operation("request.started", method=method, path=path)


def trace_backend_request_complete(
    method: str,
    path: str,
    *,
    status_code: int,
    latency_ms: float,
) -> None:
    trace_backend_operation(
        "request.completed",
        method=method,
        path=path,
        status_code=status_code,
        latency_ms=round(latency_ms, 3),
    )


def trace_retrieval_diagnostics(
    operation: str,
    *,
    diagnostics: dict[str, Any],
) -> None:
    trace_backend_operation(operation, retrieval=diagnostics)


@contextmanager
def stage_timer() -> Iterator[Callable[[], float]]:
    started_at = time.perf_counter()

    def elapsed_ms() -> float:
        return round((time.perf_counter() - started_at) * 1000, 3)

    yield elapsed_ms


def _trace_event(level: int, event: str, **fields: Any) -> None:
    if not LOGGER.isEnabledFor(level):
        return

    payload = {"event": event, "request_id": current_request_id(), **fields}
    LOGGER.log(level, "%s", json.dumps(_sanitize(payload), sort_keys=True))


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(nested_value)
            for key, nested_value in sorted(value.items())
            if nested_value is not None
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _normalize_request_id(request_id: str | None) -> str | None:
    if request_id is None:
        return None
    stripped_request_id = request_id.strip()
    if not stripped_request_id or len(stripped_request_id) > 128:
        return None
    if all(
        character.isalnum() or character in "-_." for character in stripped_request_id
    ):
        return stripped_request_id
    return None
