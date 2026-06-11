"""SQLite-backed visit telemetry persistence for the admin CMS.

This module provides :class:`VisitStore`, a dedicated lightweight store for
admin-visit telemetry used by the internal CMS.  It is intentionally separate
from the mem0 metadata store so that:

* visit counters and ``last_visited_at`` survive ``POST /configure`` resets of
  the memory store,
* visit data can be inspected and reset independently of memory payloads
  (remove ``MEM0_VISIT_DB_PATH`` / its file to clear telemetry),
* the schema stays narrow and deterministic — a simple table per visit event
  with a denormalized aggregate row, both keyed by ``memory_id``.

The store is opened against a single SQLite file path.  The admin service
configures that path from ``MEM0_VISIT_DB_PATH`` (see
:func:`services.runtime.get_admin_runtime_options`).  All public methods are
thread-safe: SQLite connections are created per-call with
``check_same_thread=False`` and short-lived transactions.

Example:
    >>> store = VisitStore(path=":memory:")  # doctest: +SKIP
    >>> store.record_visit("m1", reason="detail_open")  # doctest: +SKIP
    >>> store.get_aggregates("m1").total_visits  # doctest: +SKIP
    1
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from api_models import VisitReason


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

# Visit events table — one row per recorded visit.  This is the source of
# truth for time-series telemetry and supports future aggregations (per-reason
# histograms, freshness windows, etc.).
_VISIT_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS visit_events (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id     TEXT    NOT NULL,
    reason        TEXT    NOT NULL,
    visited_at    TEXT    NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS visit_events_memory_id_idx
    ON visit_events (memory_id);
CREATE INDEX IF NOT EXISTS visit_events_visited_at_idx
    ON visit_events (visited_at);
"""

# Per-memory aggregate table — denormalized counters kept in sync with the
# events table inside the same transaction.  Allows ``get_aggregates()`` to
# return popularity data in O(1) without scanning the events table.
_VISIT_AGGREGATES_SCHEMA = """
CREATE TABLE IF NOT EXISTS visit_aggregates (
    memory_id       TEXT PRIMARY KEY,
    total_visits    INTEGER NOT NULL DEFAULT 0,
    last_visited_at TEXT
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with ``Z`` suffix."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _normalize_reason(reason: str) -> str:
    """Return *reason* unchanged after a non-empty, string check.

    The closed ``VisitReason`` literal is enforced at the API-model layer
    (Pydantic), but we still defend the store against unexpected inputs so
    that future internal callers cannot corrupt the events table.
    """
    if not isinstance(reason, str) or not reason:
        raise ValueError("reason must be a non-empty string")
    return reason


def _normalize_metadata(metadata: dict[str, Any] | None) -> str | None:
    """Serialize *metadata* to a compact JSON string for storage, or ``None``."""
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a dict when provided")
    return json.dumps(metadata, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Data classes returned by VisitStore
# ---------------------------------------------------------------------------


@dataclass
class VisitAggregates:
    """Aggregated visit data for a single memory.

    The ``never_visited`` derived property is the single source of truth
    for the freshness block on the admin memory responses — the CMS
    relies on it (not a client-side ``last_visited_at is None`` check) to
    keep never-visited memories visibly cold.  See
    :class:`api_models.AdminFreshnessInfo` for the wire shape.

    Attributes:
        memory_id: The memory these aggregates belong to.
        total_visits: Lifetime number of visit events recorded for this memory.
        last_visited_at: ISO 8601 timestamp of the most recent visit, or
            ``None`` if the memory has never been visited.
    """

    memory_id: str
    total_visits: int
    last_visited_at: str | None

    @property
    def never_visited(self) -> bool:
        """Return ``True`` when this memory has zero recorded visits.

        This is the canonical "never visited" signal: it is independent
        of the *last_visited_at* representation (a memory with
        ``last_visited_at=None`` is also ``never_visited=True``, but the
        CMS uses this property rather than null-checks so future
        representations — e.g. epoch timestamps, partial dates — still
        surface the cold state correctly).
        """
        return self.total_visits == 0


@dataclass
class VisitEvent:
    """A single visit event row.

    Attributes:
        event_id: Auto-incrementing primary key from the ``visit_events``
            table; ``None`` for events that have not been persisted yet.
        memory_id: The memory that was visited.
        reason: One of the ``VisitReason`` literal values.
        visited_at: ISO 8601 timestamp at which the visit was recorded.
        metadata: Optional metadata dict reconstructed from the persisted
            JSON; ``None`` when no metadata was attached.
    """

    event_id: int | None
    memory_id: str
    reason: str
    visited_at: str
    metadata: dict[str, Any] | None = field(default=None)


# ---------------------------------------------------------------------------
# VisitStore
# ---------------------------------------------------------------------------


class VisitStore:
    """SQLite-backed visit telemetry store.

    The store is a thin layer over a single SQLite file with two tables
    (``visit_events`` and ``visit_aggregates``) defined in
    :data:`_VISIT_EVENTS_SCHEMA` and :data:`_VISIT_AGGREGATES_SCHEMA`.  The
    schema is bootstrapped lazily on first open, so callers can point at a
    fresh path and immediately record visits.

    For ``":memory:"`` databases the implementation keeps a long-lived
    connection alive so the schema and rows survive between operations —
    without that, SQLite would tear down the database every time a new
    connection is opened.  File-based stores continue to use a fresh
    connection per call so the write state is not shared across threads.

    Args:
        path: Filesystem path to the SQLite database.  Use ``":memory:"`` for
            an in-process, ephemeral store (useful in tests).  The default
            uses ``MEM0_VISIT_DB_PATH`` from the environment.
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path or os.environ.get(
            "MEM0_VISIT_DB_PATH", "/var/lib/mem0/visits.db"
        )
        self._schema_lock = threading.Lock()
        self._schema_initialized = False
        # ``:memory:`` databases are per-connection in SQLite; without a
        # long-lived connection the schema is lost on the second ``_open``.
        # We keep a single dedicated connection for the in-memory case so
        # tests and ephemeral processes see a stable database.  For file
        # databases we still open a short-lived connection per call.
        self._memory_connection: sqlite3.Connection | None = None

    # -- lifecycle -------------------------------------------------------

    @property
    def path(self) -> str:
        """The SQLite file path this store is bound to."""
        return self._path

    def _open(self) -> sqlite3.Connection:
        """Open a SQLite connection with a short-lived transaction.

        For ``:memory:`` databases the same connection is reused across
        calls so the schema and rows survive between operations.  For file
        databases a fresh connection is opened per call to avoid sharing
        write-state across threads.

        File-backed paths get their parent directory created on demand
        (see :meth:`_ensure_parent_dir`) so the default
        ``/var/lib/mem0/visits.db`` works on a fresh host without manual
        ``mkdir -p``.  The directory-creation step is skipped entirely
        for ``:memory:`` and other special SQLite paths.
        """
        if self._path == ":memory:":
            connection = self._memory_connection
            if connection is None:
                connection = sqlite3.connect(
                    self._path, check_same_thread=False
                )
                connection.row_factory = sqlite3.Row
                self._memory_connection = connection
            self._ensure_schema(connection)
            return connection

        self._ensure_parent_dir(self._path)
        connection = sqlite3.connect(self._path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        # WAL gives better concurrency for the admin CMS which may read and
        # write from different threads; safe even on :memory: databases.
        try:
            connection.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.DatabaseError:
            # WAL is not supported on in-memory or read-only databases; the
            # default journal mode is fine in those cases.
            pass
        self._ensure_schema(connection)
        return connection

    @staticmethod
    def _ensure_parent_dir(path: str) -> None:
        """Create the parent directory of *path* if it does not yet exist.

        File-backed SQLite stores need the parent directory to exist
        before :func:`sqlite3.connect` can open the file.  The default
        ``MEM0_VISIT_DB_PATH`` (``/var/lib/mem0/visits.db``) is unlikely
        to exist on a fresh host, so the store creates any missing
        intermediate directories transparently.

        Behavior matrix:

        * Real file paths (e.g. ``/var/lib/mem0/visits.db``) — create
          every missing directory with ``os.makedirs(..., exist_ok=True)``.
        * In-memory ``":memory:"`` — short-circuits at the top of
          :meth:`_open` and never reaches this helper, so no directory
          side-effects.
        * Special SQLite URI paths (``file:...`` etc.) — also short-circuit
          out of the file branch in :meth:`_open` because the
          ``:memory:`` check is the only short-circuit we have.  We guard
          the helper with an additional check that the path is not
          empty and does not look like a URI scheme so a future
          short-circuit addition cannot accidentally create directories
          in arbitrary filesystem locations.
        * Empty path or path with no parent component (e.g. ``visits.db``
          in the current working directory) — no-op, nothing to create.

        Raises:
            OSError: When the parent directory cannot be created
                (permission denied, parent path is a file, etc.).  The
                error propagates so the caller sees the same exception
                type as before the fix.
        """
        if not path or path == ":memory:":
            return
        # Guard against SQLite URI schemes — they should never reach this
        # helper today, but the check makes the behavior explicit and
        # safe if a future caller adds a new short-circuit branch.
        if "://" in path or path.startswith("file:"):
            return
        parent = os.path.dirname(path)
        if not parent:
            return
        os.makedirs(parent, exist_ok=True)

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        """Create the schema if it has not been created yet."""
        if self._schema_initialized:
            return
        with self._schema_lock:
            if self._schema_initialized:
                return
            connection.executescript(_VISIT_EVENTS_SCHEMA)
            connection.executescript(_VISIT_AGGREGATES_SCHEMA)
            connection.commit()
            self._schema_initialized = True

    def _release(self, connection: sqlite3.Connection) -> None:
        """Close *connection* unless it is the long-lived in-memory one.

        The in-memory SQLite database is per-connection, so the schema and
        rows live on the dedicated ``_memory_connection``.  Closing it would
        invalidate the database mid-process; file-based connections are
        short-lived and always closed here.
        """
        if connection is self._memory_connection:
            return
        connection.close()

    def close(self) -> None:
        """Reset schema-initialization state and release the in-memory connection.

        File-based connections are short-lived and do not need to be tracked
        here, but the long-lived ``:memory:`` connection must be closed
        explicitly when tests swap to a fresh database.
        """
        with self._schema_lock:
            self._schema_initialized = False
            if self._memory_connection is not None:
                try:
                    self._memory_connection.close()
                finally:
                    self._memory_connection = None

    # -- write ------------------------------------------------------------

    def record_visit(
        self,
        memory_id: str,
        *,
        reason: str,
        metadata: dict[str, Any] | None = None,
        visited_at: str | None = None,
    ) -> VisitAggregates:
        """Record a visit event for *memory_id* and update aggregates.

        Predictability contract (Task 6):

        * Each successful call increments ``total_visits`` by exactly one.
        * ``last_visited_at`` is set to the maximum of the existing value
          and the new ``visited_at``.  ISO 8601 with ``Z`` suffix is
          lexicographically sortable so this comparison is monotonic in
          real time, and tolerates out-of-order inserts (e.g. during test
          fixtures) without going backwards.
        * The backend never fabricates a ``last_visited_at``: a memory
          with zero recorded visits has ``last_visited_at=None`` and
          ``never_visited=True``.  See :class:`VisitAggregates`.

        Compare with :meth:`get_aggregates` (read-only) and
        :meth:`get_aggregates_for_memories` (batched read).

        Args:
            memory_id: Identifier of the memory being visited.  Required.
            reason: Closed ``VisitReason`` literal (validated upstream by the
                ``AdminMemoryVisitRequest`` Pydantic model).  Required.
            metadata: Optional per-visit metadata stored alongside the event;
                useful for future per-reason analytics.  Defaults to ``None``.
            visited_at: Optional ISO 8601 timestamp override; defaults to the
                current UTC time.  Provided for deterministic tests.

        Returns:
            :class:`VisitAggregates` reflecting the memory's counters and
            ``last_visited_at`` *after* this visit was recorded.  A
            successful call to ``record_visit`` always returns
            ``never_visited=False`` because the memory now has at least
            one visit event.

        Raises:
            ValueError: When *memory_id* or *reason* is empty, or when
                *metadata* is not a dict.
        """
        if not isinstance(memory_id, str) or not memory_id:
            raise ValueError("memory_id must be a non-empty string")
        normalized_reason = _normalize_reason(reason)
        normalized_metadata = _normalize_metadata(metadata)
        normalized_timestamp = visited_at or _now_iso()

        connection = self._open()
        try:
            connection.execute(
                "INSERT INTO visit_events (memory_id, reason, visited_at, metadata_json) "
                "VALUES (?, ?, ?, ?)",
                (
                    memory_id,
                    normalized_reason,
                    normalized_timestamp,
                    normalized_metadata,
                ),
            )
            connection.execute(
                "INSERT INTO visit_aggregates (memory_id, total_visits, last_visited_at) "
                "VALUES (?, 1, ?) "
                "ON CONFLICT(memory_id) DO UPDATE SET "
                "  total_visits = total_visits + 1, "
                "  last_visited_at = CASE "
                "    WHEN excluded.last_visited_at IS NULL THEN last_visited_at "
                "    WHEN last_visited_at IS NULL THEN excluded.last_visited_at "
                "    WHEN excluded.last_visited_at > last_visited_at "
                "      THEN excluded.last_visited_at "
                "    ELSE last_visited_at "
                "  END",
                (memory_id, normalized_timestamp),
            )
            connection.commit()
        finally:
            self._release(connection)

        aggregates = self.get_aggregates(memory_id)
        # ``get_aggregates`` returns a default aggregate (total_visits=0) when
        # the memory has not been recorded yet.  Replace it with the actual
        # persisted value to keep the return-type contract exact.
        if aggregates.memory_id != memory_id:
            return VisitAggregates(
                memory_id=memory_id,
                total_visits=1,
                last_visited_at=normalized_timestamp,
            )
        return aggregates

    # -- read -------------------------------------------------------------

    def get_aggregates(self, memory_id: str) -> VisitAggregates:
        """Return aggregate counters for *memory_id*.

        Memories that have never been visited return a zero-valued aggregate
        with ``last_visited_at=None`` and ``never_visited=True`` rather than
        ``None``, so callers do not need to special-case the empty result.

        Args:
            memory_id: Identifier of the memory to look up.

        Returns:
            A :class:`VisitAggregates` instance.  ``memory_id`` echoes the
            input argument so callers can use the result uniformly.
        """
        if not isinstance(memory_id, str) or not memory_id:
            raise ValueError("memory_id must be a non-empty string")

        connection = self._open()
        try:
            row = connection.execute(
                "SELECT total_visits, last_visited_at "
                "FROM visit_aggregates WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        finally:
            self._release(connection)

        if row is None:
            return VisitAggregates(
                memory_id=memory_id,
                total_visits=0,
                last_visited_at=None,
            )
        return VisitAggregates(
            memory_id=memory_id,
            total_visits=int(row["total_visits"]),
            last_visited_at=row["last_visited_at"],
        )

    def get_aggregates_for_memories(
        self, memory_ids: Iterable[str]
    ) -> dict[str, VisitAggregates]:
        """Return aggregates for several memories in a single query.

        Memories with no recorded visits are included in the result with a
        zero-valued aggregate, mirroring the single-memory behavior of
        :meth:`get_aggregates`.

        Args:
            memory_ids: Iterable of memory identifiers to look up.

        Returns:
            A ``dict`` keyed by ``memory_id`` whose values are the matching
            :class:`VisitAggregates` instances.
        """
        ids = list(memory_ids)
        if not ids:
            return {}

        connection = self._open()
        try:
            placeholders = ",".join("?" for _ in ids)
            rows = connection.execute(
                f"SELECT memory_id, total_visits, last_visited_at "
                f"FROM visit_aggregates WHERE memory_id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
        finally:
            self._release(connection)

        results: dict[str, VisitAggregates] = {
            memory_id: VisitAggregates(
                memory_id=memory_id,
                total_visits=0,
                last_visited_at=None,
            )
            for memory_id in ids
        }
        for row in rows:
            results[row["memory_id"]] = VisitAggregates(
                memory_id=row["memory_id"],
                total_visits=int(row["total_visits"]),
                last_visited_at=row["last_visited_at"],
            )
        return results

    def list_events(
        self,
        memory_id: str,
        *,
        limit: int = 100,
    ) -> list[VisitEvent]:
        """Return the most recent visit events for *memory_id*.

        Args:
            memory_id: Memory to filter on.
            limit: Maximum number of events to return; clamped to ``>= 1``.
                Defaults to ``100``.

        Returns:
            Newest-first list of :class:`VisitEvent` instances; empty list
            when no events exist for the memory.
        """
        if not isinstance(memory_id, str) or not memory_id:
            raise ValueError("memory_id must be a non-empty string")
        effective_limit = max(1, int(limit))

        connection = self._open()
        try:
            rows = connection.execute(
                "SELECT event_id, memory_id, reason, visited_at, metadata_json "
                "FROM visit_events WHERE memory_id = ? "
                "ORDER BY visited_at DESC, event_id DESC LIMIT ?",
                (memory_id, effective_limit),
            ).fetchall()
        finally:
            self._release(connection)

        return [
            VisitEvent(
                event_id=int(row["event_id"]),
                memory_id=row["memory_id"],
                reason=row["reason"],
                visited_at=row["visited_at"],
                metadata=(
                    json.loads(row["metadata_json"])
                    if row["metadata_json"]
                    else None
                ),
            )
            for row in rows
        ]

    def total_visits_globally(self) -> int:
        """Return the total number of visit events recorded across all memories.

        This is exposed primarily so the admin service can compute the
        ``visit_ratio`` popularity signal when the most-visited counter is
        not yet available.  Aggregating client-side keeps the store schema
        small.
        """
        connection = self._open()
        try:
            row = connection.execute(
                "SELECT COALESCE(SUM(total_visits), 0) AS total "
                "FROM visit_aggregates"
            ).fetchone()
        finally:
            self._release(connection)
        return int(row["total"] or 0)

    def max_total_visits(self) -> int:
        """Return the highest ``total_visits`` counter across all memories.

        Used by the admin service to compute ``visit_ratio`` (each memory's
        ``total_visits`` divided by this max, capped at 1.0).
        """
        connection = self._open()
        try:
            row = connection.execute(
                "SELECT COALESCE(MAX(total_visits), 0) AS max_total "
                "FROM visit_aggregates"
            ).fetchone()
        finally:
            self._release(connection)
        return int(row["max_total"] or 0)

    # -- maintenance ------------------------------------------------------

    def reset(self) -> None:
        """Delete every visit event and aggregate row.

        Used by tests and operator maintenance flows; not exposed as a public
        API endpoint in v1.
        """
        connection = self._open()
        try:
            connection.execute("DELETE FROM visit_events")
            connection.execute("DELETE FROM visit_aggregates")
            connection.commit()
        finally:
            self._release(connection)


__all__ = [
    "VisitAggregates",
    "VisitEvent",
    "VisitStore",
    "VisitReason",
]
