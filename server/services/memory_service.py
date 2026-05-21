from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from api_models import MemoryCreate, RetrieveRequest, SearchRequest

from .anchor_service import AnchorService
from .retrieval_service import RetrievalService
from .runtime import initialize_memory
from .tracing import current_request_id, trace_backend_operation


class MemoryService:
    def __init__(
        self,
        *,
        retrieval_service: RetrievalService,
        anchor_service: AnchorService,
    ) -> None:
        self._retrieval_service = retrieval_service
        self._anchor_service = anchor_service

    def configure(self, app: FastAPI, config: dict[str, Any]) -> dict[str, str]:
        initialize_memory(app, config)
        trace_backend_operation(
            "memory.configure",
            version=config.get("version"),
            vector_provider=config.get("vector_store", {}).get("provider"),
            llm_provider=config.get("llm", {}).get("provider"),
        )
        return {"message": "Configuration set successfully"}

    def add(self, memory_instance: Any, payload: MemoryCreate) -> Any:
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
        response = memory_instance.add(
            messages=[message.model_dump() for message in payload.messages],
            **cleaned_params,
        )
        return self._anchor_service.normalize_payload(response)

    def get_all(
        self,
        memory_instance: Any,
        *,
        user_id: str | None,
        run_id: str | None,
        agent_id: str | None,
    ) -> Any:
        params = self._identifier_params(
            user_id=user_id, run_id=run_id, agent_id=agent_id
        )
        trace_backend_operation("memory.get_all", identifiers=sorted(params.keys()))
        return self._anchor_service.normalize_payload(memory_instance.get_all(**params))

    def get(self, memory_instance: Any, memory_id: str) -> Any:
        trace_backend_operation(
            "memory.get",
            memory_id=memory_id,
            correlation_id=current_request_id(),
        )
        return self._anchor_service.normalize_payload(memory_instance.get(memory_id))

    def search(self, memory_instance: Any, payload: SearchRequest) -> Any:
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
        response = self._retrieval_service.search(
            memory_instance,
            query=payload.query,
            user_id=payload.user_id,
            run_id=payload.run_id,
            agent_id=payload.agent_id,
            filters=filters,
        )
        return self._anchor_service.normalize_payload(response)

    def retrieve(self, memory_instance: Any, payload: RetrieveRequest) -> Any:
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
        return self._anchor_service.normalize_payload(response)

    def update(
        self, memory_instance: Any, memory_id: str, updated_memory: dict[str, Any]
    ) -> Any:
        trace_backend_operation("memory.update", memory_id=memory_id)
        response = memory_instance.update(
            memory_id=memory_id,
            data=self._anchor_service.prepare_update_data(updated_memory),
        )
        return self._anchor_service.normalize_payload(response)

    def history(self, memory_instance: Any, memory_id: str) -> Any:
        trace_backend_operation("memory.history", memory_id=memory_id)
        response = memory_instance.history(memory_id=memory_id)
        return self._normalize_history_payload(response, memory_id=memory_id)

    def delete(self, memory_instance: Any, memory_id: str) -> dict[str, str]:
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
        params = self._identifier_params(
            user_id=user_id, run_id=run_id, agent_id=agent_id
        )
        trace_backend_operation("memory.delete_all", identifiers=sorted(params.keys()))
        memory_instance.delete_all(**params)
        return {"message": "All relevant memories deleted"}

    def reset(self, memory_instance: Any) -> dict[str, str]:
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
