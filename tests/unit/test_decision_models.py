"""Unit tests for Module 16 decision models."""

from __future__ import annotations

from app.services.decision.models import (
    DecisionContext,
    DecisionResult,
    DecisionTrace,
    clamp_unit,
)


def test_decision_model_validation() -> None:
    result = DecisionResult(
        action="proceed",
        gate="research",
        reason="ok",
        confidence=1.5,
        evidence={"research_count": 3},
    )
    assert result.confidence == 1.0
    assert result.to_meta()["gate"] == "research"


def test_confidence_clamping() -> None:
    assert clamp_unit(-1) == 0.0
    assert clamp_unit(2) == 1.0
    assert clamp_unit(0.4) == 0.4
    result = DecisionResult(
        action="skip", gate="candidate", reason="none", confidence=-5
    )
    assert result.confidence == 0.0


def test_decision_trace_serialization_and_bounds() -> None:
    trace = DecisionTrace(agent_id="a1", cycle_id="c1")
    for i in range(20):
        trace.add(
            DecisionResult(
                action="proceed",
                gate="research",
                reason=f"step {i}",
                confidence=0.9,
            ),
            max_items=12,
        )
    assert len(trace.decisions) == 12
    summary = trace.summary()
    assert summary["final_action"] == "proceed"
    assert summary["gates_passed"] == 12
    assert len(summary["trace"]) == 12


def test_decision_context_compact() -> None:
    ctx = DecisionContext(
        agent_id="a1",
        cycle_id="c1",
        research_count=5,
        candidate_count=3,
        selected_count=2,
    )
    meta = ctx.to_meta()
    assert "agent_id" in meta
    assert "openai_api_key" not in meta
    assert meta["research_count"] == 5


def test_decision_trace_counts_blocked() -> None:
    trace = DecisionTrace(agent_id="a1", cycle_id="c1")
    trace.add(
        DecisionResult(
            action="proceed", gate="research", reason="ok", confidence=0.9
        )
    )
    trace.add(
        DecisionResult(
            action="stop",
            gate="consistency",
            reason="rejected",
            confidence=0.95,
            blocking=True,
        )
    )
    summary = trace.summary()
    assert summary["gates_blocked"] >= 1
    assert summary["final_action"] == "stop"
