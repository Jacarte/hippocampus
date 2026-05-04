from __future__ import annotations

from typing import Any

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
