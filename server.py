#!/usr/bin/env python3
"""
Mem0 REST API Server
A FastAPI-based REST server for mem0 memory operations.

Original source: https://code.m3ta.dev/m3tam3re/nixpkgs/src/branch/master/pkgs/mem0/server.py
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from mem0 import Memory

from api_models import MemoryCreate, RetrieveRequest, SearchRequest
from services.anchor_service import AnchorService
from services.memory_service import MemoryService
from services.retrieval_service import RetrievalService
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
