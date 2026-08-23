from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str = Field(..., description="Role of the message (user or assistant).")
    content: str = Field(..., description="Message content.")


class MemoryCreate(BaseModel):
    messages: list[Message] = Field(..., description="List of messages to store.")
    user_id: str | None = None
    agent_id: str | None = None
    run_id: str | None = None
    metadata: dict[str, Any] | None = None


class SearchRequest(BaseModel):
    query: str = Field(..., description="Search query.")
    user_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    filters: dict[str, Any] | None = None


class RetrieveRequest(BaseModel):
    query: str = Field(..., description="Retrieval query.")
    scopes: list[str] = Field(
        ..., min_length=1, description="Requested retrieval scopes."
    )
    user_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    limit: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Maximum number of ranked results to return.",
    )
    filters: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Unified query models
# ---------------------------------------------------------------------------

CorpusType = Literal["memory_store", "all"]


class MemoryHit(BaseModel):
    memory_id: str
    content: str
    score: float
    datetime: str | None = None
    corpus: str = "memory_store"
    metadata: dict[str, Any] | None = None


class UnifiedQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Search query.")
    corpora: list[CorpusType] = Field(default=["all"])
    limit: int = Field(10, ge=1, le=50)
    user_id: str | None = Field(
        default=None,
        description=(
            "Optional user identifier forwarded to the memory corpus for "
            "per-user scoping.  When omitted the server applies no user filter."
        ),
    )
    min_score_memory: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum relevance score for memory-store hits (range 0.0–1.0).  "
            "Hits from the memory corpus with a score strictly below this value "
            "are excluded.  Defaults to 0.5.  Set to 0.0 to disable filtering "
            "for this corpus."
        ),
    )
class UnifiedQueryResponse(BaseModel):
    hits: list[MemoryHit]
    total: int
    corpora_queried: list[str]
    available_hits_by_corpus: dict[str, int] = Field(default_factory=dict)
    degraded: bool = False
    degradation_reasons: list[str] = []


class CapabilitiesResponse(BaseModel):
    memory_store: dict[str, Any]


# ---------------------------------------------------------------------------
# Admin / CMS contract models  (Task 1 — locked contract)
# These are the additive admin request/response shapes for the internal CMS.
# Decay scores are NOT computed here; the backend exposes raw fields and the
# CMS computes display values using the plugin-authority formulas from
# ~/.config/opencode/plugins/mem0-functional.ts.
# ---------------------------------------------------------------------------

ScopeType = Literal["user", "agent", "run"]
"""Admin scope selector — one of ``user``, ``agent``, or ``run``."""

VisitReason = Literal["detail_open", "edit_save", "copy_source"]
"""Reason an admin visit was recorded."""

AdminMessageRole = Literal["user", "assistant"]
"""Admin message role — restricted to ``"user"`` or ``"assistant"`` per the locked CMS contract."""


class AdminMessage(BaseModel):
    """A single message in an admin create/update request.

    Role is restricted to ``"user"`` or ``"assistant"`` (not the full ``Message``
    model which accepts any string) so the CMS contract stays locked to the
    expected role set.
    """

    role: AdminMessageRole = Field(description="Message role (user or assistant).")
    content: str = Field(description="Message content.")


class CopiedFromInfo(BaseModel):
    """Provenance object attached to copy operations.

    Per the locked contract, ``copied_from`` is an object
    ``{ source_memory_id, source_scope, source_scope_id }`` — not a bare string
    — so the CMS can display full copy provenance without a secondary lookup.
    """

    source_memory_id: str = Field(
        description="Identifier of the memory from which the copy was made."
    )
    source_scope: ScopeType = Field(
        description="Scope type of the source memory."
    )
    source_scope_id: str = Field(
        description="Scope identifier of the source memory."
    )


class AdminPopularityInfo(BaseModel):
    """Persisted visit-aggregate fields for popularity display.

    Decay computation authority: these raw fields are consumed by the CMS;
    the backend does not compute a combined plugin score.

    Field ownership (Task 6):
    * ``total_visits`` — counter from the dedicated visit store
      (see :class:`services.visit_store.VisitStore`).  Lifetime count,
      monotonically increasing per call to ``POST /admin/memories/{id}/visits``.
    * ``visit_ratio`` — ``total_visits / max_total_visits`` capped at
      ``1.0``.  ``max_total_visits`` is the highest ``total_visits``
      across the entire visit store at response time (per-page max when
      the list endpoint is the source).  When no visits have been
      recorded anywhere, ``max_total`` is ``0`` and the ratio is
      reported as ``0.0``.

    Compare with :class:`AdminFreshnessInfo`, which carries the
    decay-input block; the two models are deliberately disjoint and the
    CMS reads each one independently.
    """

    total_visits: int = Field(
        default=0,
        ge=0,
        description="Total number of recorded visits for this memory.",
    )
    visit_ratio: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Ratio of this memory's visits relative to the most-visited "
            "memory in the same scope (0.0–1.0).  Computed from raw visit "
            "telemetry, not a plugin score.  The denominator is the peak "
            "``total_visits`` across the visit store at response time; "
            "the numerator is the memory's own ``total_visits``.  When no "
            "visits have been recorded anywhere the ratio is ``0.0``."
        ),
    )


class AdminFreshnessInfo(BaseModel):
    """Raw decay-input fields for freshness display.

    The CMS uses these to compute recency/decay via plugin-authority formulas.

    Field ownership (Task 6):
    * ``last_visited_at`` — ISO 8601 timestamp of the most recent visit
      from the dedicated visit store.  ``None`` when the memory has
      never been visited.
    * ``never_visited`` — explicit boolean flag derived from the
      aggregate's ``total_visits == 0``.  This is the single source of
      truth for the CMS "cold" indicator: clients must NOT infer it
      from a null ``last_visited_at`` because future storage changes
      (e.g. epoch timestamps) could change that nullability.
    * ``created_at``, ``decay_half_life_days``, ``ttl_expires_at`` —
      decay inputs read from the memory's metadata.  ``None`` values
      are surfaced as-is so the CMS can fall back to its
      ``deriveHalfLifeDays(type)`` rule and the conditional
      ``ttl_expires_at`` 0.25 multiplier described in the plugin.

    The CMS owns the recency formula; the backend exposes raw fields
    only and never computes a combined plugin score at this layer.
    """

    last_visited_at: str | None = Field(
        default=None,
        description=(
            "ISO 8601 timestamp of the last recorded visit, or ``None`` "
            "if the memory has never been visited.  Pairs with "
            "``never_visited``; the CMS should read both fields rather "
            "than null-checking this one."
        ),
    )
    never_visited: bool = Field(
        default=True,
        description=(
            "``True`` when the memory has zero recorded visit events. "
            "Never-visited items remain cold in the UI regardless of "
            "creation time.  This flag is the canonical cold indicator; "
            "do not infer it client-side from a null ``last_visited_at``."
        ),
    )
    created_at: str | None = Field(
        default=None,
        description=(
            "ISO 8601 timestamp of when the memory was created.  Used by the "
            "CMS as the reference point for age-based decay computation."
        ),
    )
    decay_half_life_days: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Half-life in days for plugin-authority decay computation "
            "(e.g. 30 for ``decision``-type memories).  When ``None`` the "
            "CMS applies ``deriveHalfLifeDays(type)`` from the plugin."
        ),
    )
    ttl_expires_at: str | None = Field(
        default=None,
        description=(
            "Optional ISO 8601 timestamp after which the memory is "
            "considered TTL-expired.  When present and in the past, the "
            "CMS multiplies recency by 0.25 per the plugin formula."
        ),
    )


class AdminAuditInfo(BaseModel):
    """Provenance metadata for admin-initiated write operations."""

    impersonated_by: str | None = Field(
        default=None,
        description=(
            "Who performed the admin action.  Always ``\"admin\"`` for "
            "admin-endpoint creates, updates, and copies."
        ),
    )
    copied_from: CopiedFromInfo | None = Field(
        default=None,
        description=(
            "Copy provenance object if this memory was created via the copy "
            "endpoint.  An object ``{ source_memory_id, source_scope, "
            "source_scope_id }``.  ``None`` for directly created memories."
        ),
    )


class AdminMemoryListItem(BaseModel):
    """A single memory entry in the paginated admin list response."""

    memory_id: str = Field(description="Unique memory identifier.")
    scope: ScopeType = Field(description="Scope type under which this memory lives.")
    scope_id: str = Field(description="Scope identifier (e.g. user ID, agent ID).")
    content: str = Field(description="Text content of the memory.")
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Arbitrary metadata attached to the memory.",
    )
    popularity: AdminPopularityInfo = Field(
        description="Visit-based popularity raw fields.",
    )
    freshness: AdminFreshnessInfo = Field(
        description="Decay-input freshness raw fields.",
    )


class AdminMemoryListResponse(BaseModel):
    """Paginated response for ``GET /admin/memories``."""

    items: list[AdminMemoryListItem] = Field(
        description="List of memory entries for the current page.",
    )
    page: int = Field(ge=1, description="Current page number (1-indexed).")
    page_size: int = Field(ge=1, description="Number of items per page.")
    total_items: int = Field(ge=0, description="Total number of matching memories across all pages.")
    total_pages: int = Field(ge=0, description="Total number of pages.")


class AdminMemoryCreateRequest(BaseModel):
    """Request body for ``POST /admin/memories``."""

    scope: ScopeType = Field(description="Scope type for the new memory.")
    scope_id: str = Field(description="Scope identifier for the new memory.")
    messages: list[AdminMessage] = Field(..., min_length=1, description="Messages to store in the new memory.")
    metadata: dict[str, Any] | None = Field(
        default=None, description="Optional metadata to attach."
    )


class AdminMemoryCreateResponse(BaseModel):
    """Response body for ``POST /admin/memories``."""

    memory_id: str = Field(description="Unique identifier of the created memory.")
    scope: ScopeType = Field(description="Scope type of the created memory.")
    scope_id: str = Field(description="Scope identifier of the created memory.")
    messages: list[AdminMessage] = Field(description="Stored messages.")
    metadata: dict[str, Any] | None = Field(
        default=None, description="Attached metadata (may include audit stamps)."
    )
    impersonated_by: str = Field(
        default="admin", description="Always ``\"admin\"`` for admin-endpoint creates."
    )


class AdminMemoryDetailResponse(BaseModel):
    """Full memory detail response for ``GET /admin/memories/{memory_id}``."""

    memory_id: str = Field(description="Unique memory identifier.")
    scope: ScopeType = Field(description="Scope type of the memory.")
    scope_id: str = Field(description="Scope identifier of the memory.")
    content: str = Field(description="Text content of the memory.")
    metadata: dict[str, Any] | None = Field(
        default=None, description="Arbitrary metadata attached to the memory."
    )
    popularity: AdminPopularityInfo = Field(
        description="Visit-based popularity raw fields.",
    )
    freshness: AdminFreshnessInfo = Field(
        description="Decay-input freshness raw fields.",
    )
    audit: AdminAuditInfo = Field(
        description="Audit/provenance metadata for admin actions.",
    )


class AdminMemoryUpdateRequest(BaseModel):
    """Request body for ``PUT /admin/memories/{memory_id}``."""

    messages: list[AdminMessage] = Field(..., min_length=1, description="Updated messages.")
    metadata: dict[str, Any] | None = Field(
        default=None, description="Replacement metadata (overwrites existing)."
    )


class AdminMemoryDeleteResponse(BaseModel):
    """Response body for ``DELETE /admin/memories/{memory_id}``."""

    memory_id: str = Field(description="Identifier of the deleted memory.")
    deleted: bool = Field(default=True, description="Confirmation flag; always ``true`` on success.")


class AdminMemoryCopyRequest(BaseModel):
    """Request body for ``POST /admin/memories/{memory_id}/copy``."""

    target_scope: ScopeType = Field(description="Target scope type for the copy.")
    target_scope_id: str = Field(description="Target scope identifier for the copy.")


class AdminMemoryCopyResponse(BaseModel):
    """Response body for ``POST /admin/memories/{memory_id}/copy``."""

    source_memory_id: str = Field(description="Identifier of the source memory that was copied.")
    target_memory_id: str = Field(description="Identifier of the newly created target memory.")
    target_scope: ScopeType = Field(description="Scope type of the new memory.")
    target_scope_id: str = Field(description="Scope identifier of the new memory.")
    copied_from: CopiedFromInfo = Field(
        description=(
            "Provenance object ``{ source_memory_id, source_scope, "
            "source_scope_id }`` identifying the source memory.  Corresponds "
            "to the ``copied_from`` field in the target's audit metadata."
        ),
    )
    impersonated_by: str = Field(
        default="admin", description="Always ``\"admin\"`` for admin-endpoint copies."
    )


class AdminMemoryVisitRequest(BaseModel):
    """Request body for ``POST /admin/memories/{memory_id}/visits``."""

    reason: VisitReason = Field(description="Why the visit is being recorded.")


class AdminMemoryVisitResponse(BaseModel):
    """Response body for ``POST /admin/memories/{memory_id}/visits``.

    Predictability contract (Task 6):
    * After the first successful call, ``total_visits >= 1`` and
      ``last_visited_at`` is a non-null ISO 8601 string.  Repeated
      calls monotonically increase ``total_visits`` and refresh
      ``last_visited_at`` to the max of the previous value and the
      new event.
    * This response carries visit-telemetry fields only; the
      freshness-decay inputs (``created_at``, ``decay_half_life_days``,
      ``ttl_expires_at``) live in :class:`AdminFreshnessInfo` on the
      list/detail endpoints.  The CMS reads each shape from the
      appropriate endpoint rather than this one.
    """

    memory_id: str = Field(description="Identifier of the visited memory.")
    total_visits: int = Field(ge=0, description="Updated total visit count.")
    last_visited_at: str | None = Field(
        description="ISO 8601 timestamp of the recorded visit.  ``None`` "
        "would indicate a backend bug — the write flow always sets a "
        "timestamp on the event row."
    )
    reason: VisitReason = Field(description="The reason provided in the request.")


# ---------------------------------------------------------------------------
# Admin scope models
# ---------------------------------------------------------------------------


class AdminScopesResponse(BaseModel):
    """Response body for ``GET /admin/scopes``.

    Returns distinct identifiers observed across all stored memories
    for each scope type.  ``projects`` is populated from memory metadata
    (``project`` / ``project_id`` keys) when available.
    """

    users: list[str] = Field(
        default_factory=list,
        description="Distinct user identifiers observed in stored memories.",
    )
    agents: list[str] = Field(
        default_factory=list,
        description="Distinct agent identifiers observed in stored memories.",
    )
    runs: list[str] = Field(
        default_factory=list,
        description="Distinct run identifiers observed in stored memories.",
    )
    projects: list[str] = Field(
        default_factory=list,
        description="Distinct project identifiers observed in memory metadata.",
    )
