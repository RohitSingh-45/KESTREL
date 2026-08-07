"""Adaptive learning domain models (Module 13)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class AdaptiveSignal(BaseModel):
    """Provider-independent learned signal. Extensible via signal_type."""

    signal_type: str
    key: str
    value: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.5
    evidence_count: int = 0
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    source_urls: list[str] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    influence: float = 0.0  # decay-weighted confidence at evaluation time

    @field_validator("confidence", "influence", mode="before")
    @classmethod
    def _unit(cls, value: object) -> float:
        try:
            return clamp_unit(float(value))  # type: ignore[arg-type]
        except Exception:
            return 0.0

    def to_meta(self) -> dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "key": self.key,
            "value": self.value,
            "confidence": self.confidence,
            "influence": self.influence,
            "evidence_count": self.evidence_count,
            "source_urls": self.source_urls[:5],
            "last_observed_at": (
                self.last_observed_at.isoformat() if self.last_observed_at else None
            ),
        }


class TopicGuidance(BaseModel):
    """Deterministic guidance for one candidate. Does not replace Module 5 scores."""

    coverage: str = "unknown"  # low|moderate|high
    novelty_need: str = "moderate"
    story_status: str = "unknown"  # active|aging|stale|revived|new|unknown
    source_diversity_need: str = "moderate"
    notes: list[str] = Field(default_factory=list)

    def to_meta(self) -> dict[str, Any]:
        return self.model_dump()


class AdaptiveContext(BaseModel):
    """Bounded adaptive context for downstream modules."""

    recent_topic_patterns: list[dict[str, Any]] = Field(default_factory=list)
    overused_topics: list[str] = Field(default_factory=list)
    undercovered_topics: list[str] = Field(default_factory=list)
    recent_angles: list[dict[str, Any]] = Field(default_factory=list)
    overused_angles: list[str] = Field(default_factory=list)
    source_dependencies: list[dict[str, Any]] = Field(default_factory=list)
    active_stories: list[str] = Field(default_factory=list)
    stale_stories: list[str] = Field(default_factory=list)
    revived_stories: list[str] = Field(default_factory=list)
    rejection_patterns: list[dict[str, Any]] = Field(default_factory=list)
    relevant_adaptive_signals: list[dict[str, Any]] = Field(default_factory=list)
    candidate_guidance: dict[str, Any] = Field(default_factory=dict)
    quality_feedback: dict[str, str] = Field(default_factory=dict)
    status: str = "success"
    reason: str | None = None

    def to_meta(self) -> dict[str, Any]:
        return self.model_dump()
