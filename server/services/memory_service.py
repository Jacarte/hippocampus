from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import logging
import time
from typing import Any

from fastapi import FastAPI

from api_models import MemoryCreate, RetrieveRequest, SearchRequest

from . import query_events
from .anchor_service import AnchorService
from .mem0_compat import MemoryWriteError, add_memories
from .metrics import (
    memory_operation_errors_total,
    memory_operations_total,
    memory_retrieve_hits,
    memory_retrieve_total,
)
from .retrieval_service import RetrievalService
from .runtime import initialize_memory
from .tracing import current_request_id, trace_backend_operation
from .write_receipts import WriteReceiptStore


logger = logging.getLogger(__name__)


class MemoryService:
    def __init__(
        self,
        *,
        retrieval_service: RetrievalService,
        anchor_service: AnchorService,
        write_receipts: WriteReceiptStore | None = None,
    ) -> None:
        self._retrieval_service = retrieval_service
        self._anchor_service = anchor_service
        self._write_receipts = write_receipts or WriteReceiptStore()

    @contextmanager
    def _track_op(
        self, operation: str, user_id: str = "", agent_id: str = ""
    ) -> None:
        memory_operations_total.labels(
            operation=operation, user_id=user_id, agent_id=agent_id
        ).inc()
        try:
            yield
        except Exception:
            memory_operation_errors_total.labels(operation=operation).inc()
            raise

    @staticmethod
    def _count_hits(response: Any, user_id: str, agent_id: str) -> None:
        results: list[Any] = []
        if isinstance(response, dict):
            results = response.get("results", [])
        elif isinstance(response, list):
            results = response
        if results:
            memory_retrieve_hits.labels(
                user_id=user_id, agent_id=agent_id
            ).inc()

    def configure(self, app: FastAPI, config: dict[str, Any]) -> dict[str, str]:
        with self._track_op("configure"):
            initialize_memory(app, config)
            trace_backend_operation(
                "memory.configure",
                version=config.get("version"),
                vector_provider=config.get("vector_store", {}).get("provider"),
                llm_provider=config.get("llm", {}).get("provider"),
            )
            return {"message": "Configuration set successfully"}

    def add(
        self, memory_instance: Any, payload: MemoryCreate,
        *, runtime_config: dict[str, Any] | None = None,
    ) -> Any:
        with self._track_op(
            "add",
            user_id=payload.user_id or "",
            agent_id=payload.agent_id or "",
        ):
            identifiers = [payload.user_id, payload.agent_id, payload.run_id]
            if not any(identifiers):
                raise ValueError(
                    "At least one identifier (user_id, agent_id, run_id) is required."
                )

            prepared_metadata = self._anchor_service.prepare_metadata(
                payload.metadata,
                write_context={
                    "user_id": payload.user_id,
                    "agent_id": payload.agent_id,
                    "run_id": payload.run_id,
                },
            )
            params = {
                "user_id": payload.user_id,
                "agent_id": payload.agent_id,
                "run_id": payload.run_id,
                "metadata": prepared_metadata,
                "infer": payload.infer,
            }
            if payload.capture_id is not None:
                params["metadata"] = {
                    **(prepared_metadata or {}), "capture_id": payload.capture_id,
                }
            cleaned_params = {
                key: value for key, value in params.items() if value is not None
            }
            trace_backend_operation(
                "memory.add",
                identifiers=sorted(cleaned_params.keys()),
                anchor_state=self._anchor_service.capability_snapshot()["status"],
                message_count=len(payload.messages),
                has_metadata=payload.metadata is not None,
            )
            claim = None
            if payload.capture_id is not None:
                claim = self._write_receipts.claim(
                    payload.capture_id,
                    namespace=self._write_namespace(
                        getattr(memory_instance, "config", None) or runtime_config or {},
                    ),
                    identifiers={
                        "user_id": payload.user_id,
                        "agent_id": payload.agent_id,
                        "run_id": payload.run_id,
                    },
                    # Fingerprint original client input, not generated anchor times.
                    payload=payload.model_dump(mode="json"),
                )
                if claim.replayed:
                    return {**claim.response, "capture_id": payload.capture_id}
            try:
                response = add_memories(
                    memory_instance,
                    messages=[message.model_dump() for message in payload.messages],
                    **cleaned_params,
                )
                response = self._write_result(
                    self._anchor_service.normalize_payload(response), payload.capture_id,
                )
                if claim is not None:
                    self._write_receipts.complete(
                        claim, {key: value for key, value in response.items() if key != "capture_id"},
                    )
                return response
            except Exception as exc:
                if claim is not None:
                    self._write_receipts.fail(
                        claim,
                        retryable=isinstance(exc, MemoryWriteError) and exc.retryable,
                    )
                raise

    @staticmethod
    def _write_namespace(config: Any) -> str:
        import os
        import shlex
        from urllib.parse import parse_qsl, unquote, urlsplit

        if hasattr(config, "model_dump"):
            # Preserve opaque values long enough to reject them explicitly.
            config = config.model_dump(mode="python")
        if not isinstance(config, dict):
            raise ValueError("Cannot identify the memory storage target for capture_id.")
        vector_store = config.get("vector_store", {})
        settings = vector_store.get("config", {})
        provider = vector_store.get("provider")
        if not provider or not isinstance(settings, dict):
            raise ValueError("Cannot identify the memory storage target for capture_id.")
        if any(settings.get(key) is not None for key in ("connection_pool", "client")):
            raise ValueError(
                "capture_id requires an explicit storage target; opaque clients or "
                "connection pools cannot be identified safely."
            )

        if provider == "pgvector":
            from psycopg.conninfo import conninfo_to_dict

            # PGVector gives connection_string precedence over individual fields.
            if settings.get("connection_string"):
                try:
                    connection = conninfo_to_dict(settings["connection_string"])
                except Exception:
                    raise ValueError("Cannot identify the PostgreSQL connection target.") from None
                if connection.get("service") or os.environ.get("PGSERVICE"):
                    raise ValueError("capture_id cannot identify a PostgreSQL service-file target.")
                # libpq applies these environment defaults to omitted DSN fields.
                for key, variable in {
                    "host": "PGHOST", "hostaddr": "PGHOSTADDR", "port": "PGPORT",
                    "dbname": "PGDATABASE", "user": "PGUSER", "options": "PGOPTIONS",
                }.items():
                    if key not in connection and os.environ.get(variable):
                        connection[key] = os.environ[variable]
            else:
                connection = settings
            if not (connection.get("host") or connection.get("hostaddr")) or not connection.get("user"):
                raise ValueError("capture_id requires an explicit PostgreSQL host and user.")
            target_config = {
                key: str(connection[key])
                for key in ("host", "hostaddr", "user") if connection.get(key)
            }
            target_config["port"] = str(connection.get("port") or "5432")
            target_config["dbname"] = str(connection.get("dbname") or connection["user"])
            target_config["collection_name"] = settings.get("collection_name", "mem0")
            # Only session settings that change table/tenant resolution belong
            # in the target; statement_timeout and other tuning do not.
            try:
                options = iter(shlex.split(connection.get("options") or ""))
                for option in options:
                    if option == "-c":
                        assignment = next(options)
                    elif option.startswith("-c"):
                        assignment = option[2:]
                    elif option.startswith("--"):
                        assignment = option[2:]
                    else:
                        raise ValueError
                    name, value = assignment.split("=", 1)
                    name = name.replace("-", "_")
                    if name in {"search_path", "role", "session_authorization"}:
                        target_config[name] = value
            except (TypeError, ValueError, StopIteration):
                raise ValueError("Cannot identify PostgreSQL session target options.") from None
            for key in ("schema", "search_path", "role"):
                if connection.get(key) is not None:
                    target_config[key] = connection[key]
            target = {"provider": provider, "config": target_config}
            encoded = json.dumps(target, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

        if settings.get("connection_string"):
            raise ValueError("capture_id cannot identify this provider's connection_string target.")

        def url_target(value: Any) -> dict[str, Any]:
            if not isinstance(value, str) or not value:
                raise ValueError("Cannot identify the memory storage URL target.")
            try:
                parsed = urlsplit(value if "://" in value else "//" + value)
                if not parsed.hostname and parsed.scheme != "file":
                    raise ValueError
                query: dict[str, list[str]] = {}
                for key, item in parse_qsl(parsed.query, keep_blank_values=True):
                    normalized = key.casefold().replace("-", "_")
                    if any(secret in normalized for secret in (
                        "password", "secret", "token", "api_key", "authorization", "signature",
                    )) or normalized in {"sig", "access_key"}:
                        continue
                    query.setdefault(key, []).append(item)
                return {
                    "scheme": parsed.scheme,
                    "host": parsed.hostname,
                    "port": parsed.port or {"http": 80, "https": 443}.get(parsed.scheme),
                    "user": unquote(parsed.username) if parsed.username else None,
                    "path": parsed.path,
                    "query": query,
                }
            except (TypeError, ValueError):
                raise ValueError("Cannot identify the memory storage URL target.") from None

        # Use storage identity, not tuning or credentials: changing hnsw or
        # rotating a password must not make an old capture executable again.
        locator_keys = {
            "host", "port", "url", "uri", "path", "dbname", "database",
            "db_name", "schema", "collection_name", "collection", "index_name",
            "index", "namespace", "project_id", "project", "region", "bucket",
            "table_name", "container_name", "account_url", "endpoint",
        }
        target_config = {
            key: url_target(value) if key in {"url", "uri", "account_url", "endpoint"} else value
            for key, value in settings.items()
            if key in locator_keys and value is not None
        }
        if not target_config or not any(key in target_config for key in (
            "host", "url", "uri", "path", "database", "dbname", "db_name",
            "project_id", "project", "bucket", "account_url", "endpoint",
        )):
            if provider != "qdrant":
                raise ValueError("Cannot identify an explicit memory storage target for capture_id.")
            # The known Qdrant default remains compatible with minimal configs.
            target_config["path"] = "/tmp/qdrant"
        target = {"provider": provider, "config": target_config}
        encoded = json.dumps(target, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _write_result(response: Any, capture_id: str | None) -> dict[str, Any]:
        if isinstance(response, list):
            response = {"results": response}
        if not isinstance(response, dict):
            raise MemoryWriteError("Memory backend returned an unrecognized write result.")
        response = dict(response)
        if isinstance(response.get("results"), list):
            records = response["results"]
        elif response.get("id") or response.get("memory_id"):
            records = [response]
        else:
            raise MemoryWriteError("Memory backend returned an unrecognized write result.")
        if any(
            not isinstance(record, dict) or not (record.get("id") or record.get("memory_id"))
            for record in records
        ):
            raise MemoryWriteError("Memory backend did not identify its persisted results.")
        response["write_status"] = "saved" if records else "skipped"
        if not records:
            response["skip_reason"] = "no_new_memories"
        if capture_id is not None:
            # The new opt-in contract is a stable acknowledgement, not another
            # retained copy of memory text that could outlive a later deletion.
            response = {
                "results": [
                    {"id": record.get("id") or record["memory_id"],
                     "event": record.get("event", "ADD")}
                    for record in records
                ],
                "write_status": response["write_status"],
                "capture_id": capture_id,
                **({"skip_reason": "no_new_memories"} if not records else {}),
            }
        return response

    def get_all(
        self,
        memory_instance: Any,
        *,
        user_id: str | None,
        run_id: str | None,
        agent_id: str | None,
    ) -> Any:
        with self._track_op(
            "get_all", user_id=user_id or "", agent_id=agent_id or ""
        ):
            params = self._identifier_params(
                user_id=user_id, run_id=run_id, agent_id=agent_id
            )
            trace_backend_operation(
                "memory.get_all", identifiers=sorted(params.keys())
            )
            # mem0 2.0.0 requires entity-id keys inside ``filters``; the
            # helper on :class:`RetrievalService` centralises the
            # translation and pins the explicit ``top_k`` default so the
            # call shape never silently regresses.
            mem0_kwargs = self._retrieval_service.build_mem0_get_all_kwargs(
                identifier_params=params
            )
            return self._anchor_service.normalize_payload(
                memory_instance.get_all(**mem0_kwargs)
            )

    def get(self, memory_instance: Any, memory_id: str) -> Any:
        with self._track_op("get"):
            trace_backend_operation(
                "memory.get",
                memory_id=memory_id,
                correlation_id=current_request_id(),
            )
            return self._anchor_service.normalize_payload(
                memory_instance.get(memory_id)
            )

    def search(self, memory_instance: Any, payload: SearchRequest) -> Any:
        uid = payload.user_id or ""
        aid = payload.agent_id or ""
        memory_retrieve_total.labels(user_id=uid, agent_id=aid).inc()
        started_at = time.perf_counter()
        with self._track_op("search", user_id=uid, agent_id=aid):
            filters = self._anchor_service.prepare_filters(payload.filters)
            trace_backend_operation(
                "memory.search",
                has_filters=filters is not None,
                anchor_state=self._anchor_service.capability_snapshot()["status"],
                query_length=len(payload.query),
                identifiers=sorted(
                    key
                    for key, value in {
                        "user_id": payload.user_id,
                        "run_id": payload.run_id,
                        "agent_id": payload.agent_id,
                    }.items()
                    if value is not None
                ),
            )
            try:
                response = self._retrieval_service.search(
                    memory_instance,
                    query=payload.query,
                    user_id=payload.user_id,
                    run_id=payload.run_id,
                    agent_id=payload.agent_id,
                    filters=filters,
                )
            except Exception as exc:
                self._emit_failed_query_event("search", payload, exc, started_at)
                raise
            normalized = self._anchor_service.normalize_payload(response)
        self._count_hits(normalized, user_id=uid, agent_id=aid)
        self._emit_query_events("search", payload, normalized, started_at)
        return normalized

    def retrieve(self, memory_instance: Any, payload: RetrieveRequest) -> Any:
        uid = payload.user_id or ""
        aid = payload.agent_id or ""
        memory_retrieve_total.labels(user_id=uid, agent_id=aid).inc()
        started_at = time.perf_counter()
        with self._track_op("retrieve", user_id=uid, agent_id=aid):
            identifiers = self._identifier_params(
                user_id=payload.user_id,
                run_id=payload.run_id,
                agent_id=payload.agent_id,
            )
            filters = self._anchor_service.prepare_filters(payload.filters)
            trace_backend_operation(
                "memory.retrieve",
                has_filters=filters is not None,
                anchor_state=self._anchor_service.capability_snapshot()["status"],
                query_length=len(payload.query),
                scopes=payload.scopes,
                limit=payload.limit,
                identifiers=sorted(identifiers.keys()),
            )
            try:
                response = self._retrieval_service.retrieve(
                    memory_instance,
                    query=payload.query,
                    scopes=payload.scopes,
                    user_id=payload.user_id,
                    run_id=payload.run_id,
                    agent_id=payload.agent_id,
                    limit=payload.limit,
                    filters=filters,
                )
            except Exception as exc:
                self._emit_failed_query_event("retrieve", payload, exc, started_at)
                raise
            normalized = self._anchor_service.normalize_payload(response)
        self._count_hits(normalized, user_id=uid, agent_id=aid)
        self._emit_query_events("retrieve", payload, normalized, started_at)
        return normalized

    def _emit_query_events(
        self,
        endpoint: str,
        payload: SearchRequest | RetrieveRequest,
        response: Any,
        started_at: float,
    ) -> None:
        """Emit stdout query/hit events; never raises or alters the response."""
        try:
            project = query_events.derive_project(payload.user_id, payload.project)
            hits = query_events.extract_memory_hits(response)
            summary = query_events.summarize_retrieval(response)
            query_events.emit_query_event(
                endpoint=endpoint,
                query=payload.query,
                filters=payload.filters,
                scopes=getattr(payload, "scopes", None),
                limit=getattr(payload, "limit", None),
                user_id=payload.user_id,
                agent_id=payload.agent_id,
                run_id=payload.run_id,
                project=project,
                hits=len(hits),
                lexical=summary["lexical"],
                semantic=summary["semantic"],
                rerank=summary["rerank"],
                degraded=summary["degraded"],
                degradation_reasons=summary["degradation_reasons"],
                latency_ms=(time.perf_counter() - started_at) * 1000,
            )
            query_events.emit_memory_hits(
                endpoint=endpoint,
                hits=hits,
                user_id=payload.user_id,
                agent_id=payload.agent_id,
                project=project,
            )
        except Exception:  # noqa: BLE001
            logger.debug("query event emission failed", exc_info=True)

    @staticmethod
    def _emit_failed_query_event(
        endpoint: str,
        payload: SearchRequest | RetrieveRequest,
        exc: Exception,
        started_at: float,
    ) -> None:
        """Emit a degraded zero-hit event for a hard backend failure.

        Best effort: never raises, so the caller always re-raises the original
        exception unchanged.
        """
        try:
            query_events.emit_query_event(
                endpoint=endpoint,
                query=payload.query,
                filters=payload.filters,
                scopes=getattr(payload, "scopes", None),
                limit=getattr(payload, "limit", None),
                user_id=payload.user_id,
                agent_id=payload.agent_id,
                run_id=payload.run_id,
                project=query_events.derive_project(payload.user_id, payload.project),
                hits=0,
                degraded=True,
                degradation_reasons=[type(exc).__name__],
                latency_ms=(time.perf_counter() - started_at) * 1000,
            )
        except Exception:  # noqa: BLE001
            logger.debug("query event emission failed", exc_info=True)

    def update(
        self, memory_instance: Any, memory_id: str, updated_memory: dict[str, Any]
    ) -> Any:
        with self._track_op("update"):
            trace_backend_operation("memory.update", memory_id=memory_id)
            response = memory_instance.update(
                memory_id=memory_id,
                data=self._anchor_service.prepare_update_data(updated_memory),
            )
            return self._anchor_service.normalize_payload(response)

    def history(self, memory_instance: Any, memory_id: str) -> Any:
        with self._track_op("history"):
            trace_backend_operation("memory.history", memory_id=memory_id)
            response = memory_instance.history(memory_id=memory_id)
            return self._normalize_history_payload(response, memory_id=memory_id)

    def delete(self, memory_instance: Any, memory_id: str) -> dict[str, str]:
        with self._track_op("delete"):
            trace_backend_operation("memory.delete", memory_id=memory_id)
            memory_instance.delete(memory_id=memory_id)
            return {"message": "Memory deleted successfully"}

    def delete_all(
        self,
        memory_instance: Any,
        *,
        user_id: str | None,
        run_id: str | None,
        agent_id: str | None,
    ) -> dict[str, str]:
        with self._track_op(
            "delete_all", user_id=user_id or "", agent_id=agent_id or ""
        ):
            params = self._identifier_params(
                user_id=user_id, run_id=run_id, agent_id=agent_id
            )
            trace_backend_operation(
                "memory.delete_all", identifiers=sorted(params.keys())
            )
            memory_instance.delete_all(**params)
            return {"message": "All relevant memories deleted"}

    def reset(self, memory_instance: Any) -> dict[str, str]:
        with self._track_op("reset"):
            trace_backend_operation("memory.reset")
            memory_instance.reset()
            return {"message": "All memories reset"}

    @staticmethod
    def _identifier_params(
        *, user_id: str | None, run_id: str | None, agent_id: str | None
    ) -> dict[str, str]:
        params = {
            key: value
            for key, value in {
                "user_id": user_id,
                "run_id": run_id,
                "agent_id": agent_id,
            }.items()
            if value is not None
        }
        if not params:
            raise ValueError("At least one identifier is required.")
        return params

    def _normalize_history_payload(
        self,
        payload: Any,
        *,
        memory_id: str,
    ) -> dict[str, Any]:
        normalized_payload: dict[str, Any] = {
            "memory_id": memory_id,
            "results": self._normalize_history_entries(payload),
            "backend_capabilities": {"anchors": True},
        }

        if isinstance(payload, dict):
            normalized_payload = dict(payload)
            normalized_payload["memory_id"] = normalized_payload.get(
                "memory_id", memory_id
            )
            normalized_payload["results"] = self._normalize_history_entries(
                normalized_payload.get("results", payload)
            )

            backend_capabilities = normalized_payload.get("backend_capabilities")
            if not isinstance(backend_capabilities, dict):
                backend_capabilities = {}
            normalized_payload["backend_capabilities"] = {
                **backend_capabilities,
                "anchors": True,
            }

        return normalized_payload

    def _normalize_history_entries(self, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            entries = payload
        elif isinstance(payload, dict) and isinstance(payload.get("results"), list):
            entries = payload["results"]
        else:
            entries = [payload]

        normalized_entries: list[Any] = []
        for entry in entries:
            if isinstance(entry, dict):
                normalized_entries.append(self._anchor_service.normalize_record(entry))
            else:
                normalized_entries.append(entry)
        return normalized_entries
