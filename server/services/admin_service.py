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
# mem0 2.0.0 response-shape adapters
# ---------------------------------------------------------------------------
#
# mem0 2.0.0 tightened the wire shape of several admin-facing methods:
#
# * ``get_all()`` now requires a ``filters`` dict (not top-level entity
#   keyword args) and returns ``{"results": [...]}`` instead of a bare list.
# * ``add()`` returns ``{"results": [...]}`` (a list of ``MemoryItem`` dicts)
#   instead of a single flat record dict.
# * ``update()`` returns ``{"message": "Memory updated successfully!"}`` and
#   no longer echoes the updated record; the caller must re-fetch via
#   ``get(memory_id)`` to read the post-update state.
#
# The helpers below centralise the *only* places that need to know about
# these shape changes so the rest of the service can keep operating on the
# pre-2.0.0 shapes it was written for.  Each helper accepts the union of
# return shapes seen across the mem0 1.x → 2.x boundary, so the existing
# ``_FakeMemory`` test doubles (which still emit the older flat shapes)
# remain valid.


def _unwrap_results(result: Any) -> list[Any]:
    """Normalise a mem0 ``get_all`` / list-style return into a flat list.

    mem0 2.0.0 wraps the result list in a ``{"results": [...]}`` dict (and
    historically some backends used ``{"data": [...]}``); older versions and
    several test doubles return the list directly.  This helper is the
    single place that knows about the wrapping so the rest of the service
    can iterate over a plain list.

    Args:
        result: The raw value returned by a mem0 list-style call.  May be
            ``None``, a list, a dict with a ``"results"`` or ``"data"`` key
            whose value is a list, or any other shape (returned as an empty
            list).

    Returns:
        The inner list, or an empty list when the input is missing or has
        an unrecognised shape.  Never raises — the caller is expected to
        treat an empty list as "no records available".
    """
    if result is None:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        inner = result.get("results")
        if isinstance(inner, list):
            return inner
        inner = result.get("data")
        if isinstance(inner, list):
            return inner
    return []


def _extract_memory_id_from_add_result(result: Any) -> str:
    """Extract a memory id from a mem0 ``add()`` response.

    mem0 2.0.0 returns ``{"results": [<memory item>, ...]}`` where each
    item carries ``"id"`` and ``"memory"`` keys.  Older versions returned
    the memory item directly.  This helper accepts both shapes (and
    nested variants) and returns the first non-empty id it finds.

    Args:
        result: The raw value returned by a mem0 ``add()`` call.

    Returns:
        The extracted id string, or an empty string when no id could be
        found.  Never raises.
    """
    if result is None:
        return ""
    if isinstance(result, dict):
        # 2.0.0: {"results": [<item>]}
        nested = result.get("results")
        if isinstance(nested, list) and nested:
            first = nested[0]
            if isinstance(first, dict):
                for key in ("id", "memory_id"):
                    value = first.get(key)
                    if isinstance(value, str) and value:
                        return value
        # Some backends wrap as {"memory": {...}}
        inner = result.get("memory")
        if isinstance(inner, dict):
            for key in ("id", "memory_id"):
                value = inner.get(key)
                if isinstance(value, str) and value:
                    return value
        # Flat record (older mem0, test doubles).
        for key in ("id", "memory_id"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
    if isinstance(result, str):
        return result
    return ""


def _normalize_update_response(
    updated: Any,
    memory_id: str,
    memory_instance: Any,
) -> dict[str, Any]:
    """Normalise a mem0 ``update()`` response into a usable record dict.

    mem0 2.0.0 returns ``{"message": "Memory updated successfully!"}``
    instead of echoing the updated record.  Older versions (and the
    in-test ``_FakeMemory.update``) returned a full record dict with
    ``"messages"`` and ``"metadata"`` fields.  Centralising the
    "either the response is a usable record or fall back to get()"
    branch here means :meth:`AdminService.update_memory` only needs to
    call the helper and trust its return value.

    Args:
        updated: The raw value returned by a mem0 ``update()`` call.
        memory_id: Identifier of the memory being updated.  Used to
            fall back to ``memory_instance.get(memory_id)`` when *updated*
            is not a usable record dict.
        memory_instance: The mem0 memory instance.  Only invoked when a
            re-fetch is required.

    Returns:
        A normalized record dict carrying at minimum ``"id"``.  Raises
        :class:`ValueError` when the memory cannot be located after the
        update — i.e. the write succeeded but the post-state is
        unreadable, which the route layer surfaces as ``400``.
    """
    if isinstance(updated, dict) and "messages" in updated:
        # Pre-2.0.0 / test-double shape: the response is the full record.
        return updated
    # mem0 2.0.0 (and any future "ack-only" shape): re-fetch.
    fetched = memory_instance.get(memory_id)
    if fetched is None:
        raise ValueError(f"memory {memory_id!r} not found after update")
    if not isinstance(fetched, dict):
        raise ValueError(
            f"memory {memory_id!r} returned an unexpected post-update shape"
        )
    return fetched


def _build_get_all_filters(scope: str, scope_id: str | None) -> dict[str, Any]:
    """Build a mem0 2.0.0 ``filters`` dict from an admin scope pair.

    mem0 2.0.0 rejects top-level entity keyword arguments and requires a
    ``filters`` dict that includes at least one of ``user_id``,
    ``agent_id``, or ``run_id``.  This helper is the single place that
    maps the admin ``(scope, scope_id)`` pair onto that dict so the rest
    of the service can stay agnostic of the mem0 2.0.0 contract change.

    Args:
        scope: One of ``"user"``, ``"agent"``, ``"run"``.
        scope_id: The scope identifier (e.g. user id).

    Returns:
        A ``filters`` dict ready to pass to ``memory_instance.get_all``.
    """
    if scope not in ("user", "agent", "run"):
        raise ValueError(f"unsupported scope: {scope!r}")
    key = f"{scope}_id"
    return {key: scope_id}


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
            except Exception as exc:
                logger.debug(
                    "list_scopes: strategy %r failed: %s", strategy_name, exc
                )
                continue
            unwrapped = _unwrap_results(result)
            if unwrapped:
                records = [r for r in unwrapped if isinstance(r, dict)]
                logger.debug(
                    "list_scopes: strategy %r returned %d records",
                    strategy_name,
                    len(records),
                )
                break
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
        memory_id = _extract_memory_id_from_add_result(result)
        if not memory_id:
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

        # mem0's update() return shape varies across versions and backends;
        # see :func:`_normalize_update_response` for the full compatibility
        # matrix.  The helper centralises the "either return a usable
        # record or fall back to get(memory_id)" branch.
        normalized = _normalize_update_response(
            updated, memory_id, memory_instance
        )

        try:
            record = self._anchor_service.normalize_record(normalized)
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

    def delete_empty_memories(self, memory_instance: Any) -> dict[str, Any]:
        trace_backend_operation("admin.delete_empty_memories")
        try:
            raw_records = memory_instance.get_all()
        except Exception:
            raw_records = []

        # Unwrap dict-wrapped response BEFORE checking emptiness so the
        # ``mem0 2.0.0`` ``{"results": [...]}`` shape is treated as a list.
        raw_records = _unwrap_results(raw_records) if isinstance(raw_records, dict) else raw_records
        if not raw_records:
            try:
                raw_records = self._load_postgres_fallback_records()
            except Exception:
                raw_records = []
        raw_records = _unwrap_results(raw_records) if isinstance(raw_records, dict) else raw_records

        records = self._anchor_service.normalize_payload(raw_records) or []
        if not isinstance(records, list):
            records = list(records) if records else []

        deleted_count = 0
        for record in records:
            if self._extract_content(record).strip() != "":
                continue
            memory_id = self._extract_memory_id(record)
            if not memory_id:
                continue
            try:
                memory_instance.delete(memory_id=memory_id)
            except Exception:
                logger.warning("Failed to delete empty memory %s", memory_id, exc_info=True)
                continue
            deleted_count += 1

        return {
            "deleted_count": deleted_count,
            "message": f"Deleted {deleted_count} empty memories",
        }

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
        target_memory_id = _extract_memory_id_from_add_result(result)
        if not target_memory_id:
            normalized_result = (
                self._anchor_service.normalize_record(result)
                if isinstance(result, dict)
                else result
            )
            target_memory_id = self._extract_memory_id(normalized_result)
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
        type: str | None = None,
        project: str | None = None,
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

        When *type* or *project* is provided the listing is further filtered
        by exact case-insensitive metadata matches. ``type`` matches
        ``metadata.type``; ``project`` matches either ``metadata.project`` or
        ``metadata.project_id``. Empty/whitespace-only values are treated as
        no filter. These metadata filters are also applied **before** paging.

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
            type: Optional case-insensitive exact filter on
                ``metadata.type``; ``None`` or whitespace-only means no
                filter.
            project: Optional case-insensitive exact filter on
                ``metadata.project`` or ``metadata.project_id``; ``None`` or
                whitespace-only means no filter.

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
        normalized_type = (type or "").strip()
        normalized_project = (project or "").strip()
        scope_provided = scope is not None and scope_id is not None and bool(scope_id)

        # ``scope_provided`` is the guard: when it is true, both
        # ``scope`` and ``scope_id`` are non-None.  Pre-bind the
        # narrowed copies here so the two ``if scope_provided:``
        # branches below are statically type-safe; the asserts also
        # act as a runtime safety net if a future refactor breaks the
        # invariant (failing fast with a clear message rather than
        # calling mem0 with ``None``).
        narrowed_scope: ScopeType | None = scope if scope_provided else None
        narrowed_scope_id: str | None = scope_id if scope_provided else None

        if scope_provided:
            # The asserts narrow ``narrowed_scope`` / ``narrowed_scope_id``
            # to their non-None types for the call sites below; they
            # also act as a runtime safety net on the ``scope_provided``
            # invariant (the original ``scope``/``scope_id`` are
            # already known to be non-None by the boolean expression
            # that computed ``scope_provided``).
            assert narrowed_scope is not None
            assert narrowed_scope_id is not None
            scope_param = _scope_param_name(narrowed_scope)
            trace_backend_operation(
                "admin.list_memories",
                scope=narrowed_scope,
                scope_param=scope_param,
                page=page,
                page_size=page_size,
                has_query=bool(normalized_query),
            )
            # mem0 2.0.0 requires a ``filters`` dict; older versions and
            # the in-test ``_FakeMemory`` accept the keyword form too.
            filters = _build_get_all_filters(narrowed_scope, narrowed_scope_id)
            try:
                raw_records = memory_instance.get_all(
                    filters=filters, **{scope_param: narrowed_scope_id}
                )
            except TypeError:
                # Backward-compat path for fake/older mem0 that reject
                # ``filters`` and still use top-level entity kwargs.
                raw_records = memory_instance.get_all(
                    **{scope_param: narrowed_scope_id}
                )
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
                    raw_records = self._load_postgres_fallback_records()
                except Exception:
                    raw_records = []

        # Unwrap dict-wrapped response from mem0 2.0.0 and other
        # dict-shaped backends.  ``_unwrap_results`` is a no-op for bare
        # lists and for ``None`` / unrecognised shapes.
        raw_records = _unwrap_results(raw_records)

        items = self._anchor_service.normalize_payload(raw_records) or []
        if not isinstance(items, list):
            items = list(items) if items else []
        # ``AnchorService.normalize_payload`` normalises (and may clear)
        # the ``anchor`` field on each record.  Fallback rows store
        # anchors as ``{"created_at": "..."}`` only, so the normalize
        # step would wipe the synthesised anchor.  Re-inject the
        # stashed top-level anchor for fallback records and strip the
        # private marker before the records reach the list extractor.
        self._restore_fallback_anchors(items)

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

        if normalized_type:
            type_needle = normalized_type.casefold()

            def _matches_type(record: Any) -> bool:
                metadata = self._safe_metadata(record)
                memory_type = metadata.get("type") if metadata else None
                return (
                    isinstance(memory_type, str)
                    and memory_type.casefold() == type_needle
                )

            items = [record for record in items if _matches_type(record)]

        if normalized_project:
            project_needle = normalized_project.casefold()

            def _matches_project(record: Any) -> bool:
                metadata = self._safe_metadata(record)
                if not metadata:
                    return False
                for key in ("project", "project_id"):
                    value = metadata.get(key)
                    if isinstance(value, str) and value.casefold() == project_needle:
                        return True
                return False

            items = [record for record in items if _matches_project(record)]

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
            # Narrow the pre-bound copies from ``... | None`` to their
            # concrete types (see the assertion block earlier in this
            # method for the invariant explanation).
            assert narrowed_scope is not None
            assert narrowed_scope_id is not None
            assembled_items = [
                self._assemble_list_item(
                    record=record,
                    scope=narrowed_scope,
                    scope_id=narrowed_scope_id,
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
            "freshness": self._freshness_payload(aggregate, metadata, record),
        }

    def _load_postgres_fallback_records(self) -> list[dict[str, Any]]:
        """Load fallback records from the PostgreSQL ``mem0_memories`` table.

        The SQL fallback query (``SELECT id, payload FROM {table}``) returns
        rows that the canonical mem0 ``memory.get_all(...)`` path missed
        (e.g. when the in-memory store is empty but rows exist in the
        backing PostgreSQL table).  Live evidence (see
        ``.omo/notepads/cms-memory-cards-empty/issues.md`` 2026-06-16)
        shows these rows carry the actual memory attributes as
        **top-level** payload fields (``data``, ``type``, ``anchor``,
        ``created_at``, ``decay_half_life_days``, ``user_id``) and
        typically lack both the canonical ``memory``/``content``/
        ``messages`` content fields and a nested ``metadata`` dict.

        To keep the rest of the admin pipeline (content extraction,
        freshness, type/anchor rendering) working against the same shapes
        it sees for scoped-path records, this loader normalises the
        fallback row into the canonical shape **before** the record
        reaches :class:`AnchorService` or the list/detail extractors:

        * The SQL row UUID is stamped as ``record["id"]`` whenever the
          payload itself does not carry an ``id`` field.  This is the
          identity contract the CMS relies on for per-row actions
          (visit, copy, edit, delete) — locked by
          ``test_admin_service_list_memories_uses_postgres_row_uuid_when_payload_lacks_id``.
        * A non-empty top-level ``payload["data"]`` string is copied to
          ``payload["memory"]`` so the existing
          :meth:`AdminService._extract_content` branch picks it up.
          This is the only way to surface fallback rows through the
          canonical content extractor without coupling the extractor
          to the ``data`` field — keeps the extractor shape simple and
          centralises the data-shape adaptation in the loader.  Only
          runs when no canonical content field is already populated, so
          scoped-path records (which never go through this loader) and
          fallback rows that already carry ``memory`` are not mutated.
        * A ``record["metadata"]`` dict is synthesised from the
          top-level payload fields ``type``, ``anchor``,
          ``decay_half_life_days``, and ``created_at`` (with
          ``anchor.created_at`` as a secondary source for ``created_at``
          when the top-level field is absent).  When the payload already
          carries a nested ``metadata`` dict, the synthesised fields are
          merged in (never overwriting existing keys), so callers that
          pass richer metadata downstream are preserved.
        * The original top-level ``anchor`` is preserved on the record
          under the private marker ``_admin_fallback_anchor`` so the
          post-normalize step in
          :meth:`AdminService.list_memories` can re-inject it into
          ``record["metadata"]["anchor"]`` after
          :class:`AnchorService` strips/validates it.  The marker is
          removed once the re-injection is done; it never reaches the
          response payload.

        The normalisations are fallback-only: scoped-path records
        returned by ``memory.get_all(...)`` are never routed through
        this function, so the canonical content/metadata shapes are
        left untouched on the main list path.

        Returns:
            A list of dict-shaped records ready to be fed to
            :meth:`AnchorService.normalize_payload`.  Empty when the
            fallback query returns no rows.
        """
        rows = self._postgres_fallback_query("SELECT id, payload FROM {table}")
        records: list[dict[str, Any]] = []
        for row in rows or []:
            if not row:
                continue
            row_id: Any = None
            payload: Any = None
            if len(row) >= 2:
                row_id, payload = row[0], row[1]
            elif len(row) == 1:
                payload = row[0]
            if not isinstance(payload, dict):
                continue
            record = dict(payload)
            if isinstance(row_id, str) and row_id and not self._extract_memory_id(record):
                record["id"] = row_id
            self._promote_fallback_data_to_memory(record)
            original_anchor = record.get("anchor")
            record["metadata"] = self._synthesize_fallback_metadata(record)
            if isinstance(original_anchor, dict) and original_anchor:
                record["_admin_fallback_anchor"] = original_anchor
            records.append(record)
        return records

    @staticmethod
    def _restore_fallback_anchors(items: list[Any]) -> list[Any]:
        """Re-inject fallback anchors into ``record["metadata"]["anchor"]``.

        :class:`AnchorService` normalises the ``anchor`` field on each
        memory record and, in non-strict mode, replaces
        ``metadata["anchor"]`` with ``None`` whenever the anchor dict
        does not carry a ``type`` key.  The live PostgreSQL fallback
        rows store anchors as ``{"created_at": "..."}`` only (no
        ``type``), so the normalize step would wipe the synthesised
        anchor from :meth:`_load_postgres_fallback_records` for every
        fallback row.

        To keep the loader's synthesised metadata intact for the
        downstream list/detail extractors, this helper walks the
        record list and, for every record carrying the private marker
        ``_admin_fallback_anchor``, copies the original anchor into
        ``record["metadata"]["anchor"]`` and removes the marker.  The
        marker is only set by
        :meth:`_load_postgres_fallback_records`, so scoped-path
        records are never touched.

        Called from :meth:`AdminService.list_memories` immediately
        after :meth:`AnchorService.normalize_payload` returns.  Must
        run after normalize (so the clobber has already happened) and
        before the records reach :meth:`_assemble_list_item` (so the
        list extractor sees the restored anchor).

        Args:
            items: A list of records returned by
                :meth:`AnchorService.normalize_payload`.  May contain
                ``None`` entries and non-dict values, which are passed
                through unchanged.

        Returns:
            The same list, with fallback anchors restored and the
            private marker removed.  Returned for chaining
            convenience; the input list is mutated in place.
        """
        for record in items or []:
            if not isinstance(record, dict):
                continue
            stashed = record.pop("_admin_fallback_anchor", None)
            if not isinstance(stashed, dict) or not stashed:
                continue
            metadata = record.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                record["metadata"] = metadata
            metadata["anchor"] = stashed
        return items

    @staticmethod
    def _promote_fallback_data_to_memory(record: dict[str, Any]) -> None:
        """Copy ``payload.data`` into ``payload.memory`` for fallback rows.

        The PostgreSQL ``mem0_memories.payload`` rows surfaced by the
        fallback query carry the memory text in a top-level ``"data"``
        field rather than the canonical ``"memory"`` / ``"content"`` /
        ``"messages"`` fields.  Mutating the record in place to populate
        ``record["memory"]`` lets the existing
        :meth:`AdminService._extract_content` branch read the text
        without coupling the extractor to the fallback shape.

        Only fires when the top-level ``data`` field is a non-empty
        string and no canonical content field is already populated, so
        fallback rows that already carry ``memory`` (or ``content`` /
        ``messages``) are not overwritten.  This keeps the function
        safe to call against any dict-shaped record — scoped-path
        records that happen to pass through this loader will not be
        mutated because they do not have a top-level ``"data"`` field.

        Args:
            record: A dict-shaped record loaded from the PostgreSQL
                fallback query.  Mutated in place; ``record["memory"]``
                is set when the promotion runs.
        """
        payload_data = record.get("data")
        if not (isinstance(payload_data, str) and payload_data):
            return
        existing_memory = record.get("memory")
        if isinstance(existing_memory, str) and existing_memory:
            return
        record["memory"] = payload_data

    @staticmethod
    def _synthesize_fallback_metadata(record: dict[str, Any]) -> dict[str, Any]:
        """Return a metadata dict lifted from the payload's top-level fields.

        The fallback query returns rows that store their canonical
        attributes (``type``, ``anchor``, ``decay_half_life_days``,
        ``created_at``) at the **top level** of ``payload`` rather than
        under a nested ``"metadata"`` key.  The list and detail
        extractors (``_safe_metadata``, ``_freshness_payload``,
        ``_extract_decay_half_life_days``, etc.) all read from
        ``record["metadata"]``, so this helper builds that dict from the
        top-level fields when the payload does not already carry one.

        Merge policy:

        * If ``record["metadata"]`` is already a dict, start from a copy
          of it and only fill in keys that are missing or invalid for
          the target type.  Canonical payload metadata wins; top-level
          fields are a **secondary** source.
        * If the payload has no ``metadata`` key (the common fallback
          case), the synthesised dict is returned.
        * The synthesised dict is returned even when no fields are
          liftable, so callers that unconditionally read
          ``record["metadata"]`` still see a dict rather than ``None``.

        Field lifting rules:

        * ``type`` — copied when the top-level field is a non-empty
          string and the existing metadata has no string ``type``.
        * ``anchor`` — copied when the top-level field is a dict and
          the existing metadata has no dict ``anchor``.
        * ``decay_half_life_days`` — copied when the top-level field is
          a positive int (bool is rejected, matching
          :func:`_extract_decay_half_life_days`).
        * ``created_at`` — copied from the top-level ``created_at``
          string when present, otherwise from
          ``anchor["created_at"]`` as a secondary source.

        Args:
            record: A dict-shaped record loaded from the PostgreSQL
                fallback query.  Not mutated; the synthesised dict is
                returned as a fresh copy.

        Returns:
            A dict containing the lifted metadata fields, plus any
            fields the payload already carried under ``metadata``.  An
            empty dict is returned when the payload exposes no
            liftable fields and no nested metadata.
        """
        existing_metadata = record.get("metadata")
        metadata: dict[str, Any] = (
            dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
        )
        if not isinstance(metadata.get("type"), str):
            payload_type = record.get("type")
            if isinstance(payload_type, str) and payload_type:
                metadata["type"] = payload_type
        if not isinstance(metadata.get("anchor"), dict):
            payload_anchor = record.get("anchor")
            if isinstance(payload_anchor, dict) and payload_anchor:
                metadata["anchor"] = payload_anchor
        current_half_life = metadata.get("decay_half_life_days")
        if isinstance(current_half_life, bool) or not isinstance(current_half_life, int):
            payload_half_life = record.get("decay_half_life_days")
            if (
                isinstance(payload_half_life, int)
                and not isinstance(payload_half_life, bool)
                and payload_half_life >= 1
            ):
                metadata["decay_half_life_days"] = payload_half_life
        if not isinstance(metadata.get("created_at"), str):
            created_at = record.get("created_at")
            if not (isinstance(created_at, str) and created_at):
                anchor = record.get("anchor")
                if isinstance(anchor, dict):
                    anchor_created_at = anchor.get("created_at")
                    if isinstance(anchor_created_at, str) and anchor_created_at:
                        created_at = anchor_created_at
            if isinstance(created_at, str) and created_at:
                metadata["created_at"] = created_at
        return metadata

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
            "freshness": self._freshness_payload(aggregate, metadata, record),
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
        record: dict[str, Any] | None = None,
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
            record: The raw memory record (or ``None``); used as a
                fallback source for ``created_at`` from the ``anchor``
                object when ``metadata`` has no ``created_at``.

        Returns:
            An :class:`AdminFreshnessInfo` Pydantic model.
        """
        created_at = _extract_created_at(metadata)
        if created_at is None and isinstance(record, dict):
            anchor = record.get("anchor")
            if isinstance(anchor, dict):
                anchor_ts = anchor.get("created_at")
                if isinstance(anchor_ts, str):
                    created_at = anchor_ts
        return AdminFreshnessInfo(
            last_visited_at=aggregate.last_visited_at,
            never_visited=aggregate.never_visited,
            created_at=created_at,
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
