from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
# Unified query & index lifecycle models
# ---------------------------------------------------------------------------

CorpusType = Literal["memory_store", "file_corpus", "all"]


class FileHit(BaseModel):
    path: str
    language: str
    symbol_name: str | None = None
    symbol_kind: str | None = None
    line_start: int
    line_end: int
    snippet: str
    score: float
    corpus: str = "file_corpus"


class MemoryHit(BaseModel):
    memory_id: str
    content: str
    score: float
    corpus: str = "memory_store"
    metadata: dict | None = None


class UnifiedQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Search query.")
    corpora: list[CorpusType] = Field(default=["all"])
    limit: int = Field(10, ge=1, le=50)
    path_filter: str | None = None
    language_filter: str | None = None
    scope_filter: str | None = None


class UnifiedQueryResponse(BaseModel):
    hits: list[FileHit | MemoryHit]
    total: int
    corpora_queried: list[str]
    degraded: bool = False
    degradation_reasons: list[str] = []


class IndexSyncRequest(BaseModel):
    root: str


class IndexSyncResponse(BaseModel):
    root: str
    files_indexed: int
    chunks_indexed: int
    synced_at: datetime


class IndexStatusResponse(BaseModel):
    roots: list[dict]
    total_files: int
    total_chunks: int


class IndexResetRequest(BaseModel):
    confirm: bool = False

    @model_validator(mode="after")
    def require_confirm(self) -> "IndexResetRequest":
        if not self.confirm:
            raise ValueError("confirm must be True to reset the index")
        return self


class IndexResetResponse(BaseModel):
    files_cleared: int
    chunks_cleared: int
    reset_at: datetime


class WatchStartRequest(BaseModel):
    root: str


class WatchStopRequest(BaseModel):
    root: str


class CapabilitiesResponse(BaseModel):
    memory_store: dict[str, Any]
    file_corpus: dict[str, Any]
