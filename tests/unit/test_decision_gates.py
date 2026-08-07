"""Unit tests for Module 16 decision gates."""

from __future__ import annotations

from app.services.decision.gates import (
    candidate_gate,
    consistency_gate,
    debate_gate,
    generation_gate,
    publishing_gate,
    research_gate,
    selection_gate,
)
from app.services.decision.models import DecisionContext
from app.services.decision.policy import DecisionPolicy


def _ctx(**kwargs: object) -> DecisionContext:
    base = {"agent_id": "a1", "cycle_id": "c1"}
    base.update(kwargs)
    return DecisionContext.model_validate(base)


def test_research_gate_success() -> None:
    result = research_gate(
        _ctx(research_count=5, grounded_research_count=4),
        DecisionPolicy(),
    )
    assert result.action == "proceed"
    assert result.gate == "research"


def test_research_gate_skip() -> None:
    result = research_gate(
        _ctx(research_count=0, grounded_research_count=0),
        DecisionPolicy(),
    )
    assert result.action == "skip"


def test_candidate_gate_success_and_empty() -> None:
    ok = candidate_gate(_ctx(candidate_count=3), DecisionPolicy())
    empty = candidate_gate(_ctx(candidate_count=0), DecisionPolicy())
    assert ok.action == "proceed"
    assert empty.action == "skip"


def test_selection_gate_success_and_empty() -> None:
    ok = selection_gate(_ctx(selected_count=2, rejected_count=1), DecisionPolicy())
    empty = selection_gate(_ctx(selected_count=0, rejected_count=4), DecisionPolicy())
    assert ok.action == "proceed"
    assert empty.action == "skip"


def test_debate_proceed_and_reject_gate() -> None:
    proceed = debate_gate(
        _ctx(debated_count=2, debate_proceed_count=1, debate_reject_count=1),
        DecisionPolicy(),
    )
    reject = debate_gate(
        _ctx(debated_count=2, debate_proceed_count=0, debate_reject_count=2),
        DecisionPolicy(allow_uncertain_debate=False),
    )
    assert proceed.action == "proceed"
    assert reject.action == "skip"


def test_consistency_proceed_and_reject_gate() -> None:
    ok = consistency_gate(
        _ctx(consistency_approve_count=1, consistency_max_score=0.9),
        DecisionPolicy(min_consistency_score=0.7),
    )
    blocked = consistency_gate(
        _ctx(consistency_approve_count=0, consistency_reject_count=2),
        DecisionPolicy(),
    )
    assert ok.action == "proceed"
    assert blocked.action == "stop"
    assert blocked.blocking is True


def test_final_generation_gate() -> None:
    ok = generation_gate(
        _ctx(
            consistency_approve_count=1,
            debate_proceed_count=1,
            has_grounded_sources=True,
            grounded_research_count=2,
        ),
        DecisionPolicy(),
    )
    blocked = generation_gate(
        _ctx(consistency_approve_count=0, debate_proceed_count=0),
        DecisionPolicy(),
    )
    assert ok.action == "proceed"
    assert blocked.action == "skip"


def test_intelligence_and_memory_gates_are_non_blocking() -> None:
    from app.services.decision.gates import intelligence_gate, memory_gate

    mem = memory_gate(_ctx(memory_context_available=False), DecisionPolicy())
    intel = intelligence_gate(
        _ctx(contradiction_detected=True, source_diversity_score=0.4),
        DecisionPolicy(),
    )
    assert mem.action == "proceed"
    assert intel.action == "proceed"


def test_generation_gate_blocks_without_grounding() -> None:
    result = generation_gate(
        _ctx(
            consistency_approve_count=1,
            debate_proceed_count=1,
            has_grounded_sources=False,
            grounded_research_count=0,
        ),
        DecisionPolicy(),
    )
    assert result.action == "skip"
    assert result.blocking is True


def test_publishing_gate_and_duplicate() -> None:
    ok = publishing_gate(
        _ctx(
            has_generated_post=True,
            has_rationale=True,
            has_grounded_sources=True,
            final_validation_passed=True,
            consistency_approve_count=1,
        ),
        DecisionPolicy(),
    )
    dup = publishing_gate(
        _ctx(
            has_generated_post=True,
            has_rationale=True,
            has_grounded_sources=True,
            final_validation_passed=True,
            consistency_approve_count=1,
            is_duplicate=True,
            publication_status="duplicate",
        ),
        DecisionPolicy(),
    )
    invalid = publishing_gate(
        _ctx(has_generated_post=False),
        DecisionPolicy(),
    )
    assert ok.action == "proceed"
    assert dup.action == "skip"
    assert invalid.action == "stop"
