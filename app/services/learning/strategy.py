"""Aggregate observations into bounded LearningSignals with state transitions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.orchestration.locks import utc_now
from app.services.adaptive_learning.decay import decay_influence
from app.services.learning.analyzer import Observation
from app.services.learning.models import LearningSignal, clamp_unit
from app.services.learning.policy import validate_learning_signal


def compute_confidence(
    *,
    support_count: int,
    failure_count: int,
    evidence_count: int,
    min_observations: int,
) -> float:
    """Conservative bounded confidence.

    Uses Laplace-smoothed success ratio and an observation factor so a single
    success cannot dominate. Confidence never grows unbounded.
    """
    support = max(0, support_count)
    failure = max(0, failure_count)
    evidence = max(0, evidence_count)
    ratio = support / float(support + failure + 1)
    denom = max(1, min_observations * 2)
    observation_factor = min(1.0, evidence / float(denom))
    # Cap growth: even perfect history saturates near 0.92
    return clamp_unit(ratio * (0.45 + 0.47 * observation_factor))


def classify_state(
    *,
    confidence: float,
    support_count: int,
    failure_count: int,
    evidence_count: int,
    influence: float,
    min_observations: int,
    min_confidence: float,
    previous_state: str | None = None,
) -> str:
    if influence <= 0.0 and evidence_count > 0 and previous_state in {
        "active",
        "weakening",
        "expired",
    }:
        return "expired"
    if evidence_count <= 0:
        return "inactive"
    if evidence_count < min_observations or confidence < min_confidence:
        if previous_state == "active" and failure_count >= support_count:
            return "weakening"
        return "emerging"
    if failure_count > support_count and previous_state in {"active", "weakening"}:
        return "weakening"
    if confidence >= min_confidence and evidence_count >= min_observations:
        if support_count >= failure_count:
            return "active"
        return "weakening"
    return "emerging"


def aggregate_observations(
    observations: list[Observation],
    *,
    existing: list[LearningSignal] | None = None,
    min_observations: int = 3,
    min_confidence: float = 0.65,
    decay_days: int = 30,
    max_signals: int = 20,
    now: datetime | None = None,
) -> list[LearningSignal]:
    stamp = now or utc_now()
    existing_map = {
        (s.signal_type, s.key): s for s in (existing or []) if s.signal_type and s.key
    }

    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for obs in observations:
        key = (obs.signal_type, obs.key)
        bucket = buckets.setdefault(
            key,
            {
                "support": 0,
                "failure": 0,
                "refs": [],
                "reason": obs.reason,
                "recommendation": obs.recommendation,
                "value": dict(obs.value),
                "hints": [],
            },
        )
        if obs.success:
            bucket["support"] += 1
        else:
            bucket["failure"] += 1
        if obs.evidence_ref:
            bucket["refs"].append(obs.evidence_ref)
        bucket["hints"].append(obs.confidence_hint)
        if obs.reason:
            bucket["reason"] = obs.reason
        if obs.recommendation:
            bucket["recommendation"] = obs.recommendation
        if obs.value:
            bucket["value"].update(obs.value)

    signals: list[LearningSignal] = []
    for (signal_type, key), bucket in buckets.items():
        prev = existing_map.get((signal_type, key))
        support = int(bucket["support"]) + (prev.support_count if prev else 0)
        failure = int(bucket["failure"]) + (prev.failure_count if prev else 0)
        # Cap counts to keep influence bounded.
        support = min(support, 50)
        failure = min(failure, 50)
        evidence = min(support + failure, 100)
        confidence = compute_confidence(
            support_count=support,
            failure_count=failure,
            evidence_count=evidence,
            min_observations=min_observations,
        )
        # Blend with hint mean lightly.
        hints = bucket["hints"] or [confidence]
        hint_mean = sum(float(h) for h in hints) / len(hints)
        confidence = clamp_unit(0.8 * confidence + 0.2 * hint_mean)

        influence = decay_influence(
            confidence,
            last_observed_at=stamp,
            now=stamp,
            decay_days=decay_days,
        )
        state = classify_state(
            confidence=confidence,
            support_count=support,
            failure_count=failure,
            evidence_count=evidence,
            influence=influence,
            min_observations=min_observations,
            min_confidence=min_confidence,
            previous_state=prev.state if prev else None,
        )
        value = dict(bucket["value"])
        value["recommendation"] = bucket["recommendation"]
        refs = list(dict.fromkeys(list(bucket["refs"]) + (prev.evidence_refs if prev else [])))[
            :10
        ]
        candidate = LearningSignal(
            signal_type=signal_type,  # type: ignore[arg-type]
            key=key,
            state=state,  # type: ignore[arg-type]
            confidence=confidence,
            support_count=support,
            failure_count=failure,
            evidence_count=evidence,
            last_observed_at=stamp,
            created_at=(prev.created_at if prev and prev.created_at else stamp),
            reason=str(bucket["reason"]),
            evidence_refs=refs,
            value=value,
            influence=influence,
        )
        validated = validate_learning_signal(candidate)
        if validated is not None:
            signals.append(validated)

    # Preserve existing signals not refreshed this cycle; apply decay/state update.
    seen = {(s.signal_type, s.key) for s in signals}
    for prev in existing or []:
        if (prev.signal_type, prev.key) in seen:
            continue
        influence = decay_influence(
            prev.confidence,
            last_observed_at=prev.last_observed_at,
            now=stamp,
            decay_days=decay_days,
        )
        state = classify_state(
            confidence=prev.confidence,
            support_count=prev.support_count,
            failure_count=prev.failure_count,
            evidence_count=prev.evidence_count,
            influence=influence,
            min_observations=min_observations,
            min_confidence=min_confidence,
            previous_state=prev.state,
        )
        if influence <= 0.0:
            state = "expired"
        updated = prev.model_copy(update={"influence": influence, "state": state})
        validated = validate_learning_signal(updated)
        if validated is not None:
            signals.append(validated)

    signals.sort(key=lambda s: (s.influence, s.confidence), reverse=True)
    return signals[: max(1, max_signals)]
