"""Deterministic decision policy thresholds (Module 16).

Cannot disable grounding, consistency safety, publishing integrity, or isolation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DecisionPolicy:
    enabled: bool = True
    min_research_items: int = 1
    min_grounded_items: int = 1
    min_candidates: int = 1
    min_selected: int = 1
    min_consistency_score: float = 0.70
    allow_uncertain_debate: bool = False
    max_trace_items: int = 12

    def __post_init__(self) -> None:
        if self.min_research_items < 1:
            raise ValueError("DECISION_MIN_RESEARCH_ITEMS must be >= 1")
        if self.min_grounded_items < 1:
            raise ValueError("DECISION_MIN_GROUNDED_ITEMS must be >= 1")
        if self.min_candidates < 1:
            raise ValueError("DECISION_MIN_CANDIDATES must be >= 1")
        if self.min_selected < 1:
            raise ValueError("DECISION_MIN_SELECTED must be >= 1")
        if not 0.0 <= self.min_consistency_score <= 1.0:
            raise ValueError("DECISION_MIN_CONSISTENCY_SCORE must be in [0, 1]")
        if self.max_trace_items < 1:
            raise ValueError("DECISION_MAX_TRACE_ITEMS must be >= 1")
