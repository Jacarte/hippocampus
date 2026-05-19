from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from mem0 import Memory

from api_models import (
    CapabilitiesResponse,
    IndexResetRequest,
    IndexSyncRequest,
    MemoryCreate,
    RetrieveRequest,
    SearchRequest,
    UnifiedQueryRequest,
    WatchStartRequest,
    WatchStopRequest,
)
from services.anchor_service import AnchorService
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
)
from services.tracing import (
    bind_request_id,
    reset_request_id,
    resolve_request_id,
    trace_backend_error,
    trace_backend_request_complete,
    trace_backend_request_start,
)

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
            trace_backend_request_complete(
                request.method,
                request.url.path,
                status_code=500,
                latency_ms=latency_ms,
            )
            raise
        else:
            response.headers["X-Correlation-ID"] = request_id
            trace_backend_request_complete(
                request.method,
                request.url.path,
                status_code=response.status_code,
                latency_ms=(time.perf_counter() - started_at) * 1000,
            )
            return response
        finally:
            reset_request_id(token)

    if startup_enabled:

        @app.on_event("startup")
        def startup_initialize_memory() -> None:
            initialize_memory(app)

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
        return _execute_service_call(
            "unified_query",
            lambda: request.app.state.query_service.query(
                query_text=query_req.query,
                corpora=query_req.corpora,
                limit=query_req.limit,
                path_filter=query_req.path_filter,
                language_filter=query_req.language_filter,
                scope_filter=query_req.scope_filter,
            ),
        )

    @app.post("/index/sync", summary="Sync a root directory into the file corpus")
    def index_sync(sync_req: IndexSyncRequest, request: Request) -> Any:
        return _execute_service_call(
            "index_sync",
            lambda: request.app.state.indexing_service.sync(sync_req.root),
        )

    @app.post("/index/watch/start", summary="Start watching a root directory")
    def index_watch_start(watch_req: WatchStartRequest, request: Request) -> dict[str, Any]:
        return _execute_service_call(
            "index_watch_start",
            lambda: (
                request.app.state.watch_service.start(watch_req.root),
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
            lambda: request.app.state.indexing_service.status(),
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

    return app


def _execute_service_call(operation: str, handler: Any) -> Any:
    try:
        return handler()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        trace_backend_error(operation, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, **get_runtime_options())
