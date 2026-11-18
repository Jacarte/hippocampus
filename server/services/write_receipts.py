"""Durable receipts for client-supplied capture IDs.

The receipt is committed before calling the memory backend. A completed receipt
can be replayed, but a pending or ambiguous receipt is never automatically
executed again: SQLite and the vector store do not share a transaction. Such a
receipt needs reconciliation against the backend before it can be resolved.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping


_ENTITY_KEYS = ("user_id", "agent_id", "run_id")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_write_receipts (
    capture_key TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    owner_token TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'complete', 'unknown')),
    response_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class WriteConflictError(RuntimeError):
    """A capture ID was reused for a different request within the same scope."""

    code = "capture_conflict"


class WritePendingError(RuntimeError):
    """An earlier attempt may have persisted; automatic replay is unsafe."""

    code = "capture_pending"

    def __init__(self, state: str) -> None:
        self.state = state
        super().__init__(
            "This capture is pending or has an unknown outcome; "
            "reconcile the earlier write before retrying it."
        )


class WriteReceiptStateError(RuntimeError):
    """An attempt to change a receipt without its current ownership token."""


@dataclass(frozen=True)
class WriteClaim:
    key: str
    fingerprint: str
    owner_token: str | None
    response: Any = None

    @property
    def replayed(self) -> bool:
        return self.owner_token is None


class WriteReceiptStore:
    """Lazy, per-operation SQLite connections; no lock spans backend work.

    ``namespace`` should identify the backend storage target, not credentials
    or process identity. Capture IDs are independently scoped by that namespace
    and the exact user/agent/run identifiers. The payload is fingerprinted but
    is not stored in the receipt. Responses are retained to replay saved and
    intentionally skipped outcomes after a restart.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = os.fspath(path) if path is not None else os.environ.get(
            "MEM0_WRITE_DB_PATH", "/var/lib/mem0/writes.db"
        )
        if not self.path or self.path == ":memory:":
            raise ValueError("Write receipts require a persistent SQLite file.")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(_SCHEMA)
            # Lock only this short receipt operation, including ownership
            # checks. No transaction remains open while the backend runs.
            connection.execute("BEGIN IMMEDIATE")
            with connection:
                yield connection
        finally:
            connection.close()

    def claim(
        self,
        capture_id: str,
        *,
        namespace: str,
        identifiers: Mapping[str, str | None],
        payload: Any,
    ) -> WriteClaim:
        if not isinstance(capture_id, str) or not capture_id.strip():
            raise ValueError("capture_id must be a non-empty string")
        if not isinstance(namespace, str) or not namespace:
            raise ValueError("namespace must be a non-empty string")
        if set(identifiers) - set(_ENTITY_KEYS):
            raise ValueError("Unsupported receipt scope identifier")
        scope = {key: identifiers.get(key) for key in _ENTITY_KEYS}
        if any(value is not None and not isinstance(value, str) for value in scope.values()):
            raise ValueError("Receipt scope identifiers must be strings or null")
        if not any(scope.values()):
            raise ValueError("A receipt requires at least one scope identifier")
        key = _digest([namespace, scope, capture_id])
        fingerprint = _digest(payload)
        token = uuid.uuid4().hex
        now = time.time()
        with self._connection() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO memory_write_receipts "
                "(capture_key, fingerprint, owner_token, state, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pending', ?, ?)",
                (key, fingerprint, token, now, now),
            ).rowcount
            if inserted:
                return WriteClaim(key, fingerprint, token)
            row = connection.execute(
                "SELECT fingerprint, state, response_json FROM memory_write_receipts "
                "WHERE capture_key = ?", (key,),
            ).fetchone()
            assert row is not None
            if row[0] != fingerprint:
                raise WriteConflictError(
                    "This capture_id was already used for a different request."
                )
            if row[1] == "complete":
                return WriteClaim(key, fingerprint, None, json.loads(row[2]))
            raise WritePendingError(row[1])

    def complete(self, claim: WriteClaim, response: Any) -> None:
        """Persist the backend's confirmed result; an identical repeat is safe."""
        response_json = _json(response)
        with self._connection() as connection:
            state, previous_response = self._owned_receipt(connection, claim)
            if state == "complete":
                if previous_response != response_json:
                    raise WriteReceiptStateError("A completed result cannot be replaced.")
                return
            connection.execute(
                "UPDATE memory_write_receipts SET state = 'complete', "
                "response_json = ?, updated_at = ? WHERE capture_key = ?",
                (response_json, time.time(), claim.key),
            )

    def fail(self, claim: WriteClaim, *, retryable: bool = False) -> None:
        """Retain uncertainty unless the caller proves no side effects occurred.

        ``retryable=True`` releases a pending claim only for failures known to
        precede every backend mutation. Never use it for timeouts, connection
        loss, partial writes, or other uncertain outcomes.
        """
        with self._connection() as connection:
            state, _ = self._owned_receipt(connection, claim)
            if state == "complete":
                return
            if retryable:
                if state != "pending":
                    raise WriteReceiptStateError("An ambiguous write cannot be released.")
                connection.execute(
                    "DELETE FROM memory_write_receipts WHERE capture_key = ?", (claim.key,)
                )
            else:
                connection.execute(
                    "UPDATE memory_write_receipts SET state = 'unknown', updated_at = ? "
                    "WHERE capture_key = ?", (time.time(), claim.key),
                )

    @staticmethod
    def _owned_receipt(
        connection: sqlite3.Connection, claim: WriteClaim,
    ) -> tuple[str, str | None]:
        if claim.replayed:
            raise WriteReceiptStateError("A replayed receipt cannot be changed.")
        row = connection.execute(
            "SELECT state, response_json FROM memory_write_receipts "
            "WHERE capture_key = ? AND fingerprint = ? AND owner_token = ?",
            (claim.key, claim.fingerprint, claim.owner_token),
        ).fetchone()
        if row is None:
            raise WriteReceiptStateError("The write receipt is no longer owned by this claim.")
        return row[0], row[1]
