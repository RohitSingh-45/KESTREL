"""Domain models for publishing."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PublishedPost(BaseModel):
    id: str
    agent_id: str
    created_at: datetime
    text: str
    rationale: str
    sources: list[str] = Field(default_factory=list)
    topic: str | None = None
    content_hash: str


PublicationStatus = Literal["published", "duplicate", "rejected", "failed"]


class PublicationResult(BaseModel):
    status: PublicationStatus
    post: PublishedPost | None = None
    content_hash: str | None = None
    reason: str | None = None
    post_id: str | None = None
