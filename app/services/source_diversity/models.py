"""Source diversity domain models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SourceDiversityResult(BaseModel):
    """Per-candidate diversity analysis. Separate from source quality."""

    source_diversity: float = Field(ge=0.0, le=1.0)
    source_repetition: float = Field(ge=0.0, le=1.0)
    source_domain_count: int = Field(ge=0)
    recent_source_overlap: float = Field(ge=0.0, le=1.0)
    domains: list[str] = Field(default_factory=list)
    repeated_domains: list[str] = Field(default_factory=list)

    def to_meta(self) -> dict:
        return self.model_dump()
