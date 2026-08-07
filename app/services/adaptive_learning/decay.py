"""Deterministic adaptive signal decay.

Formula (linear recency weighting):

    age_days = max(0, (now - last_observed_at).total_seconds() / 86400)
    influence = confidence * max(0.0, 1.0 - age_days / decay_days)

When age_days >= decay_days, influence is 0.0.
Missing timestamps → influence = confidence (treated as current).
"""

from __future__ import annotations

from datetime import datetime, timezone


def age_days(observed_at: datetime | None, now: datetime) -> float:
    if observed_at is None:
        return 0.0
    ts = observed_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return max(0.0, (now_aware - ts.astimezone(timezone.utc)).total_seconds() / 86400.0)


def decay_influence(
    confidence: float,
    *,
    last_observed_at: datetime | None,
    now: datetime,
    decay_days: float,
) -> float:
    """Return decay-weighted influence in [0, 1]."""
    conf = max(0.0, min(1.0, float(confidence)))
    if decay_days <= 0:
        return conf
    age = age_days(last_observed_at, now)
    factor = max(0.0, 1.0 - (age / float(decay_days)))
    return max(0.0, min(1.0, conf * factor))
