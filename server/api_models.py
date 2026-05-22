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
            "Minimum relevance score for memory-store hits.  Hits from the "
            "memory corpus with a score strictly below this value are excluded. "
            "Defaults to 0.5.  Set to 0.0 to disable filtering for this corpus."
        ),
    )
    min_score_files: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum relevance score for file-corpus hits.  Hits from the file "
            "corpus with a score strictly below this value are excluded. "
            "Defaults to 0.05 (matches typical BM25 noise floor).  "
            "Set to 0.0 to disable filtering for this corpus."
        ),
    )


class UnifiedQueryResponse(BaseModel):
    hits: list[FileHit | MemoryHit]
    total: int
    corpora_queried: list[str]
    degraded: bool = False
    degradation_reasons: list[str] = []


class IndexSyncRequest(BaseModel):
    root: str
    generate_summaries: bool = Field(
        default=False,
        description=(
            "When True, the indexing pipeline generates natural-language summaries "
            "for each indexed chunk. Disabled by default to keep sync fast."
        ),
    )


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
    generate_summaries: bool = Field(
        default=False,
        description=(
            "When True, newly detected files will have chunk summaries generated "
            "as they are indexed by the file watcher. Disabled by default."
        ),
    )


class WatchStopRequest(BaseModel):
    root: str
    generate_summaries: bool = Field(
        default=False,
        description=(
            "Forwarded from the originating WatchStartRequest so the stop handler "
            "can clean up any summary-generation state. Defaults to False."
        ),
    )


class CapabilitiesResponse(BaseModel):
    memory_store: dict[str, Any]
    file_corpus: dict[str, Any]


class FileContent(BaseModel):
    """A single file's path and raw content, as submitted by the client."""

    file_path: str = Field(..., description="Relative file path (e.g. 'src/main.rs').")
    content: str = Field(..., description="Full raw text content of the file.")


class FileIngestRequest(BaseModel):
    """Request body for POST /index/ingest."""

    root: str = Field(
        ...,
        description=(
            "Logical root label for this batch (e.g. the project path on the client). "
            "Used as the corpus namespace when *project_id* is not provided. "
            "Does not need to exist on the server."
        ),
    )
    project_id: str | None = Field(
        default=None,
        description=(
            "Optional stable project identifier (e.g. 'my-app', 'backend-service'). "
            "When provided, used as the corpus namespace instead of *root*, so that "
            "chunks from the same project indexed from different machines or paths "
            "are kept together and do not overlap with other projects."
        ),
    )
    files: list[FileContent] = Field(
        ...,
        min_length=1,
        description="Files to ingest. Each entry carries the relative path and full content.",
    )
    generate_summaries: bool = Field(
        default=False,
        description=(
            "When True, generate LLM summaries for each indexed chunk "
            "(requires memory to be configured)."
        ),
    )


class FileChunksRequest(BaseModel):
    file_path: str = Field(
        description="Relative file path as stored in the index (e.g. 'src/main.rs').",
    )
    root: str | None = Field(
        default=None,
        description=(
            "Root namespace to scope the query. "
            "When omitted, chunks from all roots are returned."
        ),
    )
    include_embeddings: bool = Field(
        default=False,
        description=(
            "When True, include raw summary_embedding vectors in the response. "
            "When False (default), each chunk carries a boolean has_summary_embedding instead."
        ),
    )
