"""Convert evaluations into bounded quality feedback for future cycles."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.logging import get_logger
from app.services.adaptive_learning.decay import decay_influence
from app.services.evaluation.models import (
    FeedbackAction,
    PostEvaluation,
    QualityFeedback,
    clamp_unit,
)

logger = get_logger(__name__)

_DIMENSION_KEYS = (
    ("evidence_quality", "evidence_discipline"),
    ("novelty_score", "novelty"),
    ("source_diversity_score", "source_diversity"),
    ("reasoning_quality", "reasoning_quality"),
    ("relevance_score", "relevance"),
    ("rationale_quality", "rationale_quality"),
    ("consistency_score", "consistency"),
)


def build_quality_feedback(
    evaluation: PostEvaluation,
    *,
    improve_below: float = 0.65,
    maintain_above: float = 0.80,
    now: datetime | None = None,
) -> QualityFeedback:
    """Map dimension scores to improve/maintain/watch guidance."""
    actions: dict[str, FeedbackAction] = {}
    scores = {
        "evidence_quality": evaluation.evidence_quality,
        "novelty_score": evaluation.novelty_score,
        "source_diversity_score": evaluation.source_diversity_score,
        "reasoning_quality": evaluation.reasoning_quality,
        "relevance_score": evaluation.relevance_score,
        "rationale_quality": evaluation.rationale_quality,
        "consistency_score": evaluation.consistency_score,
    }
    for dim, feedback_key in _DIMENSION_KEYS:
        value = float(scores.get(dim, 0.0))
        if value < improve_below:
            actions[feedback_key] = "improve"
        elif value >= maintain_above:
            actions[feedback_key] = "maintain"
        else:
            actions[feedback_key] = "watch"

    # Overall quality_class can reinforce novelty/evidence focus.
    if evaluation.quality_class in {"weak", "failed"}:
        actions.setdefault("overall_quality", "improve")
    elif evaluation.quality_class == "excellent":
        actions.setdefault("overall_quality", "maintain")

    confidence = clamp_unit(0.4 + 0.5 * evaluation.overall_score)
    feedback = QualityFeedback(
        actions=actions,
        confidence=confidence,
        evidence_count=1,
        last_observed_at=now or evaluation.evaluated_at,
        influence=confidence,
    )
    logger.info(
        "evaluation_feedback_created",
        agent_id=evaluation.agent_id,
        cycle_id=evaluation.cycle_id,
        post_id=evaluation.post_id,
        overall_score=evaluation.overall_score,
        quality_class=evaluation.quality_class,
        stage="evaluation",
        item_count=len(actions),
    )
    return feedback


def merge_feedback_history(
    recent: list[QualityFeedback],
    *,
    now: datetime,
    decay_days: int = 30,
    max_items: int = 10,
) -> dict[str, str]:
    """Aggregate recent feedback with decay-weighted votes."""
    votes: dict[str, dict[str, float]] = {}
    for item in recent[:max_items]:
        influence = decay_influence(
            item.confidence,
            last_observed_at=item.last_observed_at,
            now=now,
            decay_days=decay_days,
        )
        item.influence = influence
        if influence <= 0:
            continue
        for key, action in item.actions.items():
            bucket = votes.setdefault(key, {})
            bucket[action] = bucket.get(action, 0.0) + influence

    merged: dict[str, str] = {}
    for key, action_scores in votes.items():
        best = max(action_scores.items(), key=lambda kv: kv[1])
        merged[key] = best[0]
    return merged


def feedback_to_adaptive_value(feedback: QualityFeedback) -> dict[str, Any]:
    return {
        "actions": dict(feedback.actions),
        "confidence": feedback.confidence,
        "evidence_count": feedback.evidence_count,
    }
