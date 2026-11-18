"""Delivery guarantees and crash boundaries of capture-id receipts."""
from __future__ import annotations

import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.write_receipts import (
    WriteConflictError,
    WritePendingError,
    WriteReceiptStateError,
    WriteReceiptStore,
)


PAYLOAD = {"messages": [{"role": "user", "content": "Use the shared cache."}]}
SCOPE = {"user_id": "user-1", "agent_id": "project-1", "run_id": None}


def _claim(store, *, capture_id="capture-1", namespace="backend-1", scope=None, payload=None):
    return store.claim(
        capture_id, namespace=namespace, identifiers=SCOPE if scope is None else scope,
        payload=PAYLOAD if payload is None else payload,
    )


def test_store_is_lazy_and_requires_durable_path(tmp_path, monkeypatch):
    path = tmp_path / "new-directory" / "writes.db"
    monkeypatch.setenv("MEM0_WRITE_DB_PATH", str(path))
    store = WriteReceiptStore()
    assert not path.parent.exists()
    _claim(store)
    assert path.is_file()
    with pytest.raises(ValueError, match="persistent"):
        WriteReceiptStore(":memory:")


@pytest.mark.parametrize("response", [
    {"results": [{"id": "m1", "memory": "Use the shared cache."}], "status": "saved"},
    {"results": [], "status": "skipped", "reason": "no_durable_knowledge"},
])
def test_completed_result_replays_after_restart(tmp_path, response):
    path = tmp_path / "writes.db"
    store = WriteReceiptStore(path)
    first = _claim(store)
    assert not first.replayed
    store.complete(first, response)

    replay = _claim(WriteReceiptStore(path))
    assert replay.replayed
    assert replay.response == response
    assert replay.owner_token is None


def test_same_capture_with_different_payload_conflicts_without_replacing_result(tmp_path):
    store = WriteReceiptStore(tmp_path / "writes.db")
    claim = _claim(store)
    store.complete(claim, {"results": [{"id": "m1"}]})
    with pytest.raises(WriteConflictError):
        _claim(store, payload={"messages": [{"role": "user", "content": "Different fact"}]})
    assert _claim(store).response == {"results": [{"id": "m1"}]}


def test_scope_and_backend_namespaces_are_independent(tmp_path):
    store = WriteReceiptStore(tmp_path / "writes.db")
    claims = [
        _claim(store),
        _claim(store, namespace="backend-2"),
        _claim(store, scope={**SCOPE, "user_id": "user-2"}),
        _claim(store, scope={**SCOPE, "agent_id": "project-2"}),
        _claim(store, scope={**SCOPE, "run_id": "session-2"}),
        _claim(store, capture_id="capture-2"),
    ]
    assert len({claim.key for claim in claims}) == len(claims)
    assert all(not claim.replayed for claim in claims)


def test_canonical_payload_order_does_not_conflict(tmp_path):
    store = WriteReceiptStore(tmp_path / "writes.db")
    claim = _claim(store, payload={"a": 1, "b": {"c": 2, "d": 3}})
    store.complete(claim, {"results": []})
    assert _claim(store, payload={"b": {"d": 3, "c": 2}, "a": 1}).replayed


def test_concurrent_connections_grant_only_one_owner(tmp_path):
    path = tmp_path / "writes.db"
    start = Barrier(8)

    def compete(_):
        start.wait(timeout=10)
        try:
            return _claim(WriteReceiptStore(path))
        except WritePendingError:
            return None

    with ThreadPoolExecutor(max_workers=8) as workers:
        claims = list(workers.map(compete, range(8)))
    assert sum(claim is not None for claim in claims) == 1


def test_old_pending_claim_is_not_reexecuted_after_restart(tmp_path):
    path = tmp_path / "writes.db"
    first = _claim(WriteReceiptStore(path))
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE memory_write_receipts SET created_at = 0, updated_at = 0")
    with pytest.raises(WritePendingError) as error:
        _claim(WriteReceiptStore(path))
    assert error.value.state == "pending"
    # The original owner can still acknowledge a late successful operation.
    WriteReceiptStore(path).complete(first, {"results": [{"id": "late-success"}]})
    assert _claim(WriteReceiptStore(path)).response["results"][0]["id"] == "late-success"


def test_unknown_write_is_retained_and_cannot_be_released_for_retry(tmp_path):
    path = tmp_path / "writes.db"
    store = WriteReceiptStore(path)
    claim = _claim(store)
    store.fail(claim)
    with pytest.raises(WritePendingError) as error:
        _claim(WriteReceiptStore(path))
    assert error.value.state == "unknown"
    with pytest.raises(WriteReceiptStateError, match="ambiguous"):
        store.fail(claim, retryable=True)


def test_proven_no_side_effect_failure_can_be_retried(tmp_path):
    store = WriteReceiptStore(tmp_path / "writes.db")
    first = _claim(store)
    store.fail(first, retryable=True)
    second = _claim(store)
    assert second.owner_token != first.owner_token
    with pytest.raises(WriteReceiptStateError):
        store.complete(first, {"results": [{"id": "stale-owner"}]})
    store.complete(second, {"results": [{"id": "m1"}]})
    assert _claim(store).response == {"results": [{"id": "m1"}]}


def test_completion_is_immutable_and_failure_cannot_erase_success(tmp_path):
    store = WriteReceiptStore(tmp_path / "writes.db")
    first = _claim(store)
    response = {"results": [{"id": "m1"}]}
    store.complete(first, response)
    store.complete(first, response)
    store.fail(first)
    store.fail(first, retryable=True)
    assert _claim(store).response == response
    with pytest.raises(WriteReceiptStateError, match="cannot be replaced"):
        store.complete(first, {"results": []})
    with pytest.raises(WriteReceiptStateError, match="replayed"):
        store.fail(_claim(store))
