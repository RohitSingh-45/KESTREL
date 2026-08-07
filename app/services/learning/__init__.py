"""Module 15 — controlled autonomous learning (bounded strategy guidance)."""

from app.services.learning.models import (
    ALLOWED_SIGNAL_TYPES,
    LearningContext,
    LearningSignal,
)
from app.services.learning.policy import validate_learning_signal
from app.services.learning.service import ControlledLearningService
from app.services.learning.store import LearningStore
from app.services.learning.strategy import classify_state, compute_confidence

__all__ = [
    "ALLOWED_SIGNAL_TYPES",
    "LearningContext",
    "LearningSignal",
    "ControlledLearningService",
    "LearningStore",
    "validate_learning_signal",
    "compute_confidence",
    "classify_state",
]
