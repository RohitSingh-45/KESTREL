"""Adaptive learning layer (Module 13) — deterministic sidecar."""

from app.services.adaptive_learning.coverage import (
    TopicCoverageAnalyzer,
    classify_angle,
    normalize_concept,
)
from app.services.adaptive_learning.decay import decay_influence
from app.services.adaptive_learning.models import (
    AdaptiveContext,
    AdaptiveSignal,
    TopicGuidance,
)
from app.services.adaptive_learning.service import AdaptiveLearningService
from app.services.adaptive_learning.store import AdaptiveSignalStore

__all__ = [
    "AdaptiveLearningService",
    "AdaptiveSignalStore",
    "AdaptiveContext",
    "AdaptiveSignal",
    "TopicGuidance",
    "TopicCoverageAnalyzer",
    "classify_angle",
    "normalize_concept",
    "decay_influence",
]
