"""Decision trace helpers."""

from __future__ import annotations

from typing import Any

from app.services.decision.models import DecisionResult, DecisionTrace


def empty_trace(*, agent_id: str, cycle_id: str | None = None) -> DecisionTrace:
    return DecisionTrace(agent_id=agent_id, cycle_id=cycle_id)


def append_decision(
    trace: DecisionTrace,
    result: DecisionResult,
    *,
    max_items: int = 12,
) -> DecisionTrace:
    trace.add(result, max_items=max_items)
    return trace


def serialize_trace(trace: DecisionTrace) -> dict[str, Any]:
    return trace.summary()
