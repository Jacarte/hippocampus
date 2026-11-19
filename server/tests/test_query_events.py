"""Tests for the structured stdout query/memory-hit events.

Covers the pure helpers in ``services.query_events`` and the wiring into
``/search``, ``/retrieve`` and ``/query``. No live dependencies are used.
"""
from __future__ import annotations

import importlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pytest import CaptureFixture, MonkeyPatch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import query_events

_CONFIG: dict[str, Any] = {
    "version": "v1.1",
    "vector_store": {"provider": "pgvector", "config": {}},
    "llm": {"provider": "openai", "config": {"model": "gpt-5", "api_key": "k"}},
    "embedder": {"provider": "openai", "config": {"api_key": "k"}},
    "history_db_path": "/tmp/history.db",
}

_PROJECT_HASH = "291b3324279295210e232d18"
_PROJECT_USER = f"default-user:project:{_PROJECT_HASH}"

_QUERY_EVENT_KEYS = {
    "event", "ts", "request_id", "endpoint", "query", "query_truncated",
    "filters", "scopes", "limit", "user_id", "agent_id", "run_id", "project",
    "hits", "lexical", "semantic", "rerank", "degraded", "degradation_reasons",
    "latency_ms",
}
# ``/query`` alone also reports the pre-threshold result count.
_QUERY_ENDPOINT_EVENT_KEYS = _QUERY_EVENT_KEYS | {"candidates"}
_MEMORY_HIT_KEYS = {
    "event", "ts", "request_id", "endpoint", "memory_id", "rank", "score",
    "user_id", "agent_id", "project",
}

_SECRET_TEXT = "ultra-secret-memory-body"


def _records(count: int = 2) -> dict[str, dict[str, Any]]:
    return {
        f"mem-{i}": {
            "id": f"mem-{i}",
            "memory": f"{_SECRET_TEXT} canonical retrieve endpoint number {i}",
            "metadata": {"source": "chat", "scope": "project", "type": "decision"},
            "updated_at": "2026-05-04T12:00:00Z",
        }
        for i in range(1, count + 1)
    }


def _build_client(
    monkeypatch: MonkeyPatch,
    *,
    records: dict[str, dict[str, Any]] | None = None,
    semantic_error: bool = False,
    lexical_error: bool = False,
    search_calls: list[dict[str, Any]] | None = None,
) -> TestClient:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MEM0_RETRIEVAL_DECAY_ENABLED", raising=False)
    importlib.reload(importlib.import_module("api_models"))
    server = importlib.reload(importlib.import_module("server"))
    stored = _records() if records is None else records

    class FakeMemory:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

        def get_all(self, **_: Any) -> list[dict[str, Any]]:
            if lexical_error:
                raise RuntimeError("lexical offline")
            return list(stored.values())

        def search(self, *, query: str, **params: Any) -> dict[str, Any]:
            if search_calls is not None:
                search_calls.append({"query": query, **params})
            if semantic_error:
                raise RuntimeError("semantic offline")
            return {
                "results": [
                    {**record, "score": round(0.9 - 0.01 * index, 2)}
                    for index, record in enumerate(stored.values())
                ]
            }

    app = server.create_app(memory_factory=FakeMemory, startup_enabled=False)
    client = TestClient(app)
    client.__enter__()
    assert client.post("/configure", json=_CONFIG).status_code == 200
    return client


@pytest.fixture
def make_client(monkeypatch: MonkeyPatch):
    clients: list[TestClient] = []

    def factory(**kwargs: Any) -> TestClient:
        client = _build_client(monkeypatch, **kwargs)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


def _events(capsys: CaptureFixture[str]) -> list[dict[str, Any]]:
    """Parse every stdout line as JSON; fail if any line is not an event."""
    out = capsys.readouterr().out
    events = []
    for line in out.splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events


def _by_kind(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [event for event in events if event["event"] == kind]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_derive_project_from_user_id_suffix() -> None:
    assert query_events.derive_project(_PROJECT_USER) == _PROJECT_HASH
    assert query_events.derive_project(f"a:b:project:abc123") == "abc123"


def test_derive_project_explicit_wins() -> None:
    assert query_events.derive_project(_PROJECT_USER, "explicit") == "explicit"


def test_derive_project_keeps_readable_ids_and_truncates_to_200_chars() -> None:
    readable = "git:github.com/org/repo"
    assert query_events.derive_project(_PROJECT_USER, readable) == readable
    assert query_events.derive_project(None, "explicit:name") == "explicit:name"

    long_project = "p" * 500
    assert query_events.derive_project(_PROJECT_USER, long_project) == "p" * 200
    assert query_events.derive_project(f"alice:project:{'a' * 300}") == "a" * 200


@pytest.mark.parametrize(
    "user_id",
    [None, "", "alice", "alice:project:", "alice:project:XYZ", "alice:project:abc:x"],
)
def test_derive_project_returns_empty_when_no_suffix(user_id: str | None) -> None:
    assert query_events.derive_project(user_id) == ""
    assert query_events.derive_project(user_id, "") == ""


def test_extract_memory_hits_normalizes_envelopes() -> None:
    envelope = {"results": [{"id": "a", "score": 0.5}, {"id": "b"}, "junk", {"x": 1}]}
    assert query_events.extract_memory_hits(envelope) == [("a", 0.5), ("b", None)]
    assert query_events.extract_memory_hits([{"id": 7, "_retrieval": {"score": 0.25}}]) == [
        ("7", 0.25)
    ]
    assert query_events.extract_memory_hits({"hits": [{"memory_id": "q", "score": 1}]}) == [
        ("q", 1.0)
    ]
    assert query_events.extract_memory_hits({"results": "nope"}) == []
    assert query_events.extract_memory_hits(None) == []
    assert query_events.extract_memory_hits([{"id": "n", "score": float("nan")}]) == [
        ("n", None)
    ]


def test_summarize_retrieval_without_diagnostics_is_all_null() -> None:
    summary = query_events.summarize_retrieval({"results": []})
    assert summary == {
        "lexical": None,
        "semantic": None,
        "rerank": None,
        "degraded": False,
        "degradation_reasons": [],
    }


def test_events_logger_setup_is_idempotent_and_isolated() -> None:
    logger = logging.getLogger("mem0.events")
    before = len(logger.handlers)
    query_events.setup_events_logger()
    query_events.setup_events_logger()
    assert len(logger.handlers) == before == 1
    assert logger.propagate is False
    assert logger.level == logging.INFO


def test_emit_query_event_truncates_long_query_and_stays_single_line(
    capsys: CaptureFixture[str],
) -> None:
    query = "x" * 499 + "\n" + "y" * 100
    query_events.emit_query_event(
        endpoint="search",
        query=query,
        user_id="u",
        agent_id=None,
        run_id=None,
        project=None,
        hits=0,
    )
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    event = json.loads(out)
    assert len(event["query"]) == 500
    assert event["query_truncated"] is True
    assert event["agent_id"] == "" and event["run_id"] == "" and event["project"] == ""

    query_events.emit_query_event(
        endpoint="search",
        query="z" * 500,
        user_id="u",
        agent_id=None,
        run_id=None,
        project=None,
        hits=0,
    )
    event = json.loads(capsys.readouterr().out)
    assert len(event["query"]) == 500
    assert event["query_truncated"] is False


def test_emit_helpers_never_raise_even_if_writing_fails(
    monkeypatch: MonkeyPatch,
) -> None:
    def boom(_: dict[str, Any]) -> None:
        raise RuntimeError("stdout closed")

    monkeypatch.setattr(query_events, "_write", boom)
    query_events.emit_query_event(
        endpoint="query", query="q", user_id=None, agent_id=None,
        run_id=None, project=None, hits=0,
    )
    query_events.emit_memory_hits(
        endpoint="query", hits=[("a", 0.1)], user_id=None, agent_id=None, project=None
    )


def test_emit_memory_hits_caps_at_fifty_and_ranks_from_one(
    capsys: CaptureFixture[str],
) -> None:
    hits = [(f"m{i}", 0.5) for i in range(80)]
    query_events.emit_memory_hits(
        endpoint="retrieve", hits=hits, user_id="u", agent_id=None, project="p"
    )
    events = _events(capsys)
    assert len(events) == 50
    assert [event["rank"] for event in events] == list(range(1, 51))
    assert set(events[0]) == _MEMORY_HIT_KEYS


# ---------------------------------------------------------------------------
# /search
# ---------------------------------------------------------------------------


def test_search_emits_query_event_and_memory_hits(make_client, capsys) -> None:
    client = make_client()
    capsys.readouterr()
    response = client.post(
        "/search",
        headers={"X-Correlation-ID": "corr-1"},
        json={
            "query": "canonical retrieve endpoint",
            "user_id": _PROJECT_USER,
            "agent_id": "agent-1",
            "run_id": "run-1",
            "filters": {"source": "chat"},
        },
    )
    assert response.status_code == 200
    events = _events(capsys)

    query_event_list = _by_kind(events, "query_event")
    assert len(query_event_list) == 1
    event = query_event_list[0]
    assert set(event) == _QUERY_EVENT_KEYS
    assert event["endpoint"] == "search"
    assert event["request_id"] == "corr-1"
    assert event["query"] == "canonical retrieve endpoint"
    assert event["query_truncated"] is False
    assert event["filters"] == {"source": "chat"}
    assert event["scopes"] is None
    assert event["limit"] is None
    assert event["user_id"] == _PROJECT_USER
    assert event["agent_id"] == "agent-1"
    assert event["run_id"] == "run-1"
    assert event["project"] == _PROJECT_HASH
    assert event["hits"] == 2
    assert event["lexical"] == 2
    assert event["semantic"] == 2
    assert event["rerank"] is None
    assert event["degraded"] is False
    assert event["degradation_reasons"] == []
    assert isinstance(event["latency_ms"], (int, float))
    assert event["ts"].endswith("+00:00")

    hit_events = _by_kind(events, "memory_hit")
    assert [hit["rank"] for hit in hit_events] == [1, 2]
    assert [hit["memory_id"] for hit in hit_events] == ["mem-1", "mem-2"]
    assert [hit["score"] for hit in hit_events] == [0.9, 0.89]
    for hit in hit_events:
        assert set(hit) == _MEMORY_HIT_KEYS
        assert hit["endpoint"] == "search"
        assert hit["request_id"] == "corr-1"
        assert hit["project"] == _PROJECT_HASH
        assert hit["agent_id"] == "agent-1"


def test_search_zero_hits(make_client, capsys) -> None:
    client = make_client(records={})
    capsys.readouterr()
    response = client.post("/search", json={"query": "nothing", "user_id": "alice"})
    assert response.status_code == 200
    events = _events(capsys)
    assert _by_kind(events, "memory_hit") == []
    (event,) = _by_kind(events, "query_event")
    assert event["hits"] == 0
    assert event["lexical"] == 0
    assert event["semantic"] == 0
    assert event["project"] == ""
    assert event["agent_id"] == "" and event["run_id"] == ""


def test_search_degraded_when_lexical_stage_fails(make_client, capsys) -> None:
    client = make_client(lexical_error=True)
    capsys.readouterr()
    response = client.post("/search", json={"query": "canonical", "user_id": "alice"})
    assert response.status_code == 200
    (event,) = _by_kind(_events(capsys), "query_event")
    assert event["degraded"] is True
    assert event["degradation_reasons"] == ["lexical_unavailable"]
    assert event["lexical"] == 0
    assert event["semantic"] == 2
    assert event["hits"] == 2


def test_search_explicit_project_wins_and_is_not_forwarded_to_mem0(
    make_client, capsys
) -> None:
    calls: list[dict[str, Any]] = []
    client = make_client(search_calls=calls)
    capsys.readouterr()
    response = client.post(
        "/search",
        json={"query": "canonical", "user_id": _PROJECT_USER, "project": "feedbeef"},
    )
    assert response.status_code == 200
    events = _events(capsys)
    assert {e["project"] for e in events} == {"feedbeef"}
    assert calls
    assert "feedbeef" not in json.dumps(calls)
    assert all("project" not in call["filters"] for call in calls)
    assert "feedbeef" not in json.dumps(response.json())


def test_search_project_over_200_chars_is_truncated_not_rejected(
    make_client, capsys
) -> None:
    client = make_client()
    capsys.readouterr()
    response = client.post(
        "/search",
        json={"query": "canonical", "user_id": "alice", "project": "g" * 500},
    )
    assert response.status_code == 200
    events = _events(capsys)
    assert {e["project"] for e in events} == {"g" * 200}


def test_search_query_over_500_chars_is_truncated(make_client, capsys) -> None:
    client = make_client()
    capsys.readouterr()
    response = client.post("/search", json={"query": "q" * 800, "user_id": "alice"})
    assert response.status_code == 200
    (event,) = _by_kind(_events(capsys), "query_event")
    assert len(event["query"]) == 500
    assert event["query_truncated"] is True


# ---------------------------------------------------------------------------
# /retrieve
# ---------------------------------------------------------------------------


def test_retrieve_emits_stage_counts_scopes_and_limit(make_client, capsys) -> None:
    client = make_client()
    capsys.readouterr()
    response = client.post(
        "/retrieve",
        headers={"X-Correlation-ID": "corr-r"},
        json={
            "query": "canonical retrieve endpoint",
            "scopes": ["project"],
            "user_id": _PROJECT_USER,
            "limit": 5,
            "filters": {"source": "chat"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    events = _events(capsys)

    (event,) = _by_kind(events, "query_event")
    assert set(event) == _QUERY_EVENT_KEYS
    assert event["endpoint"] == "retrieve"
    assert event["request_id"] == "corr-r" == body["request_id"]
    assert event["scopes"] == ["project"]
    assert event["limit"] == 5
    assert event["filters"] == {"source": "chat"}
    assert event["lexical"] == 2
    assert event["semantic"] == 2
    assert event["rerank"] == 2
    assert event["degraded"] is False
    assert event["degradation_reasons"] == []
    assert event["hits"] == len(body["results"]) == 2
    assert event["project"] == _PROJECT_HASH

    hit_events = _by_kind(events, "memory_hit")
    assert [hit["memory_id"] for hit in hit_events] == [
        result["id"] for result in body["results"]
    ]
    assert [hit["rank"] for hit in hit_events] == [1, 2]
    assert all(isinstance(hit["score"], float) for hit in hit_events)


def test_retrieve_zero_hits(make_client, capsys) -> None:
    client = make_client(records={})
    capsys.readouterr()
    response = client.post(
        "/retrieve",
        json={"query": "nothing", "scopes": ["project"], "user_id": "alice"},
    )
    assert response.status_code == 200
    events = _events(capsys)
    assert _by_kind(events, "memory_hit") == []
    (event,) = _by_kind(events, "query_event")
    assert event["hits"] == 0
    assert event["lexical"] == 0
    assert event["semantic"] == 0
    assert event["degraded"] is False


def test_retrieve_degraded_when_semantic_stage_fails(make_client, capsys) -> None:
    client = make_client(semantic_error=True)
    capsys.readouterr()
    response = client.post(
        "/retrieve",
        json={
            "query": "canonical retrieve endpoint",
            "scopes": ["project"],
            "user_id": "alice",
        },
    )
    assert response.status_code == 200
    body = response.json()
    (event,) = _by_kind(_events(capsys), "query_event")
    assert event["degraded"] is True
    assert event["degradation_reasons"] == body["degradation_reasons"]
    assert event["degradation_reasons"] == ["semantic_unavailable", "rerank_skipped"]
    assert event["semantic"] == 0
    assert event["lexical"] == 2
    assert event["rerank"] is None
    assert event["hits"] == len(body["results"])


def test_retrieve_explicit_project_wins(make_client, capsys) -> None:
    client = make_client()
    capsys.readouterr()
    response = client.post(
        "/retrieve",
        json={
            "query": "canonical",
            "scopes": ["project"],
            "user_id": _PROJECT_USER,
            "project": "abc123",
        },
    )
    assert response.status_code == 200
    assert {e["project"] for e in _events(capsys)} == {"abc123"}


# ---------------------------------------------------------------------------
# /query
# ---------------------------------------------------------------------------


def test_query_emits_event_and_hits(make_client, capsys) -> None:
    client = make_client()
    capsys.readouterr()
    response = client.post(
        "/query",
        headers={"X-Correlation-ID": "corr-q"},
        json={
            "query": "canonical retrieve endpoint",
            "limit": 5,
            "user_id": _PROJECT_USER,
            "min_score_memory": 0.5,
        },
    )
    assert response.status_code == 200
    body = response.json()
    events = _events(capsys)

    (event,) = _by_kind(events, "query_event")
    assert set(event) == _QUERY_ENDPOINT_EVENT_KEYS
    assert event["endpoint"] == "query"
    assert event["request_id"] == "corr-q"
    assert event["limit"] == 5
    assert event["filters"] is None
    assert event["scopes"] is None
    # Stage counts come from the underlying retrieval response.
    assert event["lexical"] == 2
    assert event["semantic"] == 2
    assert event["rerank"] is None
    assert event["candidates"] == body["total"] == 2
    assert event["agent_id"] == "" and event["run_id"] == ""
    assert event["project"] == _PROJECT_HASH
    assert event["hits"] == len(body["hits"]) >= 1
    assert event["degraded"] is False

    hit_events = _by_kind(events, "memory_hit")
    assert [hit["memory_id"] for hit in hit_events] == [
        hit["memory_id"] for hit in body["hits"]
    ]
    assert [hit["rank"] for hit in hit_events] == list(range(1, len(hit_events) + 1))
    assert [hit["score"] for hit in hit_events] == [
        hit["score"] for hit in body["hits"]
    ]


def test_query_zero_hits_when_threshold_filters_everything(make_client, capsys) -> None:
    client = make_client()
    capsys.readouterr()
    response = client.post(
        "/query",
        json={"query": "canonical", "user_id": "alice", "min_score_memory": 1.0},
    )
    assert response.status_code == 200
    assert response.json()["hits"] == []
    events = _events(capsys)
    assert _by_kind(events, "memory_hit") == []
    (event,) = _by_kind(events, "query_event")
    assert event["hits"] == 0
    assert event["degraded"] is False
    assert event["project"] == ""
    # Results existed but the score threshold removed them all.
    assert event["candidates"] == response.json()["total"] == 2
    assert event["lexical"] == 2
    assert event["semantic"] == 2


def test_query_degraded_when_retrieval_fails(make_client, capsys) -> None:
    client = make_client(semantic_error=True)
    capsys.readouterr()
    response = client.post("/query", json={"query": "canonical", "user_id": "alice"})
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    (event,) = _by_kind(_events(capsys), "query_event")
    assert event["degraded"] is True
    assert event["hits"] == 0
    assert event["degradation_reasons"] == body["degradation_reasons"]
    assert event["degradation_reasons"][0].startswith("memory_store:")
    assert event["candidates"] == 0
    assert event["lexical"] is None and event["semantic"] is None


def test_query_event_carries_retrieval_degradation(make_client, capsys) -> None:
    client = make_client(lexical_error=True)
    capsys.readouterr()
    response = client.post("/query", json={"query": "canonical", "user_id": "alice"})
    assert response.status_code == 200
    assert response.json()["degraded"] is False  # HTTP body is unchanged
    (event,) = _by_kind(_events(capsys), "query_event")
    assert event["degraded"] is True
    assert event["degradation_reasons"] == ["lexical_unavailable"]
    assert event["lexical"] == 0
    assert event["semantic"] == 2
    assert event["candidates"] == 2


def test_query_explicit_project_wins(make_client, capsys) -> None:
    client = make_client()
    capsys.readouterr()
    response = client.post(
        "/query",
        json={"query": "canonical", "user_id": _PROJECT_USER, "project": "beef01"},
    )
    assert response.status_code == 200
    assert {e["project"] for e in _events(capsys)} == {"beef01"}


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


def _all_requests(client: TestClient) -> dict[str, Any]:
    return {
        "search": client.post(
            "/search", json={"query": "canonical", "user_id": "alice"}
        ),
        "retrieve": client.post(
            "/retrieve",
            json={"query": "canonical", "scopes": ["project"], "user_id": "alice"},
        ),
        "query": client.post(
            "/query", json={"query": "canonical", "user_id": "alice"}
        ),
    }


def _stable(body: dict[str, Any]) -> dict[str, Any]:
    """Drop volatile timing/trace fields so bodies can be compared."""
    return {
        key: value
        for key, value in body.items()
        if key not in {"trace", "request_id"}
    }


def test_stdout_events_never_contain_memory_text(make_client, capsys) -> None:
    client = make_client()
    capsys.readouterr()
    responses = _all_requests(client)
    assert all(r.status_code == 200 for r in responses.values())
    assert _SECRET_TEXT in responses["search"].text  # sanity: body has the text
    out = capsys.readouterr().out
    assert out.strip()
    assert _SECRET_TEXT not in out
    for line in out.splitlines():
        assert json.loads(line)["event"] in {"query_event", "memory_hit"}


@pytest.mark.parametrize("target", ["_write", "emit_query_event", "emit_memory_hits"])
def test_emission_failure_does_not_fail_or_change_responses(
    make_client, monkeypatch: MonkeyPatch, capsys, target: str
) -> None:
    client = make_client()
    baseline = {name: r for name, r in _all_requests(client).items()}
    assert all(r.status_code == 200 for r in baseline.values())
    capsys.readouterr()

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("emission exploded")

    monkeypatch.setattr(query_events, target, boom)
    failed = _all_requests(client)
    for name, response in failed.items():
        assert response.status_code == 200, name
        assert _stable(response.json()) == _stable(baseline[name].json()), name


def test_events_do_not_reach_root_logger(make_client, caplog, capsys) -> None:
    client = make_client()
    capsys.readouterr()
    caplog.clear()
    with caplog.at_level(logging.INFO):
        response = client.post("/search", json={"query": "canonical", "user_id": "a"})
    assert response.status_code == 200
    assert not [r for r in caplog.records if r.name == "mem0.events"]
    assert _by_kind(_events(capsys), "query_event")


# ---------------------------------------------------------------------------
# Hard backend failures
# ---------------------------------------------------------------------------


class _BackendDown(Exception):
    pass


class _FailingRetrieval:
    def search(self, *_: Any, **__: Any) -> Any:
        raise _BackendDown("vector store down")

    def retrieve(self, *_: Any, **__: Any) -> Any:
        raise _BackendDown("vector store down")


def _error_count(operation: str) -> float:
    from services.metrics import memory_operation_errors_total

    return memory_operation_errors_total.labels(operation=operation)._value.get()


@pytest.mark.parametrize("endpoint", ["search", "retrieve"])
def test_hard_backend_failure_emits_degraded_event_and_reraises_same_exception(
    endpoint: str, capsys: CaptureFixture[str]
) -> None:
    from api_models import RetrieveRequest, SearchRequest
    from services.anchor_service import AnchorService
    from services.memory_service import MemoryService

    service = MemoryService(
        retrieval_service=_FailingRetrieval(), anchor_service=AnchorService()
    )
    if endpoint == "search":
        payload: Any = SearchRequest(query="boom", user_id=_PROJECT_USER)
        call = service.search
    else:
        payload = RetrieveRequest(
            query="boom", scopes=["project"], user_id=_PROJECT_USER, limit=3
        )
        call = service.retrieve
    errors_before = _error_count(endpoint)
    capsys.readouterr()

    with pytest.raises(_BackendDown) as raised:
        call(object(), payload)

    assert str(raised.value) == "vector store down"
    assert _error_count(endpoint) == errors_before + 1
    events = _events(capsys)
    assert _by_kind(events, "memory_hit") == []
    (event,) = _by_kind(events, "query_event")
    assert event["endpoint"] == endpoint
    assert event["hits"] == 0
    assert event["degraded"] is True
    assert event["degradation_reasons"] == ["_BackendDown"]
    assert event["project"] == _PROJECT_HASH
    assert event["query"] == "boom"


@pytest.mark.parametrize("endpoint", ["search", "retrieve"])
def test_failed_event_emission_never_masks_the_original_exception(
    endpoint: str, monkeypatch: MonkeyPatch
) -> None:
    from api_models import RetrieveRequest, SearchRequest
    from services.anchor_service import AnchorService
    from services.memory_service import MemoryService

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("emission exploded")

    monkeypatch.setattr(query_events, "emit_query_event", boom)
    service = MemoryService(
        retrieval_service=_FailingRetrieval(), anchor_service=AnchorService()
    )
    errors_before = _error_count(endpoint)
    if endpoint == "search":
        call, payload = service.search, SearchRequest(query="q", user_id="alice")
    else:
        call = service.retrieve
        payload = RetrieveRequest(query="q", scopes=["project"], user_id="alice")

    with pytest.raises(_BackendDown):
        call(object(), payload)
    assert _error_count(endpoint) == errors_before + 1
