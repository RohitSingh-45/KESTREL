"""Bounded observability / explainability models (Module 17)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

EventStatus = Literal["ok", "skipped", "blocked", "failed", "info"]
EventAction = Literal[
    "proceed",
    "skip",
    "stop",
    "reject",
    "select",
    "publish",
    "observe",
    "complete",
]

EventType = Literal[
    "research_started",
    "research_completed",
    "candidate_generated",
    "candidate_selected",
    "candidate_rejected",
    "debate_completed",
    "memory_retrieved",
    "memory_updated",
    "contradiction_detected",
    "consistency_checked",
    "consistency_rejected",
    "intelligence_analyzed",
    "adaptive_guidance_applied",
    "learning_guidance_applied",
    "final_generation_started",
    "final_generation_completed",
    "final_validation_failed",
    "decision_gate_passed",
    "decision_gate_blocked",
    "publishing_started",
    "publishing_completed",
    "publishing_skipped",
    "evaluation_completed",
    "cycle_completed",
    "cycle_skipped",
    "cycle_failed",
]


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class ObservabilityEvent(BaseModel):
    """Compact, agent-scoped observability event. No secrets / prompts / raw LLM."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str
    cycle_id: str | None = None
    order: int = 0
    timestamp: str | None = None
    stage: str
    event_type: EventType
    action: EventAction = "observe"
    status: EventStatus = "info"
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    source_urls: list[str] = Field(default_factory=list)
    related_topic_id: str | None = None
    related_post_id: str | None = None
    decision_gate: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    priority: int = 50

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, value: object) -> str:
        text = "" if value is None else str(value)
        return text[:300]

    @field_validator("source_urls", mode="before")
    @classmethod
    def _urls(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value[:10]:
            text = str(item).strip()
            if text:
                out.append(text[:240])
        return out

    @field_validator("evidence", "metadata", mode="before")
    @classmethod
    def _bounded_map(cls, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        items = list(value.items())[:10]
        bounded: dict[str, Any] = {}
        for key, raw in items:
            k = str(key)[:64]
            if isinstance(raw, str):
                bounded[k] = raw[:300]
            elif isinstance(raw, (int, float, bool)) or raw is None:
                bounded[k] = raw
            elif isinstance(raw, list):
                bounded[k] = [
                    item[:120] if isinstance(item, str) else item for item in raw[:10]
                ]
            else:
                bounded[k] = str(raw)[:120]
        return bounded

    def to_meta(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "agent_id": self.agent_id,
            "cycle_id": self.cycle_id,
            "order": self.order,
            "timestamp": self.timestamp,
            "stage": self.stage,
            "event_type": self.event_type,
            "action": self.action,
            "status": self.status,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "source_urls": list(self.source_urls),
            "related_topic_id": self.related_topic_id,
            "related_post_id": self.related_post_id,
            "decision_gate": self.decision_gate,
            "metadata": dict(self.metadata),
        }


class PublicationExplanation(BaseModel):
    published: bool
    final_reason: str
    stage: str
    explanation: str
    evidence: dict[str, Any] = Field(default_factory=dict)

    def to_meta(self) -> dict[str, Any]:
        return {
            "published": self.published,
            "final_reason": self.final_reason[:120],
            "stage": self.stage[:64],
            "explanation": self.explanation[:300],
            "evidence": dict(list(self.evidence.items())[:10]),
        }


class CycleExplanation(BaseModel):
    cycle_id: str
    agent_id: str
    result: str
    stages: list[dict[str, Any]] = Field(default_factory=list)
    decision_summary: dict[str, Any] = Field(default_factory=dict)
    publication: dict[str, Any] = Field(default_factory=dict)
    evaluation: dict[str, Any] = Field(default_factory=dict)
    why: dict[str, Any] = Field(default_factory=dict)

    def to_meta(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "agent_id": self.agent_id,
            "result": self.result,
            "stages": self.stages[:20],
            "decision_summary": dict(list(self.decision_summary.items())[:20]),
            "publication": dict(list(self.publication.items())[:20]),
            "evaluation": dict(list(self.evaluation.items())[:20]),
            "why": dict(list(self.why.items())[:20]),
        }
