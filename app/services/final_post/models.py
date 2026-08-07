"""Provider-independent models for final post generation (Module 9)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_POST_TEXT_LENGTH = 1200
MAX_RATIONALE_LENGTH = 2500


def is_valid_http_url(url: str) -> bool:
    try:
        parts = urlsplit(url.strip())
    except Exception:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


class GeneratedPost(BaseModel):
    """Validated final post object. Not persisted to the posts table in Module 9."""

    text: str = Field(..., min_length=1, max_length=MAX_POST_TEXT_LENGTH)
    rationale: str = Field(..., min_length=1, max_length=MAX_RATIONALE_LENGTH)
    source_urls: list[str] = Field(default_factory=list)

    @field_validator("text", "rationale", mode="before")
    @classmethod
    def strip_required(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("source_urls", mode="before")
    @classmethod
    def normalize_urls(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("source_urls must be a list")
        urls: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            if not is_valid_http_url(text):
                raise ValueError(f"invalid source URL: {text}")
            urls.append(text)
        return list(dict.fromkeys(urls))

    @model_validator(mode="after")
    def require_non_empty_fields(self) -> GeneratedPost:
        if not self.text.strip():
            raise ValueError("text cannot be empty")
        if not self.rationale.strip():
            raise ValueError("rationale cannot be empty")
        return self


class FinalPostContext(BaseModel):
    """Bounded context passed into final generation."""

    persona_name: str
    persona_domain: str
    topic: dict[str, Any]
    debate: dict[str, Any]
    consistency: dict[str, Any]
    memories: list[dict[str, Any]] = Field(default_factory=list)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    research: list[dict[str, Any]] = Field(default_factory=list)
    grounded_urls: list[str] = Field(default_factory=list)
    previous_titles: list[str] = Field(default_factory=list)
    intelligence: dict[str, Any] = Field(default_factory=dict)


class FinalGenerationOutcome(BaseModel):
    status: str
    generated_post: GeneratedPost | None = None
    validation_errors: list[str] = Field(default_factory=list)
    repaired: bool = False
    error: str | None = None
    llm_calls: int = 0
