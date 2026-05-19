from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .tracing import trace_backend_operation


MemoryFactory = Callable[[dict[str, Any]], Any]
_SENSITIVE_CONFIG_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
}


def get_config_from_env() -> dict[str, Any]:
    config: dict[str, Any] = {"version": "v1.1"}

    vector_provider = os.environ.get("MEM0_VECTOR_PROVIDER", "pgvector")
    vector_store: dict[str, Any] = {"provider": vector_provider}
    if vector_provider == "pgvector":
        vector_store["config"] = {
            "host": os.environ.get("POSTGRES_HOST", "localhost"),
            "port": int(os.environ.get("POSTGRES_PORT", "5432")),
            "dbname": os.environ.get("POSTGRES_DB", "postgres"),
            "user": os.environ.get("POSTGRES_USER", "postgres"),
            "password": os.environ.get("POSTGRES_PASSWORD", "postgres"),
            "collection_name": os.environ.get("POSTGRES_COLLECTION", "mem0_memories"),
        }
    config["vector_store"] = vector_store

    llm_provider = os.environ.get("MEM0_LLM_PROVIDER", "openai")
    llm_config: dict[str, Any] = {"model": os.environ.get("MEM0_LLM_MODEL", "gpt-5")}
    temperature = os.environ.get("MEM0_LLM_TEMPERATURE")
    if temperature is not None:
        llm_config["temperature"] = float(temperature)

    extra_config_json = os.environ.get("MEM0_LLM_EXTRA_CONFIG")
    if extra_config_json:
        try:
            extra_config = json.loads(extra_config_json)
            if isinstance(extra_config, dict):
                llm_config.update(extra_config)
            else:
                logging.warning(
                    "MEM0_LLM_EXTRA_CONFIG must be a JSON object; ignoring non-object value."
                )
        except json.JSONDecodeError:
            logging.warning(
                "Failed to parse MEM0_LLM_EXTRA_CONFIG: %s", extra_config_json
            )

    if llm_provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            llm_config["api_key"] = api_key
    config["llm"] = {"provider": llm_provider, "config": llm_config}

    embedder_provider = os.environ.get("MEM0_EMBEDDER_PROVIDER", "openai")
    embedder: dict[str, Any] = {"provider": embedder_provider}
    if embedder_provider:
        embedder_config: dict[str, Any] = {}
        embedder_model = os.environ.get("MEM0_EMBEDDER_MODEL")
        if embedder_model:
            embedder_config["model"] = embedder_model
        embedder["config"] = embedder_config

    if embedder_provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            embedder.setdefault("config", {})["api_key"] = api_key
    config["embedder"] = embedder
    config["history_db_path"] = os.environ.get(
        "MEM0_HISTORY_DB_PATH", "/var/lib/mem0/history.db"
    )
    return config


def validate_memory_config(config: dict[str, Any]) -> None:
    if config.get("llm", {}).get("provider") == "openai" and not config.get(
        "llm", {}
    ).get("config", {}).get("api_key"):
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is required but not set. "
            "Please set OPENAI_API_KEY or configure a non-OpenAI provider."
        )


def build_memory_instance(config: dict[str, Any], memory_factory: MemoryFactory) -> Any:
    trace_backend_operation("memory.initialize", version=config.get("version"))
    logging.info("Initializing mem0 with config: %s", _sanitize_config_for_log(config))
    validate_memory_config(config)
    try:
        memory_instance = memory_factory(config)
    except Exception as exc:
        logging.error("Failed to initialize Memory: %s", exc)
        logging.error(
            "Please check your configuration and ensure all required services are running."
        )
        raise RuntimeError("Failed to initialize Memory instance.") from exc

    logging.info("Memory instance initialized successfully")
    return memory_instance


def _sanitize_config_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if _is_sensitive_config_key(key)
                else _sanitize_config_for_log(nested_value)
            )
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_config_for_log(item) for item in value]
    return value


def _is_sensitive_config_key(key: str) -> bool:
    normalized_key = key.strip().casefold()
    return any(
        sensitive_key in normalized_key for sensitive_key in _SENSITIVE_CONFIG_KEYS
    )


def initialize_memory(app: FastAPI, config: dict[str, Any] | None = None) -> Any:
    resolved_config = config or get_config_from_env()
    memory_instance = build_memory_instance(resolved_config, app.state.memory_factory)
    app.state.memory = memory_instance
    app.state.memory_config = resolved_config
    return memory_instance


def get_memory_instance(request: Request) -> Any:
    memory_instance = getattr(request.app.state, "memory", None)
    if memory_instance is None:
        raise HTTPException(
            status_code=503, detail="Memory instance is not initialized."
        )
    return memory_instance


def get_runtime_options() -> dict[str, Any]:
    return {
        "host": os.environ.get("MEM0_HOST", "0.0.0.0"),
        "port": int(os.environ.get("MEM0_PORT", "8000")),
        "workers": int(os.environ.get("MEM0_WORKERS", "1")),
        "log_level": os.environ.get("MEM0_LOG_LEVEL", "info"),
    }


def is_chunk_memory_enabled() -> bool:
    """Return True if chunk-level memory is enabled via environment variable.

    Reads the ``USE_CHUNK_MEMORY`` environment variable and treats the values
    ``"1"``, ``"true"``, and ``"yes"`` (case-insensitive) as *enabled*.  Any
    other value—including an unset variable—is treated as *disabled* and
    returns ``False``.
    """
    value = os.environ.get("USE_CHUNK_MEMORY", "").strip().lower()
    return value in {"1", "true", "yes"}
