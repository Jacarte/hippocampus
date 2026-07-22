from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from mem0 import Memory

from api_models import (
    AdminMemoryCopyRequest,
    AdminMemoryCreateRequest,
    AdminMemoryUpdateRequest,
    AdminMemoryVisitRequest,
    AdminScopesResponse,
    CapabilitiesResponse,
    FileChunksRequest,
    IndexResetRequest,
    IndexSyncRequest,
    FileIngestRequest,
    MemoryCreate,
    RetrieveRequest,
    SearchRequest,
    ScopeType,
    UnifiedQueryRequest,
    WatchStartRequest,
    WatchStopRequest,
)
from services.admin_service import AdminService
from services.anchor_service import AnchorService
from services.background_job_service import BackgroundJobService
from services.file_corpus_service import FileCorpusService
from services.file_scanner import FileScanner
from services.index_manifest_service import IndexManifestService
from services.indexing_service import IndexingService
from services.memory_service import MemoryService
from services.query_service import QueryService
from services.retrieval_service import RetrievalService
from services.watch_service import WatchService
from services.runtime import (
    MemoryFactory,
    get_memory_instance,
    get_runtime_options,
    initialize_memory,
    is_chunk_memory_enabled,
)
from services.tracing import (
    bind_request_id,
    reset_request_id,
    resolve_request_id,
    trace_backend_error,
    trace_backend_request_complete,
    trace_backend_request_start,
)
from prometheus_client import make_asgi_app
from services.metrics import http_request_duration_seconds, http_requests_total

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def create_app(
    *, memory_factory: MemoryFactory = Memory.from_config, startup_enabled: bool = True
) -> FastAPI:
    app = FastAPI(
        title="Mem0 REST API",
        description="A REST API for managing and searching memories for your AI Agents and Apps.",
        version="1.0.0",
    )
    app.state.memory_factory = memory_factory
    app.state.memory = None
    app.state.memory_config = None
    app.state.memory_service = MemoryService(
        retrieval_service=RetrievalService(),
        anchor_service=AnchorService(),
    )
    _corpus = FileCorpusService()
    _manifest = IndexManifestService()
    _scanner = FileScanner()
    _retrieval = RetrievalService()
    app.state.indexing_service = IndexingService(
        corpus=_corpus,
        manifest=_manifest,
        scanner=_scanner,
    )
    app.state.query_service = QueryService(
        corpus=_corpus,
        retrieval_service=_retrieval,
    )
    app.state.watch_service = WatchService(
        indexing_service=app.state.indexing_service,
    )
    app.state.job_service = BackgroundJobService(max_workers=20)
    app.state.admin_service = AdminService()

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next: Any) -> Any:
        request_id = resolve_request_id(request.headers)
        request.state.request_id = request_id
        started_at = time.perf_counter()
        token = bind_request_id(request_id)
        trace_backend_request_start(request.method, request.url.path)
        try:
            response = await call_next(request)
        except Exception:
            latency_ms = (time.perf_counter() - started_at) * 1000
            http_requests_total.labels(
                method=request.method, path=request.url.path, status_code=500
            ).inc()
            http_request_duration_seconds.labels(
                method=request.method, path=request.url.path
            ).observe(latency_ms / 1000)
            trace_backend_request_complete(
                request.method,
                request.url.path,
                status_code=500,
                latency_ms=latency_ms,
            )
            raise
        else:
            response.headers["X-Correlation-ID"] = request_id
            latency_ms = (time.perf_counter() - started_at) * 1000
            http_requests_total.labels(
                method=request.method, path=request.url.path, status_code=response.status_code
            ).inc()
            http_request_duration_seconds.labels(
                method=request.method, path=request.url.path
            ).observe(latency_ms / 1000)
            trace_backend_request_complete(
                request.method,
                request.url.path,
                status_code=response.status_code,
                latency_ms=latency_ms,
            )
            return response
        finally:
            reset_request_id(token)

    class _PrometheusASGI:
        """Wraps the Prometheus ASGI app so Starlette routes it correctly.

        ``make_asgi_app()`` returns a closure (function), which ``Route`` treats
        as a regular request handler ``func(request) -> response`` and calls
        with the wrong signature.  Wrapping it in a class makes ``Route``
        detect it as an ASGI callable and invoke it with ``(scope, receive,
        send)`` instead — no trailing-slash redirect, no signature mismatch.
        """

        def __init__(self) -> None:
            self._app = make_asgi_app()

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            await self._app(scope, receive, send)

    app.add_route(
        "/metrics", _PrometheusASGI(), methods=["GET"], include_in_schema=False
    )

    if startup_enabled:

        @app.on_event("startup")
        def startup_initialize_memory() -> None:
            initialize_memory(app)

    @app.on_event("shutdown")
    def shutdown_job_service() -> None:
        app.state.job_service.shutdown(wait=False)

    @app.get("/", summary="Redirect to documentation", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    @app.get("/health", summary="Health check")
    def health() -> dict[str, str]:
        return {"status": "healthy", "service": "mem0-api"}

    @app.post("/configure", summary="Configure Mem0")
    def set_config(config: dict[str, Any], request: Request) -> dict[str, str]:
        return _execute_service_call(
            "set_config",
            lambda: request.app.state.memory_service.configure(request.app, config),
        )

    @app.post("/memories", summary="Create memories")
    def add_memory(memory_create: MemoryCreate, request: Request) -> JSONResponse:
        memory_instance = get_memory_instance(request)
        response = _execute_service_call(
            "add_memory",
            lambda: request.app.state.memory_service.add(
                memory_instance, memory_create
            ),
        )
        return JSONResponse(content=response)

    @app.get("/memories", summary="Get memories")
    def get_all_memories(
        request: Request,
        user_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
    ) -> Any:
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "get_all_memories",
            lambda: request.app.state.memory_service.get_all(
                memory_instance,
                user_id=user_id,
                run_id=run_id,
                agent_id=agent_id,
            ),
        )

    @app.get("/memories/{memory_id}", summary="Get a memory")
    def get_memory(memory_id: str, request: Request) -> Any:
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "get_memory",
            lambda: request.app.state.memory_service.get(memory_instance, memory_id),
        )

    @app.post("/search", summary="Search memories")
    def search_memories(search_req: SearchRequest, request: Request) -> Any:
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "search_memories",
            lambda: request.app.state.memory_service.search(
                memory_instance, search_req
            ),
        )

    @app.post("/retrieve", summary="Retrieve memories")
    def retrieve_memories(retrieve_req: RetrieveRequest, request: Request) -> Any:
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "retrieve_memories",
            lambda: request.app.state.memory_service.retrieve(
                memory_instance, retrieve_req
            ),
        )

    @app.put("/memories/{memory_id}", summary="Update a memory")
    def update_memory(
        memory_id: str, updated_memory: dict[str, Any], request: Request
    ) -> Any:
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "update_memory",
            lambda: request.app.state.memory_service.update(
                memory_instance, memory_id, updated_memory
            ),
        )

    @app.get("/memories/{memory_id}/history", summary="Get memory history")
    def memory_history(memory_id: str, request: Request) -> Any:
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "memory_history",
            lambda: request.app.state.memory_service.history(
                memory_instance, memory_id
            ),
        )

    @app.delete("/memories/{memory_id}", summary="Delete a memory")
    def delete_memory(memory_id: str, request: Request) -> dict[str, str]:
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "delete_memory",
            lambda: request.app.state.memory_service.delete(memory_instance, memory_id),
        )

    @app.delete("/memories", summary="Delete all memories")
    def delete_all_memories(
        request: Request,
        user_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, str]:
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "delete_all_memories",
            lambda: request.app.state.memory_service.delete_all(
                memory_instance,
                user_id=user_id,
                run_id=run_id,
                agent_id=agent_id,
            ),
        )

    @app.post("/reset", summary="Reset all memories")
    def reset_memory(request: Request) -> dict[str, str]:
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "reset_memory",
            lambda: request.app.state.memory_service.reset(memory_instance),
        )

    @app.post("/query", summary="Unified cross-corpus query")
    def unified_query(query_req: UnifiedQueryRequest, request: Request) -> Any:
        chunk_memory_enabled = is_chunk_memory_enabled()
        try:
            memory_instance = get_memory_instance(request)
        except Exception:
            memory_instance = None
        logger.info(
            "query user_id=%s corpora=%s chunk_memory=%s query=%r",
            query_req.user_id,
            query_req.corpora,
            chunk_memory_enabled,
            query_req.query,
        )
        return _execute_service_call(
            "unified_query",
            lambda: request.app.state.query_service.query(
                query_text=query_req.query,
                corpora=query_req.corpora,
                limit=query_req.limit,
                path_filter=query_req.path_filter,
                language_filter=query_req.language_filter,
                scope_filter=query_req.scope_filter,
                chunk_memory_enabled=chunk_memory_enabled,
                memory_instance=memory_instance,
                user_id=query_req.user_id,
                min_score_memory=query_req.min_score_memory,
                min_score_files=query_req.min_score_files,
            ),
        )

    @app.post("/index/sync", summary="Sync a root directory into the file corpus")
    def index_sync(sync_req: IndexSyncRequest, request: Request) -> Any:
        """Enqueue a filesystem sync of *root* and return a job record immediately.

        The server reads files from its local filesystem; *root* must be a path
        accessible to the server process.  The actual indexing work runs in a
        background thread so this endpoint returns quickly even for large trees.

        Returns:
            A job record dict with ``job_id`` and ``status="queued"``.
            Poll ``GET /index/jobs/{job_id}`` for progress and errors.
        """
        job_id = request.app.state.job_service.submit(
            request.app.state.indexing_service.sync,
            sync_req.root,
            generate_summaries=sync_req.generate_summaries,
        )
        return _execute_service_call(
            "index_sync",
            lambda: request.app.state.job_service.get_job(job_id),
        )

    @app.post("/index/ingest", summary="Ingest file contents into the corpus")
    def index_ingest(ingest_req: FileIngestRequest, request: Request) -> Any:
        """Accept pre-read file contents and index them into the corpus.

        Unlike ``POST /index/sync``, which requires the server to read files
        from its own filesystem, this endpoint accepts file contents in the
        request body.  Use it when the server is remote or does not share a
        filesystem with the client.

        The corpus namespace is ``project_id`` when provided, otherwise
        ``root``.  Using a stable ``project_id`` ensures that chunks from the
        same project indexed from different machines or paths are stored
        together and do not collide with other projects.

        Returns immediately with a ``job_id``.  Poll ``GET /index/jobs/{job_id}``
        for status and errors.
        """
        job_id = request.app.state.job_service.submit(
            request.app.state.indexing_service.ingest,
            root=ingest_req.root,
            files=[f.model_dump() for f in ingest_req.files],
            generate_summaries=ingest_req.generate_summaries,
            project_id=ingest_req.project_id,
        )
        return _execute_service_call(
            "index_ingest",
            lambda: request.app.state.job_service.get_job(job_id),
        )

    @app.get("/index/jobs", summary="List background indexing jobs")
    def index_jobs_list(request: Request, limit: int = 50) -> Any:
        """Return the most-recent indexing jobs, newest first.

        Args:
            limit: Maximum number of records to return (default ``50``).

        Returns:
            List of job records, each with keys ``job_id``, ``status``,
            ``queued_at``, ``started_at``, ``completed_at``, ``result``,
            and ``errors``.
        """
        return _execute_service_call(
            "index_jobs_list",
            lambda: request.app.state.job_service.list_jobs(limit=limit),
        )

    @app.get("/index/jobs/{job_id}", summary="Get status of a background indexing job")
    def index_job_get(job_id: str, request: Request) -> Any:
        """Return the job record for *job_id*, or raise 404 if not found.

        Args:
            job_id: UUID string returned by ``POST /index/sync`` or
                ``POST /index/ingest``.

        Returns:
            Job dict with keys ``job_id``, ``status``, ``queued_at``,
            ``started_at``, ``completed_at``, ``result``, and ``errors``.

        Raises:
            HTTPException: 404 when *job_id* is not recognised.
        """
        def _get() -> dict:
            record = request.app.state.job_service.get_job(job_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
            return record
        return _execute_service_call("index_job_get", _get)

    @app.post("/index/watch/start", summary="Start watching a root directory")
    def index_watch_start(watch_req: WatchStartRequest, request: Request) -> dict[str, Any]:
        return _execute_service_call(
            "index_watch_start",
            lambda: (
                request.app.state.watch_service.start(
                    watch_req.root,
                    generate_summaries=watch_req.generate_summaries,
                ),
                {"root": watch_req.root, "watching": True},
            )[-1],
        )

    @app.post("/index/watch/stop", summary="Stop watching a root directory")
    def index_watch_stop(watch_req: WatchStopRequest, request: Request) -> dict[str, Any]:
        return _execute_service_call(
            "index_watch_stop",
            lambda: (
                request.app.state.watch_service.stop(watch_req.root),
                {"root": watch_req.root, "watching": False},
            )[-1],
        )

    @app.get("/index/status", summary="Get file corpus index status")
    def index_status(request: Request) -> Any:
        return _execute_service_call(
            "index_status",
            lambda: {
                **request.app.state.indexing_service.status(),
                "recent_errors": request.app.state.job_service.recent_errors(),
            },
        )

    @app.post("/index/file", summary="Get all indexed chunks for a specific file")
    def index_file_chunks(req: FileChunksRequest, request: Request) -> Any:
        return _execute_service_call(
            "index_file_chunks",
            lambda: request.app.state.indexing_service.file_chunks(
                file_path=req.file_path,
                root=req.root,
                include_embeddings=req.include_embeddings,
            ),
        )

    @app.post("/index/reset", summary="Reset the file corpus index")
    def index_reset(reset_req: IndexResetRequest, request: Request) -> Any:
        return _execute_service_call(
            "index_reset",
            lambda: request.app.state.indexing_service.reset(),
        )

    @app.get("/query/capabilities", summary="Describe query capabilities")
    def query_capabilities(request: Request) -> Any:
        return _execute_service_call(
            "query_capabilities",
            lambda: CapabilitiesResponse(
                memory_store={
                    "lexical": True,
                    "semantic": True,
                    "rerank": False,
                },
                file_corpus={
                    "lexical": True,
                    "semantic": False,
                },
            ).model_dump(),
        )

    # ------------------------------------------------------------------
    # Admin CMS routes (additive — no changes to public endpoints)
    # ------------------------------------------------------------------
    # Handlers are thin; every method delegates to AdminService which
    # owns audit stamping, visit persistence, and orchestration rules.
    # ------------------------------------------------------------------

    @app.get("/admin/health", summary="Admin CMS health check")
    def admin_health(request: Request) -> dict[str, Any]:
        """Return a readiness metadata payload for the admin CMS surface.

        Unlike ``GET /health`` (which reports public API status), this
        endpoint returns admin-specific metadata such as the bound visit
        store path.

        Returns:
            A dict with ``status`` (``"ok"``), ``service``
            (``"admin-cms"``), and ``visit_db_path`` (the SQLite path).
        """
        return _execute_service_call(
            "admin_health",
            lambda: request.app.state.admin_service.health(),
        )

    @app.get(
        "/admin/scopes",
        summary="Admin list known scope identifiers",
        response_model=AdminScopesResponse,
    )
    def admin_list_scopes(request: Request) -> AdminScopesResponse:
        """Return distinct user, agent, run, and project identifiers.

        The endpoint tries multiple strategies to enumerate all stored
        memories (since mem0's ``get_all()`` requires an identifier) and
        extracts distinct scope values from the results.

        Returns:
            An :class:`AdminScopesResponse` with ``users``, ``agents``,
            ``runs``, and ``projects`` lists (sorted, no duplicates).
            Lists are empty when no memories are stored or no strategy
            succeeds.
        """
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "admin_list_scopes",
            lambda: request.app.state.admin_service.list_scopes(memory_instance),
        )

    @app.get("/admin/memories", summary="Admin list memories")
    def admin_list_memories(
        request: Request,
        scope: ScopeType | None = None,
        scope_id: str | None = None,
        page: int = Query(default=1, ge=1, description="Page number (1-indexed)."),
        page_size: int = Query(
            default=20,
            ge=1,
            le=100,
            description="Items per page (max 100).",
        ),
        query: str | None = Query(
            default=None,
            description=(
                "Optional case-insensitive substring filter applied to "
                "the extracted memory content.  Whitespace-only is treated "
                "as no filter.  The filter is applied before pagination."
            ),
        ),
        type: str | None = Query(
            default=None,
            description=(
                "Optional case-insensitive exact filter on "
                "metadata.type. Whitespace-only is treated as no filter."
            ),
        ),
        project: str | None = Query(
            default=None,
            description=(
                "Optional case-insensitive exact filter on metadata.project "
                "or metadata.project_id. Whitespace-only is treated as no "
                "filter."
            ),
        ),
    ) -> dict[str, Any]:
        """Return a paginated list of memories, optionally filtered by scope.

        When ``scope`` and ``scope_id`` are both provided the response is
        scoped to that particular user/agent/run.  When either is omitted
        the endpoint returns **all** memories across all scopes, with each
        item carrying its own ``scope``/``scope_id`` inferred from the
        stored record.

        The response mirrors :class:`AdminMemoryListResponse` and
        includes per-memory popularity and freshness raw fields for the
        CMS.  Each item carries a Pydantic
        :class:`AdminPopularityInfo` / :class:`AdminFreshnessInfo` pair.

        Args:
            scope: ``"user"`` / ``"agent"`` / ``"run"``.  When omitted
                all scopes are returned.
            scope_id: Identifier within the chosen scope.  When omitted
                all scopes are returned.
            page: 1-indexed page number.
            page_size: Items per page (clamped to 100).
            query: Optional case-insensitive substring filter on
                ``content``; ``None`` or whitespace-only means no filter.
            type: Optional case-insensitive exact filter on
                ``metadata.type``.
            project: Optional case-insensitive exact filter on
                ``metadata.project`` or ``metadata.project_id``.

        Returns:
            Paginated dict with ``items``, ``page``, ``page_size``,
            ``total_items``, and ``total_pages`` keys.
        """
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "admin_list_memories",
            lambda: request.app.state.admin_service.list_memories(
                memory_instance,
                scope=scope,
                scope_id=scope_id,
                page=page,
                page_size=page_size,
                query=query,
                type=type,
                project=project,
            ),
        )

    @app.post("/admin/memories", summary="Admin create memory")
    def admin_create_memory(
        payload: AdminMemoryCreateRequest, request: Request
    ) -> dict[str, Any]:
        """Create a memory under the admin scope with audit stamping.

        The payload specifies ``scope``, ``scope_id``, ``messages``, and
        optional ``metadata``.  ``impersonated_by=admin`` is stamped by
        :class:`AdminService` before the write reaches mem0.

        Returns:
            A dict shaped like :class:`AdminMemoryCreateResponse` with
            ``memory_id``, ``scope``, ``scope_id``, ``messages``,
            ``metadata``, and ``impersonated_by``.
        """
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "admin_create_memory",
            lambda: request.app.state.admin_service.create_memory(
                memory_instance, payload
            ),
        )

    @app.get("/admin/memories/{memory_id}", summary="Admin get memory detail")
    def admin_get_memory(memory_id: str, request: Request) -> dict[str, Any]:
        """Return full detail for a single memory, including audit provenance.

        Args:
            memory_id: Identifier of the memory to retrieve.

        Returns:
            A dict shaped like :class:`AdminMemoryDetailResponse` with
            ``memory_id``, ``scope``, ``scope_id``, ``content``,
            ``metadata``, ``popularity``, ``freshness``, and ``audit``.

        Raises:
            HTTPException 404: When *memory_id* does not exist.
        """
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "admin_get_memory",
            lambda: request.app.state.admin_service.get_memory(
                memory_instance, memory_id
            ),
        )

    @app.put("/admin/memories/{memory_id}", summary="Admin update memory")
    def admin_update_memory(
        memory_id: str, payload: AdminMemoryUpdateRequest, request: Request
    ) -> dict[str, Any]:
        """Update a memory's messages and/or metadata under the admin scope.

        ``impersonated_by=admin`` is re-stamped on the metadata, and any
        prior ``copied_from`` provenance is preserved unless the new
        metadata explicitly overrides it (the request replaces
        ``metadata`` wholesale — there is no field-level merge).

        Compare with ``POST /admin/memories`` (no source) and
        ``POST /admin/memories/{memory_id}/copy`` (creates a new memory
        under a different scope) — update is the only flow that mutates
        an existing record's content/metadata in place.

        Args:
            memory_id: Identifier of the memory to update.
            payload: Updated ``messages`` and optional ``metadata``.

        Returns:
            A dict shaped like :class:`AdminMemoryDetailResponse` with
            the updated fields plus audit block.

        Raises:
            HTTPException 422: When ``messages`` is empty or any message
                has an invalid role (validated by Pydantic).
            HTTPException 400: When *memory_id* does not exist (raised by
                the service's ``ValueError``).
        """
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "admin_update_memory",
            lambda: request.app.state.admin_service.update_memory(
                memory_instance, memory_id, payload
            ),
        )

    @app.delete("/admin/memories/empty", summary="Delete all empty memories")
    def admin_delete_empty_memories(request: Request) -> dict[str, Any]:
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "admin_delete_empty_memories",
            lambda: request.app.state.admin_service.delete_empty_memories(
                memory_instance
            ),
        )

    @app.delete("/admin/memories/{memory_id}", summary="Admin delete memory")
    def admin_delete_memory(memory_id: str, request: Request) -> dict[str, Any]:
        """Delete a memory by id under the admin scope.

        Deletion is intentionally separate from the copy flow: copy
        creates a new record under the target scope without touching the
        source, while delete removes the record entirely.  The two
        endpoints are not coupled.

        Args:
            memory_id: Identifier of the memory to delete.

        Returns:
            A dict with ``memory_id`` and ``deleted=True``.

        Raises:
            HTTPException 400: When *memory_id* does not exist (raised by
                the service's ``ValueError``).
        """
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "admin_delete_memory",
            lambda: request.app.state.admin_service.delete_memory(
                memory_instance, memory_id
            ),
        )

    @app.post(
        "/admin/memories/{memory_id}/copy",
        summary="Admin copy memory to a different scope",
    )
    def admin_copy_memory(
        memory_id: str, payload: AdminMemoryCopyRequest, request: Request
    ) -> dict[str, Any]:
        """Copy a memory into a new scope with full provenance stamping.

        Read-source → create-target semantics: the source memory is **not**
        mutated, deleted, or rebound.  A new memory is created in the
        target scope with the source's messages and metadata, stamped with
        both ``impersonated_by=admin`` and the
        ``copied_from={ source_memory_id, source_scope, source_scope_id }``
        provenance object.

        Compare with ``POST /admin/memories`` (no source — new memory
        only), ``PUT /admin/memories/{memory_id}`` (mutates an existing
        record), and ``DELETE /admin/memories/{memory_id}`` (removes the
        record) — copy is the only flow that introduces a new memory under
        a different scope while preserving the source.

        Args:
            memory_id: Identifier of the source memory to copy.
            payload: ``target_scope`` and ``target_scope_id`` (both
                validated by :class:`AdminMemoryCopyRequest`).

        Returns:
            A dict shaped like :class:`AdminMemoryCopyResponse` with
            ``source_memory_id``, ``target_memory_id``,
            ``target_scope``, ``target_scope_id``, ``copied_from``, and
            ``impersonated_by``.

        Raises:
            HTTPException 422: When ``target_scope`` is not a valid
                scope literal or ``target_scope_id`` is missing.
            HTTPException 400: When the source memory does not exist
                (raised by the service's ``ValueError``).
        """
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "admin_copy_memory",
            lambda: request.app.state.admin_service.copy_memory(
                memory_instance, memory_id, payload
            ),
        )

    @app.post(
        "/admin/memories/{memory_id}/visits",
        summary="Admin record a visit for a memory",
    )
    def admin_record_visit(
        memory_id: str, payload: AdminMemoryVisitRequest, request: Request
    ) -> dict[str, Any]:
        """Record a visit event through the dedicated visit telemetry store.

        Unlike ``GET /admin/memories/{memory_id}``, this endpoint
        explicitly writes a visit event.  The underlying memory metadata is
        **not** mutated — only the visit store is updated.

        Args:
            memory_id: Identifier of the memory being visited.
            payload: ``reason`` — one of ``"detail_open"``,
                ``"edit_save"``, or ``"copy_source"``.

        Returns:
            A dict shaped like :class:`AdminMemoryVisitResponse` with
            ``memory_id``, ``total_visits``, ``last_visited_at``, and
            ``reason``.

        Raises:
            HTTPException 404: When the memory does not exist.
        """
        memory_instance = get_memory_instance(request)
        return _execute_service_call(
            "admin_record_visit",
            lambda: request.app.state.admin_service.record_visit(
                memory_instance, memory_id, payload.reason
            ),
        )

    @app.get("/admin/index/overview", summary="Admin index overview")
    def admin_index_overview(request: Request) -> dict[str, Any]:
        """Return an aggregated snapshot of current server-known index state.

        Mirrors the :class:`AdminIndexOverviewResponse` contract — roots,
        jobs, files, limits (always ``current_process_state_only: true``
        in v1), and raw visibility/decay inputs for the CMS.

        Contrast with ``GET /index/status``: ``/index/status`` is a
        compact operational view that returns ``recent_errors`` and a
        per-root summary for the indexing pipeline, whereas this
        endpoint additionally carries the full background-job tail
        and per-file metadata needed for the CMS's visibility-style
        UI.  Both endpoints share the same in-memory data sources;
        neither persists state across process restarts.

        Returns:
            An :class:`AdminIndexOverviewResponse`-compatible dict.
        """
        return _execute_service_call(
            "admin_index_overview",
            lambda: request.app.state.admin_service.index_overview(
                indexing_service=request.app.state.indexing_service,
                job_service=request.app.state.job_service,
                watch_service=request.app.state.watch_service,
            ).model_dump(),
        )

    return app


def _execute_service_call(operation: str, handler: Any) -> Any:
    try:
        return handler()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        trace_backend_error(operation, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


app = create_app()

if __name__ == "__main__":
    import uvicorn

    options = get_runtime_options()
    # uvicorn requires an import string (not an app object) to use workers>1 or reload
    needs_import_string = options.get("workers", 1) > 1 or options.get("reload", False)
    uvicorn.run("server:app" if needs_import_string else app, **options)
