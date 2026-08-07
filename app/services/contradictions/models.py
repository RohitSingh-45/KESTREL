"""Contradiction detection models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ContradictionType = Literal["direct", "partial", "contextual", "uncertain"]


class ContradictionSignal(BaseModel):
    memory_id: str
    memory_key: str
    evidence_urls: list[str] = Field(default_factory=list)
    contradiction_type: ContradictionType = "uncertain"
    explanation: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)

    def to_meta(self) -> dict:
        return {
            "detected": True,
            "type": self.contradiction_type,
            "confidence": self.confidence,
            "memory_key": self.memory_key,
            "memory_id": self.memory_id,
            "evidence_urls": self.evidence_urls,
            "explanation": self.explanation,
        }


class ContradictionAnalysisResult(BaseModel):
    signals: list[ContradictionSignal] = Field(default_factory=list)
    llm_used: bool = False
    status: str = "success"
    reason: str | None = None

    def to_meta(self) -> dict:
        if not self.signals:
            return {"detected": False, "status": self.status, "reason": self.reason}
        top = max(self.signals, key=lambda s: s.confidence)
        return {
            **top.to_meta(),
            "status": self.status,
            "signal_count": len(self.signals),
            "llm_used": self.llm_used,
        }
