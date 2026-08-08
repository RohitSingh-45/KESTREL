"""Provider-independent research domain models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ResearchItem(BaseModel):
    """Normalized research item produced by all source connectors."""

    title: str
    summary: str = ""
    url: str
    source_name: str
    published_at: datetime | None = None
    discovered_at: datetime
    author: str | None = None
    tags: list[str] = Field(default_factory=list)
    content_hash: str
    # Module 20 — set by AISecurityFilter from real lexical evidence only.
    ai_security_domain: str | None = None
    ai_security_relevance: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("title", "url", "source_name", "content_hash")
    @classmethod
    def strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        # Ensures scheme/host presence without binding callers to HttpUrl type.
        HttpUrl(value)
        return value
