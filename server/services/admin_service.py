"""Admin CMS service scaffold for cross-user memory management.

:class:`AdminService` centralizes the audit-stamping and orchestration rules
the v1 internal CMS relies on so that route handlers (Task 3) can stay thin.

The service is the **single** place that:

* Stamps ``impersonated_by=admin`` on every CMS-initiated write.
* Attaches ``copied_from={ source_memory_id, source_scope, source_scope_id }``
  provenance on copy operations.
* Persists visit telemetry through :class:`VisitStore`, keeping the
  counters independent of mem0 metadata.

The service is **narrow** by design — Task 5/6/9 will fill in the concrete
list/detail/create/update/delete/copy/visit/index flows.  The methods exposed
here are thin placeholders that already enforce the audit-stamping rules and
delegated persistence, so Task 3 can wire routes and Task 5/6/9 can replace
the placeholders without changing the seam.

Visit telemetry persistence lives in :class:`~services.visit_store.VisitStore`
and is injected so tests can point the service at an in-memory SQLite
database (``":memory:"``) without touching the real ``MEM0_VISIT_DB_PATH``.
"""
from __future__ import annotations

import logging
import os
from copy import deepcopy
from typing import Any

from api_models import (
    AdminAuditInfo,
    AdminFreshnessInfo,
    AdminIndexFileInfo,
    AdminIndexJobInfo,
    AdminIndexLimits,
    AdminIndexOverviewResponse,
    AdminScopesResponse,
    AdminIndexRootInfo,
    AdminIndexVisibilityInputs,
    AdminMemoryCopyRequest,
    AdminMemoryCreateRequest,
    AdminMemoryUpdateRequest,
    AdminPopularityInfo,
    CopiedFromInfo,
    ScopeType,
    VisitReason,
)

from .anchor_service import AnchorService
from .file_scanner import FileScanner
from .tracing import trace_backend_operation
from .visit_store import VisitAggregates, VisitStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit constants
# ---------------------------------------------------------------------------

#: Literal value used to stamp ``impersonated_by`` on every admin write.
ADMIN_IMPERSONATOR: str = "admin"

#: Metadata key for the copy-provenance object on the *target* memory.
_COPIED_FROM_METADATA_KEY: str = "copied_from"

#: Metadata key for the impersonation stamp.
_IMPERSONATED_BY_METADATA_KEY: str = "impersonated_by"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scope_param_name(scope: ScopeType) -> str:
    """Return the mem0 keyword-argument name for the given admin scope.

    Args:
        scope: One of ``"user"``, ``"agent"``, ``"run"``.

    Returns:
        The matching mem0 keyword (``"user_id"``, ``"agent_id"``, ``"run_id"``).

    Raises:
        ValueError: If *scope* is not a known admin scope literal.
    """
    if scope == "user":
        return "user_id"
    if scope == "agent":
        return "agent_id"
    if scope == "run":
        return "run_id"
    raise ValueError(f"unsupported scope: {scope!r}")


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with ``Z`` suffix.

    Centralized so callers do not have to import ``datetime`` themselves.
    """
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _as_iso_string(value: Any) -> str | None:
    """Return *value* when it is already a string, otherwise ``None``."""
    return value if isinstance(value, str) else None


# ---------------------------------------------------------------------------
# AdminService
# ---------------------------------------------------------------------------


class AdminService:
    """Orchestrate admin CMS memory flows and centralize audit stamping.

    Args:
        visit_store: Backing :class:`VisitStore` for visit telemetry.  When
            omitted, a default store is created from ``MEM0_VISIT_DB_PATH``
            (see :func:`services.runtime.get_admin_runtime_options`).
        anchor_service: Optional :class:`AnchorService` used to normalize
            memory metadata in the same way as the public
            :class:`~services.memory_service.MemoryService`.  When omitted,
            a default :class:`AnchorService` is instantiated.
    """

    def __init__(
        self,
        *,
        visit_store: VisitStore | None = None,
        anchor_service: AnchorService | None = None,
    ) -> None:
        self._visit_store = visit_store or VisitStore()
        self._anchor_service = anchor_service or AnchorService()

    # -- introspection ----------------------------------------------------

    @property
    def visit_store(self) -> VisitStore:
        """Return the backing :class:`VisitStore` (used by tests)."""
        return self._visit_store

    def health(self) -> dict[str, Any]:
        """Return a small ``/admin/health`` payload describing readiness.

        Returns:
            A dict with ``status`` (``"ok"``), ``service`` (``"admin-cms"``),
            and ``visit_db_path`` (the SQLite path this service is bound to).
        """
        return {
            "status": "ok",
            "service": "admin-cms",
            "visit_db_path": self._visit_store.path,
        }

    # -- scopes introspection --------------------------------------------

    def list_scopes(self, memory_instance: Any) -> AdminScopesResponse:
        """Extract distinct scope identifiers from all stored memories.

        mem0's ``get_all()`` requires at least one identifier, so the
        method tries multiple fallback strategies in order:

        1. ``get_all()`` with no arguments
        2. ``get_all(filters={})`` with an empty filter dict
        3. ``get_all(limit=100000)`` with a large limit
        4. Direct PostgreSQL query on the pgvector table (reads
           ``payload->>'user_id'`` / ``agent_id`` / ``run_id`` from the
           ``POSTGRES_COLLECTION`` table)
        5. Graceful return of empty lists when all strategies fail

        For each successful call the method scans every record for
        top-level ``user_id``, ``agent_id``, ``run_id`` and metadata
        ``project`` / ``project_id`` keys, collecting distinct values.

        Returns:
            An :class:`AdminScopesResponse` with distinct ``users``,
            ``agents``, ``runs``, and ``projects`` lists.
        """
        strategies = [
            ("no_args", lambda: memory_instance.get_all()),
            ("empty_filters", lambda: memory_instance.get_all(filters={})),
            ("large_limit", lambda: memory_instance.get_all(limit=100000)),
        ]

        records: list[dict[str, Any]] = []
        for strategy_name, strategy_fn in strategies:
            try:
                result = strategy_fn()
                if isinstance(result, list):
                    records = result
                    logger.debug(
                        "list_scopes: strategy %r returned %d records",
                        strategy_name,
                        len(records),
                    )
                    break
                if isinstance(result, dict):
                    # Some mem0 backends wrap the list in a dict
                    inner = result.get("results") or result.get("data") or []
                    if isinstance(inner, list):
                        records = inner
                        logger.debug(
                            "list_scopes: strategy %r returned %d records (wrapped)",
                            strategy_name,
                            len(records),
                        )
                        break
            except Exception as exc:
                logger.debug(
                    "list_scopes: strategy %r failed: %s", strategy_name, exc
                )
                continue
        else:
            # 4th strategy: direct PostgreSQL query — final fallback when
            # the mem0 API refuses to return all records without a scope
            # identifier.
            try:
                rows = self._postgres_fallback_query(
                    "SELECT DISTINCT "
                    "payload->>'user_id' AS user_id, "
                    "payload->>'agent_id' AS agent_id, "
                    "payload->>'run_id' AS run_id "
                    "FROM {table} "
                    "WHERE payload->>'user_id' IS NOT NULL "
                    "OR payload->>'agent_id' IS NOT NULL "
                    "OR payload->>'run_id' IS NOT NULL"
                )
                seen: set[tuple[str | None, str | None, str | None]]
                seen = set()
                for user_id, agent_id, run_id in rows:
                    key = (user_id, agent_id, run_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    record: dict[str, Any] = {}
                    if user_id:
                        record["user_id"] = user_id
                    if agent_id:
                        record["agent_id"] = agent_id
                    if run_id:
                        record["run_id"] = run_id
                    records.append(record)
                logger.debug(
                    "list_scopes: postgres fallback returned %d records",
                    len(records),
                )
            except Exception as exc:
                logger.warning(
                    "list_scopes: postgres fallback failed: %s", exc
                )

            if not records:
                logger.warning(
                    "list_scopes: all strategies exhausted — returning empty scopes"
                )

        users: set[str] = set()
        agents: set[str] = set()
        runs: set[str] = set()
        projects: set[str] = set()

        for record in records:
            if not isinstance(record, dict):
                continue
            uid = record.get("user_id")
            if isinstance(uid, str) and uid:
                users.add(uid)
            aid = record.get("agent_id")
            if isinstance(aid, str) and aid:
                agents.add(aid)
            rid = record.get("run_id")
            if isinstance(rid, str) and rid:
                runs.add(rid)
            # Also check metadata for project identifiers
            metadata = record.get("metadata")
            if isinstance(metadata, dict):
                for meta_key in ("project", "project_id"):
                    meta_val = metadata.get(meta_key)
                    if isinstance(meta_val, str) and meta_val:
                        projects.add(meta_val)

        return AdminScopesResponse(
            users=sorted(users),
            agents=sorted(agents),
            runs=sorted(runs),
            projects=sorted(projects),
        )

    # -- write paths (these own audit stamping) ---------------------------

    def create_memory(
        self,
        memory_instance: Any,
        payload: AdminMemoryCreateRequest,
    ) -> dict[str, Any]:
        """Create a memory and stamp ``impersonated_by=admin`` on its metadata.

        Args:
            memory_instance: The mem0 memory instance to write to.
            payload: Validated :class:`AdminMemoryCreateRequest`.

        Returns:
            A dict shaped like :class:`AdminMemoryCreateResponse` with the
            stored fields plus ``impersonated_by="admin"``.

        Raises:
            ValueError: When ``scope``/``scope_id`` are missing or the
                underlying memory instance rejects the write.
        """
        scope = payload.scope
        scope_id = payload.scope_id
        if not scope or not scope_id:
            raise ValueError("scope and scope_id are required for admin create")
        scope_param = _scope_param_name(scope)
        prepared_metadata = self._stamp_write_metadata(payload.metadata)
        trace_backend_operation(
            "admin.create_memory",
            scope=scope,
            message_count=len(payload.messages),
            impersonator=ADMIN_IMPERSONATOR,
        )
        result = memory_instance.add(
            messages=[message.model_dump() for message in payload.messages],
            metadata=prepared_metadata,
            **{scope_param: scope_id},
        )
        memory_id = self._extract_memory_id(result)
        return {
            "memory_id": memory_id,
            "scope": scope,
            "scope_id": scope_id,
            "messages": list(payload.messages),
            "metadata": prepared_metadata,
            "impersonated_by": ADMIN_IMPERSONATOR,
        }

    def update_memory(
        self,
        memory_instance: Any,
        memory_id: str,
        payload: AdminMemoryUpdateRequest,
    ) -> dict[str, Any]:
        """Update a memory and re-stamp ``impersonated_by=admin`` on metadata.

        The audit stamp is re-applied so the latest editor is always visible,
        and any prior ``copied_from`` provenance is preserved when the new
        metadata does not include an explicit override.

        Args:
            memory_instance: The mem0 memory instance to write to.
            memory_id: Identifier of the memory to update.
            payload: Validated :class:`AdminMemoryUpdateRequest`.

        Returns:
            A dict shaped like :class:`AdminMemoryDetailResponse` with the
            updated content, metadata, popularity/freshness aggregates, and
            the audit block.

        Raises:
            ValueError: When the memory does not exist.
        """
        existing = memory_instance.get(memory_id)
        if existing is None:
            raise ValueError(f"memory {memory_id!r} not found")
        existing_metadata = self._safe_metadata(existing)
        carried_copied_from = self._extract_copied_from(existing_metadata)
        prepared_metadata = self._stamp_write_metadata(
            payload.metadata, copied_from=carried_copied_from
        )
        trace_backend_operation(
            "admin.update_memory",
            memory_id=memory_id,
            has_messages=bool(payload.messages),
            impersonator=ADMIN_IMPERSONATOR,
        )
        updated = memory_instance.update(
            memory_id=memory_id,
            data={
                "messages": [message.model_dump() for message in payload.messages],
                "metadata": prepared_metadata,
            },
        )

        # mem0's update() may return None, a string, or a dict with
        # unexpected shape (e.g. {"message": "...", "id": "..."})
        # instead of the full record.  When the response is not a
        # usable record dict, re-fetch via get().
        if not isinstance(updated, dict) or "messages" not in updated:
            fetched = memory_instance.get(memory_id)
            if fetched is None:
                raise ValueError(
                    f"memory {memory_id!r} not found after update"
                )
            updated = fetched

        try:
            record = self._anchor_service.normalize_record(updated)
            return self._assemble_detail_item(record)
        except Exception as exc:
            raise ValueError(
                f"failed to assemble detail for memory {memory_id!r} "
                f"after update: {exc}"
            ) from exc

    def delete_memory(
        self, memory_instance: Any, memory_id: str
    ) -> dict[str, Any]:
        """Delete a memory by id.

        Args:
            memory_instance: The mem0 memory instance to write to.
            memory_id: Identifier of the memory to delete.

        Returns:
            A dict shaped like :class:`AdminMemoryDeleteResponse` with
            ``memory_id`` and ``deleted=True``.

        Raises:
            ValueError: When the memory does not exist.
        """
        existing = memory_instance.get(memory_id)
        if existing is None:
            raise ValueError(f"memory {memory_id!r} not found")
        trace_backend_operation("admin.delete_memory", memory_id=memory_id)
        memory_instance.delete(memory_id=memory_id)
        return {"memory_id": memory_id, "deleted": True}

    def copy_memory(
        self,
        memory_instance: Any,
        source_memory_id: str,
        payload: AdminMemoryCopyRequest,
    ) -> dict[str, Any]:
        """Copy a memory into a new scope and stamp copy provenance.

        Read-source → create-target semantics per the locked CMS contract:
        the source memory is **not** mutated, deleted, or rebound.  A new
        memory is created in the target scope with the source's messages
        and a deep-copied metadata dict.  The new memory is stamped with
        both ``impersonated_by=admin`` and a
        ``copied_from={ source_memory_id, source_scope, source_scope_id }``
        provenance object built from the source's inferred scope.

        Compare with :meth:`create_memory` (no source) and
        :meth:`update_memory` (mutates an existing record) — copy is the
        only flow that introduces a new memory under a different scope
        while preserving the source.

        Args:
            memory_instance: The mem0 memory instance to write to.
            source_memory_id: Identifier of the memory being copied.
            payload: Validated :class:`AdminMemoryCopyRequest` carrying
                ``target_scope`` and ``target_scope_id``.

        Returns:
            A dict shaped like :class:`AdminMemoryCopyResponse` with the
            new target id, the provenance object, and the impersonation
            stamp.

        Raises:
            ValueError: When the source memory does not exist, the target
                scope is missing, or the underlying memory instance rejects
                the write.  The source memory is guaranteed to be
                unchanged in all of these failure paths (the new write is
                only attempted after the source has been read).
        """
        source_record = memory_instance.get(source_memory_id)
        if source_record is None:
            raise ValueError(f"memory {source_memory_id!r} not found")
        source_metadata = self._safe_metadata(source_record)
        source_scope, source_scope_id = self._extract_scope_from_record(
            source_record
        )
        if not source_scope_id:
            source_scope, source_scope_id = self._infer_source_scope(
                source_metadata
            )
        copied_from = CopiedFromInfo(
            source_memory_id=source_memory_id,
            source_scope=source_scope,
            source_scope_id=source_scope_id,
        )

        target_scope = payload.target_scope
        target_scope_id = payload.target_scope_id
        if not target_scope or not target_scope_id:
            raise ValueError("target_scope and target_scope_id are required")

        target_param = _scope_param_name(target_scope)
        target_metadata = self._stamp_write_metadata(
            source_metadata, copied_from=copied_from
        )
        messages = self._extract_source_messages(source_record)
        trace_backend_operation(
            "admin.copy_memory",
            source_memory_id=source_memory_id,
            target_scope=target_scope,
            impersonator=ADMIN_IMPERSONATOR,
        )
        result = memory_instance.add(
            messages=messages,
            metadata=target_metadata,
            **{target_param: target_scope_id},
        )
        target_memory_id = self._extract_memory_id(
            self._anchor_service.normalize_record(result)
            if isinstance(result, dict)
            else result
        )
        return {
            "source_memory_id": source_memory_id,
            "target_memory_id": target_memory_id,
            "target_scope": target_scope,
            "target_scope_id": target_scope_id,
            "copied_from": copied_from,
            "impersonated_by": ADMIN_IMPERSONATOR,
        }

    # -- read paths (placeholders filled by Task 5) -----------------------

    def list_memories(
        self,
        memory_instance: Any,
        *,
        scope: ScopeType | None = None,
        scope_id: str | None = None,
        page: int,
        page_size: int,
        query: str | None = None,
    ) -> dict[str, Any]:
        """List memories with normalized aggregates.

        When both ``scope`` and ``scope_id`` are provided the listing is
        scoped to that particular user/agent/run.  When either is omitted
        the method returns **all** memories across all scopes, with each
        item carrying its own ``scope``/``scope_id`` inferred from the
        stored record's top-level fields.

        The returned records are passed through :class:`AnchorService` for
        metadata normalization so the CMS always sees a stable, anchor-aware
        shape.  Per-memory popularity and freshness are computed from the
        dedicated visit store and attached as Pydantic models.

        When *query* is provided the listing is filtered to memories whose
        extracted ``content`` contains the query as a case-insensitive
        substring (whitespace-trimmed).  An empty/whitespace-only query
        is treated as no filter.  The filter is applied **before** paging
        so ``total_items`` / ``total_pages`` reflect the filtered count.

        Args:
            memory_instance: The mem0 memory instance to read from.
            scope: ``"user"`` / ``"agent"`` / ``"run"``.  When omitted
                all scopes are returned.
            scope_id: Identifier within the chosen scope.  When omitted
                all scopes are returned.
            page: 1-indexed page number; must be ``>= 1``.
            page_size: Items per page; must be ``>= 1``.
            query: Optional case-insensitive substring filter applied to
                the extracted memory content; ``None`` or whitespace-only
                means no filter.

        Returns:
            A dict with ``items``, ``page``, ``page_size``, ``total_items``,
            and ``total_pages`` keys.  Each item carries ``memory_id``,
            ``scope``, ``scope_id``, ``content``, ``metadata``,
            ``popularity`` (:class:`AdminPopularityInfo`) and ``freshness``
            (:class:`AdminFreshnessInfo`) fields.

        Raises:
            ValueError: When ``page`` or ``page_size`` is invalid.
        """
        if page < 1:
            raise ValueError("page must be >= 1")
        if page_size < 1:
            raise ValueError("page_size must be >= 1")

        normalized_query = (query or "").strip()
        scope_provided = scope is not None and scope_id is not None and bool(scope_id)

        if scope_provided:
            scope_param = _scope_param_name(scope)  # type: ignore[arg-type]
            trace_backend_operation(
                "admin.list_memories",
                scope=scope,
                scope_param=scope_param,
                page=page,
                page_size=page_size,
                has_query=bool(normalized_query),
            )
            raw_records = memory_instance.get_all(**{scope_param: scope_id})
        else:
            trace_backend_operation(
                "admin.list_memories",
                scope=None,
                page=page,
                page_size=page_size,
                has_query=bool(normalized_query),
            )
            try:
                raw_records = memory_instance.get_all()
            except Exception:
                raw_records = []
            # Fall back to PostgreSQL if mem0 get_all() returned nothing
            if not raw_records:
                try:
                    rows = self._postgres_fallback_query(
                        "SELECT payload FROM {table}"
                    )
                    raw_records = [
                        row[0] for row in rows
                        if isinstance(row[0], dict)
                    ] if rows else []
                except Exception:
                    raw_records = []

        # Unwrap dict-wrapped response from some mem0 backends
        if isinstance(raw_records, dict):
            raw_records = raw_records.get("results") or raw_records.get("data") or []

        items = self._anchor_service.normalize_payload(raw_records) or []
        if not isinstance(items, list):
            items = list(items) if items else []

        if normalized_query:
            needle = normalized_query.casefold()
            def _matches(record: Any) -> bool:
                if needle in self._extract_content(record).casefold():
                    return True
                metadata = self._safe_metadata(record)
                if metadata:
                    for value in metadata.values():
                        if isinstance(value, str) and needle in value.casefold():
                            return True
                return False
            items = [record for record in items if _matches(record)]

        total_items = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = items[start:end]
        total_pages = (
            (total_items + page_size - 1) // page_size if total_items else 0
        )

        max_total = self._visit_store.max_total_visits()
        aggregates = self._visit_store.get_aggregates_for_memories(
            self._extract_memory_ids(page_items)
        )

        if scope_provided:
            assembled_items = [
                self._assemble_list_item(
                    record=record,
                    scope=scope,  # type: ignore[arg-type]
                    scope_id=scope_id,  # type: ignore[arg-type]
                    aggregates_by_id=aggregates,
                    max_total=max_total,
                )
                for record in page_items
            ]
        else:
            assembled_items = []
            for record in page_items:
                item_scope, item_scope_id = self._extract_scope_from_record(record)
                assembled_items.append(
                    self._assemble_list_item(
                        record=record,
                        scope=item_scope,
                        scope_id=item_scope_id,
                        aggregates_by_id=aggregates,
                        max_total=max_total,
                    )
                )

        return {
            "items": assembled_items,
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
        }

    def get_memory(self, memory_instance: Any, memory_id: str) -> dict[str, Any]:
        """Scaffold: return a detail payload for *memory_id*.

        Args:
            memory_instance: The mem0 memory instance to read from.
            memory_id: Identifier of the memory to look up.

        Returns:
            A dict shaped like :class:`AdminMemoryDetailResponse` with
            ``memory_id``, ``scope``, ``scope_id``, ``content``,
            ``metadata``, ``popularity``, ``freshness``, and ``audit``
            fields.

        Raises:
            ValueError: When the memory does not exist.
        """
        raw = memory_instance.get(memory_id)
        if raw is None:
            raise ValueError(f"memory {memory_id!r} not found")
        record = (
            self._anchor_service.normalize_record(raw)
            if isinstance(raw, dict)
            else raw
        )
        return self._assemble_detail_item(record)

    # -- visit path (delegates to VisitStore) ----------------------------

    def record_visit(
        self,
        memory_instance: Any,
        memory_id: str,
        reason: VisitReason,
    ) -> dict[str, Any]:
        """Record a visit event through the dedicated :class:`VisitStore`.

        Unlike the public :meth:`MemoryService.get` flow, this method writes
        a row to the dedicated visit telemetry SQLite database (and updates
        the matching aggregate) instead of mutating the mem0 metadata.  The
        underlying memory is **not** mutated; this method only checks that
        it exists before writing to the visit store.

        The visit write is idempotent in the count sense only — each call
        adds exactly one visit event, and repeated calls monotonically
        increase ``total_visits`` and refresh ``last_visited_at`` (see
        :meth:`services.visit_store.VisitStore.record_visit` for the full
        predictability contract).

        Args:
            memory_instance: The mem0 memory instance — used to confirm the
                memory exists before recording the visit.  The memory itself
                is not mutated.
            memory_id: Identifier of the memory being visited.
            reason: Closed ``VisitReason`` literal
                (``"detail_open"`` / ``"edit_save"`` / ``"copy_source"``).

        Returns:
            A dict shaped like :class:`AdminMemoryVisitResponse` with
            ``memory_id``, ``total_visits``, ``last_visited_at``, and
            ``reason`` fields.

        Raises:
            ValueError: When the memory does not exist.
        """
        existing = memory_instance.get(memory_id)
        if existing is None:
            raise ValueError(f"memory {memory_id!r} not found")
        trace_backend_operation(
            "admin.record_visit", memory_id=memory_id, reason=reason
        )
        aggregates = self._visit_store.record_visit(memory_id, reason=reason)
        return {
            "memory_id": memory_id,
            "total_visits": aggregates.total_visits,
            "last_visited_at": aggregates.last_visited_at,
            "reason": reason,
        }

    # -- index overview (placeholder filled by Task 9) --------------------

    def index_overview(
        self,
        indexing_service: Any,
        job_service: Any,
        watch_service: Any | None = None,
    ) -> AdminIndexOverviewResponse:
        """Return an :class:`AdminIndexOverviewResponse` for the current state.

        Aggregates the **current server-process** state from existing
        indexing, job, watch, and file services.  The result is *not*
        a durable manifest — the ``limits.current_process_state_only``
        flag in the response signals this explicitly so the CMS can
        present it as a live view rather than historical data.

        Truthful-current-process contract:

        * ``roots`` — derived from the in-memory manifest.  When the
          manifest is empty, the function falls back to the indexing
          service status to surface roots that have run a sync but
          have not been materialised into the manifest yet.
        * ``watcher_active`` — sourced from :class:`WatchService`
          (when available) and **not** from the manifest's
          ``RootManifest.watching`` field, which is only mutated
          indirectly.  Falls back to ``False`` when no watch service
          is reachable.
        * ``files`` — derived from the in-memory manifest records
          (one row per ``(root, file_path)`` pair).  ``language`` is
          derived from the file extension via
          :meth:`FileScanner.language_for` and ``has_summary_embedding``
          is computed against the live corpus via
          :meth:`IndexingService.file_has_summary_embedding`.  When
          the manifest has no entries the file list is empty.
        * ``jobs`` — most-recent jobs (newest first, capped at 20)
          from the background job service.
        * ``visibility_inputs.chunk_count`` — sourced from the corpus
          status when available, otherwise derived from the sum of
          per-root chunk counts.

        Args:
            indexing_service: The live :class:`IndexingService` instance
                (used for both manifest and corpus access).
            job_service: The live :class:`BackgroundJobService` instance
                used to source the ``jobs`` list.
            watch_service: Optional :class:`WatchService` instance used
                to populate ``watcher_active``.  ``None`` (or a service
                without an ``is_watching`` method) results in
                ``watcher_active=False`` for every root.

        Returns:
            An :class:`AdminIndexOverviewResponse` aggregating the
            current server-known state.  The ``limits`` field is
            always ``{"current_process_state_only": true}``.
        """
        trace_backend_operation("admin.index_overview")

        manifest_status = self._safe_manifest_status(indexing_service)
        indexing_status = self._safe_indexing_status(indexing_service)

        roots = self._collect_roots(
            manifest_status=manifest_status,
            indexing_status=indexing_status,
            job_service=job_service,
            watch_service=watch_service,
        )

        jobs = self._collect_recent_jobs(job_service, limit=20)
        files = self._collect_file_records(indexing_service)
        total_files = self._total_files(indexing_service, manifest_status)
        total_chunks = self._total_chunks(indexing_service, indexing_status, roots)

        return AdminIndexOverviewResponse(
            roots=roots,
            jobs=jobs,
            files=files,
            limits=AdminIndexLimits(current_process_state_only=True),
            visibility_inputs=AdminIndexVisibilityInputs(
                generated_at=_now_iso(),
                root_count=len(roots),
                file_count=total_files,
                chunk_count=total_chunks,
            ),
        )

    # -- internal helpers -------------------------------------------------

    @staticmethod
    def _postgres_fallback_query(query: str) -> list[tuple[Any, ...]]:
        """Execute a PostgreSQL query and return the fetched rows.

        Connects to the PostgreSQL database using environment variables
        for configuration (``POSTGRES_HOST``, ``POSTGRES_PORT``,
        ``POSTGRES_DB``, ``POSTGRES_USER``, ``POSTGRES_PASSWORD``) and
        substitutes ``{table}`` in the query with the
        ``POSTGRES_COLLECTION`` table name.

        Args:
            query: SQL query string.  Use ``{table}`` as a placeholder
                for the configured collection table name.

        Returns:
            A list of row tuples from the query result.

        Raises:
            ImportError: When neither ``psycopg2`` nor ``psycopg`` is
                installed.
            Exception: Any PostgreSQL connection or query error.
        """
        try:
            import psycopg2  # type: ignore[import-untyped]
        except ImportError:
            import psycopg as psycopg2  # type: ignore[import-untyped,no-redef]

        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB", "postgres"),
            user=os.environ.get("POSTGRES_USER", "postgres"),
            password=os.environ.get("POSTGRES_PASSWORD", "postgres"),
        )
        table = os.environ.get("POSTGRES_COLLECTION", "mem0_memories")
        try:
            with conn.cursor() as cur:
                cur.execute(query.format(table=table))
                return cur.fetchall()
        finally:
            conn.close()

    def _assemble_list_item(
        self,
        *,
        record: dict[str, Any],
        scope: ScopeType,
        scope_id: str,
        aggregates_by_id: dict[str, VisitAggregates],
        max_total: int,
    ) -> dict[str, Any]:
        """Build a list-item payload from a raw memory record.

        The shape mirrors :class:`AdminMemoryListItem` in
        :mod:`api_models`: identity, scope, content, metadata, and the
        popularity/freshness raw fields the CMS needs for display.

        Separation guarantee (Task 6): the returned ``popularity`` and
        ``freshness`` blocks are derived from disjoint sources — visit
        telemetry for popularity, ``last_visited_at`` /
        ``created_at`` / ``decay_half_life_days`` / ``ttl_expires_at``
        for freshness — and **never** collapse into a single combined
        score.  See :class:`AdminPopularityInfo` and
        :class:`AdminFreshnessInfo` for the wire shapes and the contrast
        between the two blocks.

        Args:
            record: A normalized mem0 record (post
                :meth:`AnchorService.normalize_payload`).
            scope: Admin scope type echoed into the payload.
            scope_id: Admin scope identifier echoed into the payload.
            aggregates_by_id: Pre-fetched :class:`VisitAggregates` map for
                the page (computed once per call to avoid per-row lookups).
            max_total: The peak ``total_visits`` across all memories
                (denominator for ``visit_ratio``).

        Returns:
            A dict shaped like :class:`AdminMemoryListItem`.
        """
        memory_id = self._extract_memory_id(record)
        aggregate = aggregates_by_id.get(
            memory_id,
            VisitAggregates(
                memory_id=memory_id,
                total_visits=0,
                last_visited_at=None,
            ),
        )
        metadata = self._safe_metadata(record)
        return {
            "memory_id": memory_id,
            "scope": scope,
            "scope_id": scope_id,
            "content": self._extract_content(record),
            "metadata": metadata,
            "popularity": self._popularity_payload(aggregate, max_total),
            "freshness": self._freshness_payload(aggregate, metadata),
        }

    def _assemble_detail_item(self, record: dict[str, Any]) -> dict[str, Any]:
        """Build a detail-item payload from a raw memory record.

        The shape mirrors :class:`AdminMemoryDetailResponse` in
        :mod:`api_models`: identity, scope, content, metadata,
        popularity/freshness raw fields, plus an :class:`AdminAuditInfo`
        block that exposes ``impersonated_by`` and ``copied_from``
        provenance for admin-initiated writes.

        The scope/scope_id are **inferred from the stored metadata** (not
        the request path) so the detail payload is consistent with the
        way the memory was actually persisted.

        Separation guarantee (Task 6): ``popularity`` comes from the
        dedicated visit store, ``freshness`` reads decay inputs from the
        memory's metadata, and ``never_visited`` flows through the
        freshness block as a first-class field — clients do not have to
        null-check ``last_visited_at`` to keep never-visited memories
        cold.  No combined score is computed at this layer.

        Args:
            record: A normalized mem0 record (post
                :meth:`AnchorService.normalize_payload`).

        Returns:
            A dict shaped like :class:`AdminMemoryDetailResponse`.
        """
        memory_id = self._extract_memory_id(record)
        metadata = self._safe_metadata(record)
        scope, scope_id = self._extract_scope_from_record(record)
        if not scope_id:
            scope, scope_id = self._infer_source_scope(metadata)
        aggregate = self._visit_store.get_aggregates(memory_id)
        max_total = self._visit_store.max_total_visits()
        return {
            "memory_id": memory_id,
            "scope": scope,
            "scope_id": scope_id,
            "content": self._extract_content(record),
            "metadata": metadata,
            "popularity": self._popularity_payload(aggregate, max_total),
            "freshness": self._freshness_payload(aggregate, metadata),
            "audit": AdminAuditInfo(
                impersonated_by=self._extract_impersonated_by(metadata),
                copied_from=self._extract_copied_from(metadata),
            ),
        }

    def _stamp_write_metadata(
        self,
        metadata: dict[str, Any] | None,
        *,
        copied_from: CopiedFromInfo | None = None,
    ) -> dict[str, Any]:
        """Stamp ``impersonated_by=admin`` and (optionally) ``copied_from``.

        Args:
            metadata: Existing metadata to copy and stamp; ``None`` becomes
                an empty dict.
            copied_from: Provenance object to attach when present.  When
                ``None`` the existing ``copied_from`` key (if any) on
                *metadata* is preserved verbatim.

        Returns:
            A new dict (deep-copied) with the audit stamp applied.
        """
        prepared: dict[str, Any] = deepcopy(metadata) if metadata else {}
        prepared[_IMPERSONATED_BY_METADATA_KEY] = ADMIN_IMPERSONATOR
        if copied_from is not None:
            prepared[_COPIED_FROM_METADATA_KEY] = copied_from.model_dump()
        return prepared

    @staticmethod
    def _popularity_payload(
        aggregate: VisitAggregates, max_total: int
    ) -> AdminPopularityInfo:
        """Build a :class:`AdminPopularityInfo` payload from a store aggregate.

        The :class:`AdminPopularityInfo` shape is reused across list-item,
        detail, and copy responses; returning a Pydantic instance keeps
        callers (FastAPI response models, CMS clients) and tests consistent.

        ``visit_ratio`` is computed as ``total_visits / max_total`` capped at
        ``1.0``.  When the store has no recorded visits ``max_total`` is
        ``0`` and the ratio is reported as ``0.0``.  The ratio is a
        *popularity* signal only — it carries no decay information and is
        intentionally distinct from the freshness block.

        Compare with :meth:`_freshness_payload`, which returns the
        decay-input block; the two helpers are deliberately not combined
        and the resulting blocks do not share fields.

        Args:
            aggregate: The :class:`VisitAggregates` row for the memory.
            max_total: The highest ``total_visits`` value across all memories
                in the visit store (denominator for the ratio).

        Returns:
            An :class:`AdminPopularityInfo` Pydantic model.
        """
        if max_total <= 0:
            visit_ratio = 0.0
        else:
            visit_ratio = min(1.0, aggregate.total_visits / max_total)
        return AdminPopularityInfo(
            total_visits=aggregate.total_visits,
            visit_ratio=visit_ratio,
        )

    @staticmethod
    def _freshness_payload(
        aggregate: VisitAggregates,
        metadata: dict[str, Any] | None,
    ) -> AdminFreshnessInfo:
        """Build an :class:`AdminFreshnessInfo` payload from store + metadata.

        Reused across list-item, detail, and copy responses; returning a
        Pydantic instance keeps the contract shape uniform.

        The CMS uses these raw fields to compute recency/decay display
        values using the plugin-authority formulas from
        ``~/.config/opencode/plugins/mem0-functional.ts`` — this helper
        does not compute a combined plugin score.

        ``never_visited`` is copied from the aggregate so the wire shape
        exposes an explicit "cold" flag — never-visited memories must
        never be inferred client-side from a null ``last_visited_at``
        (the freshness block is the only authority the CMS reads).

        Compare with :meth:`_popularity_payload`, which returns the
        visit-telemetry block; the two helpers are deliberately not
        combined and the resulting blocks do not share fields.

        Args:
            aggregate: The :class:`VisitAggregates` row for the memory.
            metadata: The memory's metadata dict (or ``None``); used to
                read the decay-input fields ``created_at``,
                ``decay_half_life_days``, and ``ttl_expires_at``.

        Returns:
            An :class:`AdminFreshnessInfo` Pydantic model.
        """
        return AdminFreshnessInfo(
            last_visited_at=aggregate.last_visited_at,
            never_visited=aggregate.never_visited,
            created_at=_extract_created_at(metadata),
            decay_half_life_days=_extract_decay_half_life_days(metadata),
            ttl_expires_at=_extract_ttl_expires_at(metadata),
        )

    @staticmethod
    def _safe_metadata(record: Any) -> dict[str, Any] | None:
        """Return ``record['metadata']`` as a dict, or ``None`` when missing."""
        if not isinstance(record, dict):
            return None
        metadata = record.get("metadata")
        return metadata if isinstance(metadata, dict) else None

    @staticmethod
    def _extract_memory_id(record: Any) -> str:
        """Extract the ``memory_id``/``id`` field from a raw record."""
        if isinstance(record, str):
            return record
        if not isinstance(record, dict):
            return ""
        for key in ("id", "memory_id"):
            value = record.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    @staticmethod
    def _extract_memory_ids(records: list[Any]) -> list[str]:
        """Return the memory ids from a list of raw records."""
        ids: list[str] = []
        for record in records or []:
            memory_id = AdminService._extract_memory_id(record)
            if memory_id:
                ids.append(memory_id)
        return ids

    @staticmethod
    def _extract_content(record: Any) -> str:
        """Return a stable text representation of the memory's content."""
        if not isinstance(record, dict):
            return ""
        if isinstance(record.get("memory"), str):
            return record["memory"]
        if isinstance(record.get("content"), str):
            return record["content"]
        messages = record.get("messages")
        if isinstance(messages, list) and messages:
            parts: list[str] = []
            for message in messages:
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        parts.append(content)
            return "\n".join(parts)
        return ""

    @staticmethod
    def _extract_scope_from_record(
        record: dict[str, Any],
    ) -> tuple[ScopeType, str]:
        """Extract ``(scope, scope_id)`` from a memory record's top-level fields.

        Mem0 stores scope identifiers (``user_id``, ``agent_id``, ``run_id``)
        as top-level fields on each record.  This helper reads them back so
        the admin list can display per-memory scope info even when no
        global scope filter was applied.

        Args:
            record: A normalized mem0 record (post
                :class:`AnchorService` normalisation).

        Returns:
            A ``(scope, scope_id)`` pair.  Falls back to ``("user", "")``
            when the record has no recognised scope field.
        """
        if not isinstance(record, dict):
            return "user", ""
        for scope, key in (
            ("user", "user_id"),
            ("agent", "agent_id"),
            ("run", "run_id"),
        ):
            value = record.get(key)
            if isinstance(value, str) and value:
                return scope, value  # type: ignore[return-value]
        return "user", ""

    @staticmethod
    def _extract_source_messages(record: Any) -> list[dict[str, str]]:
        """Return copy-able messages from a source record, or a single blank.

        mem0 stores memories as a list of ``messages``; when the source
        record does not carry them (e.g. legacy / read-only paths) we fall
        back to the textual ``memory`` field so the copy still preserves
        content.
        """
        if not isinstance(record, dict):
            return [{"role": "user", "content": ""}]
        messages = record.get("messages")
        if isinstance(messages, list) and messages:
            normalized: list[dict[str, str]] = []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                content = message.get("content")
                if isinstance(content, str):
                    normalized.append(
                        {
                            "role": role if isinstance(role, str) else "user",
                            "content": content,
                        }
                    )
            if normalized:
                return normalized
        memory = record.get("memory") or record.get("content")
        if isinstance(memory, str) and memory:
            return [{"role": "user", "content": memory}]
        return [{"role": "user", "content": ""}]

    @staticmethod
    def _infer_source_scope(
        metadata: dict[str, Any] | None,
    ) -> tuple[ScopeType, str]:
        """Infer a ``(scope, scope_id)`` pair from a memory's metadata.

        Memories created through the public API do not store a scope in
        metadata; this helper falls back to the safest default
        (``("user", "")``) so callers that need a real identifier can
        detect the empty id and refuse the copy.
        """
        if not isinstance(metadata, dict):
            return "user", ""
        for scope, key in (
            ("user", "user_id"),
            ("agent", "agent_id"),
            ("run", "run_id"),
        ):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return scope, value  # type: ignore[return-value]
        return "user", ""

    @staticmethod
    def _extract_impersonated_by(metadata: dict[str, Any] | None) -> str | None:
        """Return the ``impersonated_by`` stamp from metadata, or ``None``."""
        if not isinstance(metadata, dict):
            return None
        value = metadata.get(_IMPERSONATED_BY_METADATA_KEY)
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _extract_copied_from(
        metadata: dict[str, Any] | None,
    ) -> CopiedFromInfo | None:
        """Reconstruct a :class:`CopiedFromInfo` from stored metadata, if any."""
        if not isinstance(metadata, dict):
            return None
        raw = metadata.get(_COPIED_FROM_METADATA_KEY)
        if not isinstance(raw, dict):
            return None
        try:
            return CopiedFromInfo.model_validate(raw)
        except Exception:
            return None

    @staticmethod
    def _latest_job_for_root(job_service: Any, root: str) -> str | None:
        """Return the most-recent job id that touched *root*, if any."""
        if job_service is None or not hasattr(job_service, "list_jobs"):
            return None
        try:
            jobs = job_service.list_jobs(limit=50)
        except Exception:
            return None
        for job in jobs or []:
            result = job.get("result") if isinstance(job, dict) else None
            if isinstance(result, dict) and result.get("root") == root:
                job_id = job.get("job_id")
                if isinstance(job_id, str):
                    return job_id
        return None

    @staticmethod
    def _collect_recent_jobs(
        job_service: Any, *, limit: int
    ) -> list[AdminIndexJobInfo]:
        """Project background jobs into the admin index schema."""
        if job_service is None or not hasattr(job_service, "list_jobs"):
            return []
        try:
            jobs = job_service.list_jobs(limit=limit)
        except Exception:
            return []
        out: list[AdminIndexJobInfo] = []
        for job in jobs or []:
            if not isinstance(job, dict):
                continue
            out.append(
                AdminIndexJobInfo(
                    job_id=str(job.get("job_id", "")),
                    status=str(job.get("status", "unknown")),
                    queued_at=_as_iso_string(job.get("queued_at")),
                    started_at=_as_iso_string(job.get("started_at")),
                    completed_at=_as_iso_string(job.get("completed_at")),
                    result=job.get("result")
                    if isinstance(job.get("result"), dict)
                    else None,
                    errors=job.get("errors") or None,
                )
            )
        return out

    @staticmethod
    def _collect_file_records(indexing_service: Any) -> list[AdminIndexFileInfo]:
        """Project manifest file records into the admin index schema.

        Reads file-level state through the public
        :meth:`IndexingService.iter_manifest_files` accessor (added
        alongside :class:`FileCorpusService.has_summary_embedding`)
        rather than reaching into the private ``_manifest._files``
        dict and the (non-existent) ``_corpus.has_summary_embeddings``
        method.  ``language`` is derived from the file extension via
        :func:`services.file_scanner.language_for` so the response
        carries the same mapping :class:`FileScanner.scan` uses.

        Args:
            indexing_service: The live :class:`IndexingService`.  ``None``
                yields an empty list (consistent with the
                :class:`IndexingService` always being present at the
                route layer).

        Returns:
            A list of :class:`AdminIndexFileInfo` reflecting the
            current in-memory manifest.  Always empty when no
            indexing has happened in this process.
        """
        if indexing_service is None:
            return []
        iter_files = getattr(indexing_service, "iter_manifest_files", None)
        if not callable(iter_files):
            return []
        out: list[AdminIndexFileInfo] = []
        for file_key, file_record in list(iter_files()):  # type: ignore[union-attr]
            if not isinstance(file_key, str) or "\x00" not in file_key:
                continue
            root, _, file_path = file_key.partition("\x00")
            if not root or not file_path:
                continue
            chunk_count = (
                len(getattr(file_record, "chunk_ids", []) or [])
                if file_record is not None
                else 0
            )
            has_summary_embedding = bool(
                chunk_count
                and getattr(indexing_service, "file_has_summary_embedding", None)
                is not None
                and bool(
                    indexing_service.file_has_summary_embedding(root, file_path)
                )
            )
            out.append(
                AdminIndexFileInfo(
                    root=root,
                    file_path=file_path,
                    chunk_count=chunk_count,
                    language=_language_for(file_path),
                    last_indexed_at=_as_iso_string(
                        getattr(file_record, "last_indexed_at", None)
                    ),
                    has_summary_embedding=has_summary_embedding,
                )
            )
        return out

    @staticmethod
    def _safe_manifest_status(indexing_service: Any) -> dict[str, Any]:
        """Return the manifest status dict, or an empty stub if unavailable."""
        if indexing_service is None:
            return {"roots": {}, "total_files": 0}
        manifest = getattr(indexing_service, "_manifest", None)
        if manifest is None or not hasattr(manifest, "get_status"):
            return {"roots": {}, "total_files": 0}
        try:
            return manifest.get_status()
        except Exception:
            return {"roots": {}, "total_files": 0}

    @staticmethod
    def _safe_indexing_status(indexing_service: Any) -> dict[str, Any]:
        """Return the indexing-service status dict, or an empty stub."""
        if indexing_service is None or not hasattr(indexing_service, "status"):
            return {"roots": [], "total_chunks": 0}
        try:
            return indexing_service.status()
        except Exception:
            return {"roots": [], "total_chunks": 0}

    @staticmethod
    def _watcher_is_active(watch_service: Any, root: str) -> bool:
        """Return ``True`` if *watch_service* is actively watching *root*.

        The manifest's ``RootManifest.watching`` field is currently
        only ever the dataclass default — :class:`WatchService` is
        the actual source of truth and must be consulted directly.
        Returns ``False`` when *watch_service* is ``None`` or does
        not expose :meth:`WatchService.is_watching`.
        """
        if watch_service is None or not hasattr(watch_service, "is_watching"):
            return False
        try:
            return bool(watch_service.is_watching(root))
        except Exception:
            return False

    @staticmethod
    def _collect_roots(
        *,
        manifest_status: dict[str, Any],
        indexing_status: dict[str, Any],
        job_service: Any,
        watch_service: Any | None,
    ) -> list[AdminIndexRootInfo]:
        """Assemble the per-root rows for :class:`AdminIndexOverviewResponse`.

        The manifest is the primary source — it carries the per-root
        ``file_count`` and ``chunk_count``.  Roots that have only ever
        run a sync without populating the manifest are surfaced from
        the indexing-service status (the same fallback the previous
        implementation used) so the overview never silently drops a
        root the operator has interacted with.
        """
        roots: list[AdminIndexRootInfo] = []
        seen: set[str] = set()

        raw_roots = manifest_status.get("roots", {}) or {}
        for root_name, root_info in raw_roots.items():
            if not isinstance(root_name, str):
                continue
            info = root_info if isinstance(root_info, dict) else {}
            roots.append(
                AdminIndexRootInfo(
                    root=root_name,
                    total_files=int(info.get("file_count", 0) or 0),
                    total_chunks=int(info.get("chunk_count", 0) or 0),
                    watcher_active=AdminService._watcher_is_active(
                        watch_service, root_name
                    ),
                    last_job_id=AdminService._latest_job_for_root(
                        job_service=job_service, root=root_name
                    ),
                )
            )
            seen.add(root_name)

        for root_info in indexing_status.get("roots", []) or []:
            if not isinstance(root_info, dict):
                continue
            root_name = root_info.get("root_path")
            if not isinstance(root_name, str) or not root_name or root_name in seen:
                continue
            roots.append(
                AdminIndexRootInfo(
                    root=root_name,
                    total_files=0,
                    total_chunks=int(root_info.get("chunk_count", 0) or 0),
                    watcher_active=AdminService._watcher_is_active(
                        watch_service, root_name
                    ),
                    last_job_id=AdminService._latest_job_for_root(
                        job_service=job_service, root=root_name
                    ),
                )
            )
            seen.add(root_name)

        return roots

    @staticmethod
    def _total_files(
        indexing_service: Any, manifest_status: dict[str, Any]
    ) -> int:
        """Return the file count, preferring the manifest and falling back to the corpus."""
        manifest_total = int(manifest_status.get("total_files", 0) or 0)
        if manifest_total:
            return manifest_total
        if indexing_service is None or not hasattr(indexing_service, "_corpus"):
            return 0
        corpus = getattr(indexing_service, "_corpus", None)
        if corpus is None or not hasattr(corpus, "get_status"):
            return 0
        try:
            corpus_status = corpus.get_status()
        except Exception:
            return 0
        return int(corpus_status.get("total_files", 0) or 0)

    @staticmethod
    def _total_chunks(
        indexing_service: Any,
        indexing_status: dict[str, Any],
        roots: list[AdminIndexRootInfo],
    ) -> int:
        """Return the chunk count, preferring the corpus and falling back to per-root sum."""
        corpus_total = int(indexing_status.get("total_chunks", 0) or 0)
        if corpus_total:
            return corpus_total
        if indexing_service is None or not hasattr(indexing_service, "_corpus"):
            return sum(root.total_chunks for root in roots)
        corpus = getattr(indexing_service, "_corpus", None)
        if corpus is None or not hasattr(corpus, "get_status"):
            return sum(root.total_chunks for root in roots)
        try:
            corpus_status = corpus.get_status()
        except Exception:
            return sum(root.total_chunks for root in roots)
        return int(corpus_status.get("total_chunks", 0) or 0)


# ---------------------------------------------------------------------------
# Module-level helpers (reused by other services in future tasks)
# ---------------------------------------------------------------------------


def _extract_created_at(metadata: dict[str, Any] | None) -> str | None:
    """Return ``created_at`` from metadata, or ``None`` when not present."""
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("created_at")
    return value if isinstance(value, str) else None


def _extract_decay_half_life_days(
    metadata: dict[str, Any] | None,
) -> int | None:
    """Return ``decay_half_life_days`` from metadata, or ``None``."""
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("decay_half_life_days")
    if isinstance(value, bool):
        # Pydantic distinguishes bool from int; reject booleans.
        return None
    if isinstance(value, int) and value >= 1:
        return value
    return None


def _extract_ttl_expires_at(metadata: dict[str, Any] | None) -> str | None:
    """Return ``ttl_expires_at`` from metadata, or ``None``."""
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("ttl_expires_at")
    return value if isinstance(value, str) else None


def _language_for(file_path: str) -> str | None:
    """Map *file_path* to its detected language, or ``None`` when unsupported.

    Delegates to :meth:`FileScanner.language_for` so the admin
    overview surfaces the same language label the scanner uses
    during indexing.  Keeping this a thin wrapper avoids duplicating
    the extension-to-language mapping and lets the mapping evolve in
    one place.
    """
    return FileScanner.language_for(file_path)


__all__ = [
    "AdminService",
    "ADMIN_IMPERSONATOR",
]
