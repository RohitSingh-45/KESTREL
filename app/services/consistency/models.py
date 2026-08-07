"""Domain models for persona consistency and anti-drift analysis."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ConsistencyDecision = Literal["approve", "revise", "reject"]


class ConsistencyDecisionEnum(str, Enum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class PersonaStyle(BaseModel):
    technical_depth: str = "moderate_to_high"
    avoid_hype: bool = True
    avoid_unverified_claims: bool = True
    prefer_specific_evidence: bool = True


class PersonaProfile(BaseModel):
    version: int = 1
    name: str = "Kestrel"
    domain: str = "AI Security"
    mission: str = "Identify and explain meaningful developments in AI Security."
    tone: list[str] = Field(
        default_factory=lambda: ["precise", "analytical", "calm", "evidence-driven"]
    )
    style: PersonaStyle = Field(default_factory=PersonaStyle)
    domain_keywords: list[str] = Field(default_factory=list)


class ConsistencySignals(BaseModel):
    identity_fit: float = 0.0
    domain_fit: float = 0.0
    tone_fit: float = 0.0
    stance_fit: float = 0.0
    technical_depth_fit: float = 0.0
    evidence_discipline: float = 0.0
    historical_style_fit: float = 0.0

    @field_validator(
        "identity_fit",
        "domain_fit",
        "tone_fit",
        "stance_fit",
        "technical_depth_fit",
        "evidence_discipline",
        "historical_style_fit",
        mode="before",
    )
    @classmethod
    def _unit(cls, value: object) -> float:
        try:
            return clamp_unit(float(value))  # type: ignore[arg-type]
        except Exception:
            return 0.0


class HistoricalStyleProfile(BaseModel):
    """Lightweight deterministic style baseline from prior topic/debate metadata."""

    sample_count: int = 0
    avg_title_length: float = 0.0
    avg_text_length: float = 0.0
    technical_keyword_density: float = 0.0
    hype_phrase_rate: float = 0.0
    domain_keyword_density: float = 0.0
    source_usage_rate: float = 0.0


class ConsistencyResult(BaseModel):
    topic_id: str
    decision: ConsistencyDecisionEnum
    overall_score: float
    signals: ConsistencySignals
    reasons: list[str] = Field(default_factory=list)
    hard_reject: bool = False
    drift_flags: list[str] = Field(default_factory=list)
    belief_change_accepted: bool = False
    revised: bool = False
    revision_count: int = 0
    error: str | None = None
    profile_version: int = 1

    @field_validator("overall_score", mode="before")
    @classmethod
    def _score(cls, value: object) -> float:
        try:
            return clamp_unit(float(value))  # type: ignore[arg-type]
        except Exception:
            return 0.0


def consistency_payload(result: ConsistencyResult) -> dict[str, Any]:
    return {
        "version": 1,
        "profile_version": result.profile_version,
        "decision": result.decision.value,
        "overall_score": result.overall_score,
        "signals": result.signals.model_dump(),
        "reasons": result.reasons,
        "hard_reject": result.hard_reject,
        "drift_flags": result.drift_flags,
        "belief_change_accepted": result.belief_change_accepted,
        "revised": result.revised,
        "revision_count": result.revision_count,
        "error": result.error,
    }
