"""Analyze historical evidence into learning observations (deterministic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.adaptive_learning.models import AdaptiveSignal
from app.services.evaluation.models import PostEvaluation


@dataclass
class Observation:
    signal_type: str
    key: str
    success: bool
    confidence_hint: float
    reason: str
    evidence_ref: str
    recommendation: str
    value: dict[str, Any] = field(default_factory=dict)


class LearningAnalyzer:
    """Derive learning observations from evaluations + adaptive patterns."""

    def __init__(self, *, lookback: int = 20) -> None:
        self._lookback = max(1, lookback)

    def analyze(
        self,
        *,
        evaluations: list[PostEvaluation],
        adaptive_signals: list[AdaptiveSignal] | None = None,
        quality_feedback: dict[str, str] | None = None,
        adaptive_meta: dict[str, Any] | None = None,
    ) -> list[Observation]:
        observations: list[Observation] = []
        recent_evals = list(evaluations)[: self._lookback]

        for evaluation in recent_evals:
            success = evaluation.overall_score >= 0.70 and evaluation.quality_class in {
                "excellent",
                "good",
            }
            weak = evaluation.overall_score < 0.55 or evaluation.quality_class in {
                "weak",
                "failed",
            }
            ref = f"eval:{evaluation.post_id}"

            # Evidence-first strategy from evidence_quality outcomes.
            if evaluation.evidence_quality >= 0.75 and success:
                observations.append(
                    Observation(
                        signal_type="strategy",
                        key="evidence_first",
                        success=True,
                        confidence_hint=evaluation.evidence_quality,
                        reason=(
                            "Posts with stronger evidence scores consistently received "
                            "higher evaluations."
                        ),
                        evidence_ref=ref,
                        recommendation=(
                            "prefer topics with strong multi-source evidence"
                        ),
                        value={"dimension": "evidence_quality"},
                    )
                )
            elif evaluation.evidence_quality < 0.55 or weak:
                observations.append(
                    Observation(
                        signal_type="evidence_preference",
                        key="strengthen_evidence_discipline",
                        success=False,
                        confidence_hint=1.0 - evaluation.evidence_quality,
                        reason="Low evidence quality correlated with weaker evaluations.",
                        evidence_ref=ref,
                        recommendation="require stronger grounded evidence before publishing",
                        value={"dimension": "evidence_quality"},
                    )
                )

            # Novelty preference.
            if evaluation.novelty_score < 0.55:
                observations.append(
                    Observation(
                        signal_type="novelty_preference",
                        key="increase_novelty_after_repetition",
                        success=False,
                        confidence_hint=1.0 - evaluation.novelty_score,
                        reason="Low novelty scores indicate repetitive coverage.",
                        evidence_ref=ref,
                        recommendation=(
                            "prefer less repetitive topics when recent coverage is saturated"
                        ),
                        value={"dimension": "novelty_score"},
                    )
                )
            elif evaluation.novelty_score >= 0.75 and success:
                observations.append(
                    Observation(
                        signal_type="novelty_preference",
                        key="reward_novel_angles",
                        success=True,
                        confidence_hint=evaluation.novelty_score,
                        reason="Higher novelty aligned with stronger evaluation outcomes.",
                        evidence_ref=ref,
                        recommendation="continue preferring novel AI Security angles",
                        value={"dimension": "novelty_score"},
                    )
                )

            # Source diversity.
            if evaluation.source_diversity_score < 0.55:
                observations.append(
                    Observation(
                        signal_type="source_preference",
                        key="diversify_source_domains",
                        success=False,
                        confidence_hint=1.0 - evaluation.source_diversity_score,
                        reason="Low source diversity correlated with weaker evaluations.",
                        evidence_ref=ref,
                        recommendation="prefer topics backed by diverse source domains",
                        value={"dimension": "source_diversity_score"},
                    )
                )
            elif evaluation.source_diversity_score >= 0.7 and success:
                observations.append(
                    Observation(
                        signal_type="source_preference",
                        key="prefer_diverse_sources",
                        success=True,
                        confidence_hint=evaluation.source_diversity_score,
                        reason="Diverse sources aligned with stronger evaluations.",
                        evidence_ref=ref,
                        recommendation="maintain multi-domain source coverage",
                        value={"dimension": "source_diversity_score"},
                    )
                )

            # Reasoning / rationale.
            if evaluation.reasoning_quality >= 0.75 and success:
                observations.append(
                    Observation(
                        signal_type="reasoning_preference",
                        key="transparent_reasoning",
                        success=True,
                        confidence_hint=evaluation.reasoning_quality,
                        reason="Strong reasoning quality accompanied successful posts.",
                        evidence_ref=ref,
                        recommendation="keep selection rationale explicit and evidence-linked",
                        value={"dimension": "reasoning_quality"},
                    )
                )
            if evaluation.rationale_quality < 0.55:
                observations.append(
                    Observation(
                        signal_type="rationale_preference",
                        key="improve_rationale_clarity",
                        success=False,
                        confidence_hint=1.0 - evaluation.rationale_quality,
                        reason="Weak rationale quality appeared in lower-scoring posts.",
                        evidence_ref=ref,
                        recommendation="explain selection tradeoffs more clearly",
                        value={"dimension": "rationale_quality"},
                    )
                )

            # Feedback actions as soft observations.
            for key, action in (evaluation.feedback or {}).items():
                if action == "improve":
                    observations.append(
                        Observation(
                            signal_type=self._feedback_signal_type(key),
                            key=f"improve_{key}"[:80],
                            success=False,
                            confidence_hint=0.6,
                            reason=f"Quality feedback requested improvement for {key}.",
                            evidence_ref=ref,
                            recommendation=f"improve {key.replace('_', ' ')} in future cycles",
                            value={"feedback_key": key, "action": action},
                        )
                    )
                elif action == "maintain" and success:
                    observations.append(
                        Observation(
                            signal_type=self._feedback_signal_type(key),
                            key=f"maintain_{key}"[:80],
                            success=True,
                            confidence_hint=0.7,
                            reason=f"Quality feedback supports maintaining {key}.",
                            evidence_ref=ref,
                            recommendation=f"maintain current {key.replace('_', ' ')} practice",
                            value={"feedback_key": key, "action": action},
                        )
                    )

        # Adaptive overuse / dependency patterns.
        meta = adaptive_meta or {}
        for topic in list(meta.get("overused_topics") or [])[:5]:
            observations.append(
                Observation(
                    signal_type="topic_preference",
                    key=f"reduce_overuse_{str(topic)[:40]}",
                    success=False,
                    confidence_hint=0.65,
                    reason=f"Topic pattern '{topic}' appears overused in adaptive coverage.",
                    evidence_ref=f"adaptive:topic:{topic}",
                    recommendation=f"reduce repetition of topic concept {topic}",
                    value={"topic": topic},
                )
            )
        for dep in list(meta.get("source_dependencies") or [])[:5]:
            domain = str(dep.get("domain") or "")
            if not domain:
                continue
            if str(dep.get("dependency")) == "high":
                observations.append(
                    Observation(
                        signal_type="source_preference",
                        key=f"reduce_dependency_{domain[:40]}",
                        success=False,
                        confidence_hint=0.7,
                        reason=f"High dependency on {domain} detected by adaptive learning.",
                        evidence_ref=f"adaptive:source:{domain}",
                        recommendation=f"diversify away from over-reliance on {domain}",
                        value={"domain": domain},
                    )
                )
        for story in list(meta.get("stale_stories") or [])[:5]:
            observations.append(
                Observation(
                    signal_type="story_preference",
                    key=f"avoid_stale_{str(story)[:40]}",
                    success=False,
                    confidence_hint=0.6,
                    reason=f"Story '{story}' is stale without meaningful new evidence.",
                    evidence_ref=f"adaptive:story:{story}",
                    recommendation="prefer revived or fresh stories over stale continuations",
                    value={"story": story},
                )
            )

        # Adaptive signal rejection patterns.
        for signal in list(adaptive_signals or [])[:20]:
            if signal.signal_type == "rejection_pattern" and signal.evidence_count >= 2:
                observations.append(
                    Observation(
                        signal_type="strategy",
                        key=f"avoid_rejection_{signal.key}"[:80],
                        success=False,
                        confidence_hint=min(1.0, 0.4 + 0.1 * signal.evidence_count),
                        reason=f"Repeated rejection pattern: {signal.key}.",
                        evidence_ref=f"adaptive:rejection:{signal.key}",
                        recommendation=f"avoid patterns that trigger {signal.key}",
                        value={"rejection": signal.key},
                    )
                )

        # Latest quality feedback map.
        for key, action in (quality_feedback or {}).items():
            if action == "improve":
                observations.append(
                    Observation(
                        signal_type=self._feedback_signal_type(key),
                        key=f"qf_improve_{key}"[:80],
                        success=False,
                        confidence_hint=0.65,
                        reason=f"Aggregated quality feedback: improve {key}.",
                        evidence_ref=f"quality_feedback:{key}",
                        recommendation=f"improve {key.replace('_', ' ')}",
                        value={"feedback_key": key, "action": action},
                    )
                )

        return self._dedupe(observations)

    @staticmethod
    def _feedback_signal_type(key: str) -> str:
        mapping = {
            "novelty": "novelty_preference",
            "evidence_discipline": "evidence_preference",
            "source_diversity": "source_preference",
            "reasoning_quality": "reasoning_preference",
            "rationale_quality": "rationale_preference",
            "relevance": "topic_preference",
            "consistency": "strategy",
            "overall_quality": "strategy",
        }
        return mapping.get(str(key), "strategy")

    @staticmethod
    def _dedupe(observations: list[Observation]) -> list[Observation]:
        """Keep chronological order but collapse identical type/key pairs later in strategy."""
        return observations
