"""Contradiction detection engine (Module 12)."""

from app.services.contradictions.detector import ContradictionDetector, challenge_payload
from app.services.contradictions.models import (
    ContradictionAnalysisResult,
    ContradictionSignal,
)

__all__ = [
    "ContradictionDetector",
    "ContradictionAnalysisResult",
    "ContradictionSignal",
    "challenge_payload",
]
