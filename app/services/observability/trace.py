"""Chronological stage trace helpers for observability."""

from __future__ import annotations

from typing import Any

from app.services.observability.models import ObservabilityEvent

STAGE_ORDER = (
    "research",
    "ai_security",
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
    "evaluation",
    "decision",
    "cycle",
)


def build_stage_trace(events: list[ObservabilityEvent]) -> list[dict[str, Any]]:
    """Compact chronological stage entries derived from recorded events."""
    ordered = sorted(events, key=lambda e: e.order)
    out: list[dict[str, Any]] = []
    for event in ordered:
        out.append(
            {
                "order": event.order,
                "stage": event.stage,
                "event_type": event.event_type,
                "action": event.action,
                "status": event.status,
                "reason": event.reason,
                "timestamp": event.timestamp,
                "evidence": dict(list(event.evidence.items())[:5]),
                "decision_gate": event.decision_gate,
            }
        )
    return out


def stage_sort_key(stage: str) -> int:
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return 99
