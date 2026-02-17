#!/usr/bin/env python3
"""
Mem0 REST API Server
A FastAPI-based REST server for mem0 memory operations.

Original source: https://code.m3ta.dev/m3tam3re/nixpkgs/src/branch/master/pkgs/mem0/server.py
"""

import logging
import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from mem0 import Memory

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# Configuration from environment variables
def get_config_from_env() -> Dict[str, Any]:
    """Build mem0 configuration from environment variables."""
    config = {"version": "v1.1"}

    # Vector store configuration
    vector_provider = os.environ.get("MEM0_VECTOR_PROVIDER", "pgvector")
    config["vector_store"] = {"provider": vector_provider}

    if vector_provider == "pgvector":
        config["vector_store"]["config"] = {
            "host": os.environ.get("POSTGRES_HOST", "localhost"),
            "port": int(os.environ.get("POSTGRES_PORT", "5432")),
            "dbname": os.environ.get("POSTGRES_DB", "postgres"),
            "user": os.environ.get("POSTGRES_USER", "postgres"),
            "password": os.environ.get("POSTGRES_PASSWORD", "postgres"),
            "collection_name": os.environ.get("POSTGRES_COLLECTION", "mem0_memories"),
        }


    # LLM configuration
    llm_provider = os.environ.get("MEM0_LLM_PROVIDER", "openai")
    config["llm"] = {
        "provider": llm_provider,
        "config": {
            "model": os.environ.get("MEM0_LLM_MODEL", "gpt-5"),
        }
    }

    # Temperature: only include if set (null means use provider default)
    temperature = os.environ.get("MEM0_LLM_TEMPERATURE")
    if temperature is not None:
        config["llm"]["config"]["temperature"] = float(temperature)

    # Extra config: merge JSON env var if provided
    extra_config_json = os.environ.get("MEM0_LLM_EXTRA_CONFIG")
    if extra_config_json:
        import json
        try:
            extra_config = json.loads(extra_config_json)
            config["llm"]["config"].update(extra_config)
        except json.JSONDecodeError:
            logging.warning(f"Failed to parse MEM0_LLM_EXTRA_CONFIG: {extra_config_json}")

    # Add API key if available
    if llm_provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            config["llm"]["config"]["api_key"] = api_key

    # Embedder configuration
    embedder_provider = os.environ.get("MEM0_EMBEDDER_PROVIDER", "openai")
    config["embedder"] = {
        "provider": embedder_provider,
    }

    # Embedder model: only include if provider is set
    if embedder_provider:
        embedder_config = {}
        embedder_model = os.environ.get("MEM0_EMBEDDER_MODEL")
        if embedder_model:
            embedder_config["model"] = embedder_model
        config["embedder"]["config"] = embedder_config

    if embedder_provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            config["embedder"]["config"]["api_key"] = api_key

    # History DB path
    history_db_path = os.environ.get("MEM0_HISTORY_DB_PATH", "/var/lib/mem0/history.db")
    config["history_db_path"] = history_db_path

    return config


# Initialize Memory instance
try:
    config = get_config_from_env()
    logging.info(f"Initializing mem0 with config: {config}")

    # Validate API key is set for OpenAI provider
    if config.get("llm", {}).get("provider") == "openai":
        if not config.get("llm", {}).get("config", {}).get("api_key"):
            logging.error("OPENAI_API_KEY environment variable is required but not set.")
            logging.error("Please set OPENAI_API_KEY environment variable or configure apiKeyFile in NixOS module.")
            sys.exit(1)

    MEMORY_INSTANCE = Memory.from_config(config)
    logging.info("Memory instance initialized successfully")
except Exception as e:
    logging.error(f"Failed to initialize Memory: {e}")
    logging.error("Please check your configuration and ensure all required services are running.")
    sys.exit(1)


app = FastAPI(
    title="Mem0 REST API",
    description="A REST API for managing and searching memories for your AI Agents and Apps.",
    version="1.0.0",
)


class Message(BaseModel):
    role: str = Field(..., description="Role of the message (user or assistant).")
    content: str = Field(..., description="Message content.")


class MemoryCreate(BaseModel):
    messages: List[Message] = Field(..., description="List of messages to store.")
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class SearchRequest(BaseModel):
    query: str = Field(..., description="Search query.")
    user_id: Optional[str] = None
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    filters: Optional[Dict[str, Any]] = None


@app.get("/", summary="Redirect to documentation", include_in_schema=False)
def home():
    """Redirect to the OpenAPI documentation."""
    return RedirectResponse(url="/docs")


@app.get("/health", summary="Health check")
def health():
    """Check if the server is running."""
    return {"status": "healthy", "service": "mem0-api"}


@app.post("/configure", summary="Configure Mem0")
def set_config(config: Dict[str, Any]):
    """Set memory configuration."""
    global MEMORY_INSTANCE
    MEMORY_INSTANCE = Memory.from_config(config)
    return {"message": "Configuration set successfully"}


@app.post("/memories", summary="Create memories")
def add_memory(memory_create: MemoryCreate):
    """Store new memories."""
    if not any([memory_create.user_id, memory_create.agent_id, memory_create.run_id]):
        raise HTTPException(status_code=400, detail="At least one identifier (user_id, agent_id, run_id) is required.")

    params = {k: v for k, v in memory_create.model_dump().items() if v is not None and k != "messages"}
    try:
        response = MEMORY_INSTANCE.add(messages=[m.model_dump() for m in memory_create.messages], **params)
        return JSONResponse(content=response)
    except Exception as e:
        logging.exception("Error in add_memory:")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memories", summary="Get memories")
def get_all_memories(
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
):
    """Retrieve stored memories."""
    if not any([user_id, run_id, agent_id]):
        raise HTTPException(status_code=400, detail="At least one identifier is required.")
    try:
        params = {
            k: v for k, v in {"user_id": user_id, "run_id": run_id, "agent_id": agent_id}.items() if v is not None
        }
        return MEMORY_INSTANCE.get_all(**params)
    except Exception as e:
        logging.exception("Error in get_all_memories:")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memories/{memory_id}", summary="Get a memory")
def get_memory(memory_id: str):
    """Retrieve a specific memory by ID."""
    try:
        return MEMORY_INSTANCE.get(memory_id)
    except Exception as e:
        logging.exception("Error in get_memory:")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/search", summary="Search memories")
def search_memories(search_req: SearchRequest):
    """Search for memories based on a query."""
    try:
        params = {k: v for k, v in search_req.model_dump().items() if v is not None and k != "query"}
        return MEMORY_INSTANCE.search(query=search_req.query, **params)
    except Exception as e:
        logging.exception("Error in search_memories:")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/memories/{memory_id}", summary="Update a memory")
def update_memory(memory_id: str, updated_memory: Dict[str, Any]):
    """Update an existing memory with new content."""
    try:
        return MEMORY_INSTANCE.update(memory_id=memory_id, data=updated_memory)
    except Exception as e:
        logging.exception("Error in update_memory:")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memories/{memory_id}/history", summary="Get memory history")
def memory_history(memory_id: str):
    """Retrieve memory history."""
    try:
        return MEMORY_INSTANCE.history(memory_id=memory_id)
    except Exception as e:
        logging.exception("Error in memory_history:")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/memories/{memory_id}", summary="Delete a memory")
def delete_memory(memory_id: str):
    """Delete a specific memory by ID."""
    try:
        MEMORY_INSTANCE.delete(memory_id=memory_id)
        return {"message": "Memory deleted successfully"}
    except Exception as e:
        logging.exception("Error in delete_memory:")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/memories", summary="Delete all memories")
def delete_all_memories(
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
):
    """Delete all memories for a given identifier."""
    if not any([user_id, run_id, agent_id]):
        raise HTTPException(status_code=400, detail="At least one identifier is required.")
    try:
        params = {
            k: v for k, v in {"user_id": user_id, "run_id": run_id, "agent_id": agent_id}.items() if v is not None
        }
        MEMORY_INSTANCE.delete_all(**params)
        return {"message": "All relevant memories deleted"}
    except Exception as e:
        logging.exception("Error in delete_all_memories:")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reset", summary="Reset all memories")
def reset_memory():
    """Completely reset stored memories."""
    try:
        MEMORY_INSTANCE.reset()
        return {"message": "All memories reset"}
    except Exception as e:
        logging.exception("Error in reset_memory:")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("MEM0_HOST", "127.0.0.1")
    port = int(os.environ.get("MEM0_PORT", "8000"))
    workers = int(os.environ.get("MEM0_WORKERS", "1"))
    log_level = os.environ.get("MEM0_LOG_LEVEL", "info")

    uvicorn.run(app, host=host, port=port, workers=workers, log_level=log_level)

