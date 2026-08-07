"""Provider-independent post evaluation models (Module 14)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

EVALUATOR_VERSION = "1.0.0"

QualityClass = Literal["excellent", "good", "acceptable", "weak", "failed"]
FeedbackAction = Literal["improve", "maintain", "watch"]


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class EvaluationLLMResponse(BaseModel):
    """Optional LLM reflection payload. Never a new post."""

    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    improvement_actions: list[str] = Field(default_factory=list)
    confidence: float = 0.5

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, value: object) -> float:
        try:
            return clamp_unit(float(value))  # type: ignore[arg-type]
        except Exception:
            return 0.0


class DimensionScores(BaseModel):
    evidence_quality: float = 0.0
    relevance_score: float = 0.0
    novelty_score: float = 0.0
    reasoning_quality: float = 0.0
    source_diversity_score: float = 0.0
    consistency_score: float = 0.0
    rationale_quality: float = 0.0

    @field_validator(
        "evidence_quality",
        "relevance_score",
        "novelty_score",
        "reasoning_quality",
        "source_diversity_score",
        "consistency_score",
        "rationale_quality",
        mode="before",
    )
    @classmethod
    def _unit(cls, value: object) -> float:
        try:
            return clamp_unit(float(value))  # type: ignore[arg-type]
        except Exception:
            return 0.0

    def to_dict(self) -> dict[str, float]:
        return self.model_dump()


class QualityFeedback(BaseModel):
    """Bounded guidance for future cycles. Never mutates Module 5 weights."""

    actions: dict[str, FeedbackAction] = Field(default_factory=dict)
    confidence: float = 0.5
    evidence_count: int = 1
    last_observed_at: datetime | None = None
    influence: float = 0.0

    @field_validator("confidence", "influence", mode="before")
    @classmethod
    def _unit(cls, value: object) -> float:
        try:
            return clamp_unit(float(value))  # type: ignore[arg-type]
        except Exception:
            return 0.0

    def to_meta(self) -> dict[str, Any]:
        return {
            "actions": dict(self.actions),
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "influence": self.influence,
            "last_observed_at": (
                self.last_observed_at.isoformat() if self.last_observed_at else None
            ),
        }


class PostEvaluation(BaseModel):
    """Deterministic-first evaluation of one published post."""

    post_id: str
    agent_id: str
    cycle_id: str | None = None
    evaluated_at: datetime | None = None
    overall_score: float = 0.0
    quality_class: QualityClass = "failed"
    evidence_quality: float = 0.0
    relevance_score: float = 0.0
    novelty_score: float = 0.0
    reasoning_quality: float = 0.0
    source_diversity_score: float = 0.0
    consistency_score: float = 0.0
    rationale_quality: float = 0.0
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    improvement_actions: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    evaluator_version: str = EVALUATOR_VERSION
    reflection_status: str = "skipped"  # success|failed|unavailable|skipped
    feedback: dict[str, FeedbackAction] = Field(default_factory=dict)
    status: str = "success"
    reason: str | None = None

    @field_validator(
        "overall_score",
        "evidence_quality",
        "relevance_score",
        "novelty_score",
        "reasoning_quality",
        "source_diversity_score",
        "consistency_score",
        "rationale_quality",
        mode="before",
    )
    @classmethod
    def _unit(cls, value: object) -> float:
        try:
            return clamp_unit(float(value))  # type: ignore[arg-type]
        except Exception:
            return 0.0

    def to_meta(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "post_id": self.post_id,
            "overall_score": self.overall_score,
            "quality_class": self.quality_class,
            "evidence_quality": self.evidence_quality,
            "relevance_score": self.relevance_score,
            "novelty_score": self.novelty_score,
            "reasoning_quality": self.reasoning_quality,
            "source_diversity_score": self.source_diversity_score,
            "consistency_score": self.consistency_score,
            "rationale_quality": self.rationale_quality,
            "strengths": self.strengths[:5],
            "weaknesses": self.weaknesses[:5],
            "improvement_actions": self.improvement_actions[:5],
            "source_urls": self.source_urls[:5],
            "reflection_status": self.reflection_status,
            "feedback": dict(self.feedback),
            "evaluator_version": self.evaluator_version,
            "llm_calls": 1 if self.reflection_status == "success" else 0,
        }
