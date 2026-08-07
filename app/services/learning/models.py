"""Controlled learning domain models (Module 15).

Learning may influence bounded strategy/guidance only.
It must never rewrite persona, weights, prompts, or safety gates.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ALLOWED_SIGNAL_TYPES = frozenset(
    {
        "topic_preference",
        "source_preference",
        "angle_preference",
        "evidence_preference",
        "novelty_preference",
        "reasoning_preference",
        "rationale_preference",
        "story_preference",
        "strategy",
    }
)

LearningState = Literal["inactive", "emerging", "active", "weakening", "expired"]
SignalType = Literal[
    "topic_preference",
    "source_preference",
    "angle_preference",
    "evidence_preference",
    "novelty_preference",
    "reasoning_preference",
    "rationale_preference",
    "story_preference",
    "strategy",
]


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class LearningSignal(BaseModel):
    """Bounded, explainable learning signal. Extensible only within allowed types."""

    signal_type: SignalType
    key: str
    state: LearningState = "inactive"
    confidence: float = 0.0
    support_count: int = 0
    failure_count: int = 0
    evidence_count: int = 0
    last_observed_at: datetime | None = None
    created_at: datetime | None = None
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    value: dict[str, Any] = Field(default_factory=dict)
    influence: float = 0.0

    @field_validator("signal_type", mode="before")
    @classmethod
    def _type(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if text not in ALLOWED_SIGNAL_TYPES:
            raise ValueError(f"unsupported learning signal_type: {value}")
        return text

    @field_validator("confidence", "influence", mode="before")
    @classmethod
    def _unit(cls, value: object) -> float:
        try:
            return clamp_unit(float(value))  # type: ignore[arg-type]
        except Exception:
            return 0.0

    @field_validator("support_count", "failure_count", "evidence_count", mode="before")
    @classmethod
    def _nonneg(cls, value: object) -> int:
        try:
            return max(0, int(value))  # type: ignore[arg-type]
        except Exception:
            return 0

    def to_meta(self) -> dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "key": self.key,
            "state": self.state,
            "confidence": self.confidence,
            "influence": self.influence,
            "support_count": self.support_count,
            "failure_count": self.failure_count,
            "evidence_count": self.evidence_count,
            "reason": self.reason[:240],
            "value": self.value,
            "evidence_refs": self.evidence_refs[:5],
            "last_observed_at": (
                self.last_observed_at.isoformat() if self.last_observed_at else None
            ),
        }


class LearningContext(BaseModel):
    """Bounded guidance for downstream modules. Never authoritative over safety."""

    active_strategies: list[dict[str, Any]] = Field(default_factory=list)
    topic_preferences: list[dict[str, Any]] = Field(default_factory=list)
    source_preferences: list[dict[str, Any]] = Field(default_factory=list)
    angle_preferences: list[dict[str, Any]] = Field(default_factory=list)
    novelty_preferences: list[dict[str, Any]] = Field(default_factory=list)
    evidence_preferences: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    relevant_signals: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "success"
    reason: str | None = None

    def to_meta(self) -> dict[str, Any]:
        return self.model_dump()
