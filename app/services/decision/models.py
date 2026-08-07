"""Provider-independent decision orchestration models (Module 16)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

DecisionAction = Literal["proceed", "skip", "stop", "retry"]
GateName = Literal[
    "research",
    "candidate",
    "selection",
    "debate",
    "memory",
    "consistency",
    "intelligence",
    "adaptive",
    "learning",
    "generation",
    "publishing",
]


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class DecisionResult(BaseModel):
    action: DecisionAction
    gate: GateName
    reason: str
    confidence: float = 0.5
    evidence: dict[str, Any] = Field(default_factory=dict)
    blocking: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, value: object) -> float:
        try:
            return clamp_unit(float(value))  # type: ignore[arg-type]
        except Exception:
            return 0.0

    def to_meta(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "gate": self.gate,
            "reason": self.reason[:240],
            "confidence": self.confidence,
            "evidence": dict(list(self.evidence.items())[:12]),
            "blocking": self.blocking,
        }


class DecisionContext(BaseModel):
    """Compact, agent-scoped orchestration context. No secrets."""

    agent_id: str
    cycle_id: str | None = None
    research_count: int = 0
    grounded_research_count: int = 0
    candidate_count: int = 0
    selected_count: int = 0
    rejected_count: int = 0
    debated_count: int = 0
    debate_proceed_count: int = 0
    debate_reject_count: int = 0
    memory_context_available: bool = False
    consistency_approve_count: int = 0
    consistency_reject_count: int = 0
    consistency_max_score: float = 0.0
    contradiction_detected: bool = False
    source_diversity_score: float | None = None
    adaptive_guidance: dict[str, Any] = Field(default_factory=dict)
    learning_guidance: dict[str, Any] = Field(default_factory=dict)
    final_generation_status: str | None = None
    final_validation_passed: bool = False
    publication_status: str | None = None
    has_generated_post: bool = False
    has_grounded_sources: bool = False
    has_rationale: bool = False
    is_duplicate: bool = False
    previous_cycle_summary: dict[str, Any] = Field(default_factory=dict)

    def to_meta(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "cycle_id": self.cycle_id,
            "research_count": self.research_count,
            "grounded_research_count": self.grounded_research_count,
            "candidate_count": self.candidate_count,
            "selected_count": self.selected_count,
            "rejected_count": self.rejected_count,
            "debated_count": self.debated_count,
            "consistency_approve_count": self.consistency_approve_count,
            "consistency_reject_count": self.consistency_reject_count,
            "contradiction_detected": self.contradiction_detected,
            "final_generation_status": self.final_generation_status,
            "publication_status": self.publication_status,
        }


class DecisionTrace(BaseModel):
    """Bounded machine-readable decision history for one cycle."""

    cycle_id: str | None = None
    agent_id: str
    decisions: list[DecisionResult] = Field(default_factory=list)
    final_action: DecisionAction = "proceed"
    status: str = "success"
    reason: str | None = None

    def add(self, result: DecisionResult, *, max_items: int = 12) -> None:
        self.decisions.append(result)
        if len(self.decisions) > max_items:
            self.decisions = self.decisions[-max_items:]
        self.final_action = result.action

    def summary(self) -> dict[str, Any]:
        passed = sum(1 for d in self.decisions if d.action == "proceed")
        skipped = sum(1 for d in self.decisions if d.action == "skip")
        blocked = sum(
            1 for d in self.decisions if d.action == "stop" or d.blocking
        )
        retried = sum(1 for d in self.decisions if d.action == "retry")
        return {
            "final_action": self.final_action,
            "gates_passed": passed,
            "gates_skipped": skipped,
            "gates_blocked": blocked,
            "gates_retried": retried,
            "status": self.status,
            "reason": self.reason,
            "trace": [d.to_meta() for d in self.decisions],
        }
