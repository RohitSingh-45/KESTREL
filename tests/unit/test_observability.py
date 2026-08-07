"""Unit tests for Module 17 observability models and service helpers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.observability import ObservabilityEvent, ObservabilityService
from app.services.observability.collector import EventCollector
from app.services.observability.models import PublicationExplanation


def test_event_model_validation() -> None:
    event = ObservabilityEvent(
        agent_id="a1",
        cycle_id="c1",
        stage="selection",
        event_type="candidate_selected",
        action="select",
        status="ok",
        reason="Highest weighted score among grounded candidates",
        evidence={"final_score": 0.91, "rank": 1},
    )
    assert event.event_id
    assert event.evidence["rank"] == 1
    meta = event.to_meta()
    assert meta["event_type"] == "candidate_selected"


def test_event_bounds_and_reason_truncation() -> None:
    long_reason = "x" * 500
    event = ObservabilityEvent(
        agent_id="a1",
        stage="research",
        event_type="research_completed",
        reason=long_reason,
        evidence={f"k{i}": i for i in range(20)},
        source_urls=[f"https://example.com/{i}" for i in range(20)],
    )
    assert len(event.reason) <= 300
    assert len(event.evidence) <= 10
    assert len(event.source_urls) <= 10


def test_evidence_bounds_in_service() -> None:
    svc = ObservabilityService(max_evidence_items=3, max_reason_length=50)
    svc.begin(agent_id="a1", cycle_id="c1")
    event = svc.record(
        "candidate_selected",
        stage="selection",
        reason="r" * 200,
        evidence={f"e{i}": i for i in range(10)},
    )
    assert event is not None
    assert len(event.reason) <= 50
    assert len(event.evidence) <= 3


def test_decision_and_publication_explanations() -> None:
    svc = ObservabilityService()
    svc.begin(agent_id="a1", cycle_id="c1")
    published = svc.explain_publication(
        publication={"status": "published", "published_post_id": "p1"},
        decision={"final_action": "proceed"},
    )
    assert published.published is True
    assert published.final_reason == "published"

    blocked = svc.explain_publication(
        publication={"status": "skipped", "reason": "no_generated_post"},
        final_generation={
            "status": "skipped",
            "reason": "decision_gate_blocked_generation",
        },
    )
    assert blocked.published is False
    assert blocked.stage in {"decision", "generation"}

    consistency = svc.explain_publication(
        publication={"status": "skipped"},
        final_generation={"status": "skipped", "reason": "no_approved_finalist"},
        consistency=[{"decision": "reject"}],
    )
    assert consistency.final_reason == "consistency_gate_blocked"


def test_why_published_and_source_memory_learning() -> None:
    svc = ObservabilityService()
    why = svc.explain_why_published(
        selected={"title": "Prompt injection", "rank": 1, "final_score": 0.9},
        debate={"decision": "proceed"},
        consistency={"decision": "approve", "overall_score": 0.9},
        intelligence={
            "source_diversity_score": 0.8,
            "story_type": "new",
            "source_repetition": False,
        },
        adaptive={"status": "success", "overused_topics": ["a"]},
        learning={"status": "success", "active_strategies": ["novelty"]},
        final_generation={"status": "success"},
        publication={"status": "published"},
        evaluation={"status": "success"},
        sources=["https://arxiv.org/abs/1"],
    )
    assert why["selection_rank"] == 1
    assert why["source_grounding"]["source_count"] == 1
    assert why["adaptive"]["used"] is True

    sources = svc.source_explanation(
        source_urls=["https://arxiv.org/abs/1", "https://cisa.gov/x"],
        intelligence={"source_diversity_score": 1.0, "source_repetition": False},
        unknown_urls_rejected=True,
    )
    assert sources["grounded_url_count"] == 2
    assert "arxiv.org" in sources["source_domains"]

    memory = svc.memory_explanation(items_considered=3, keys=["prompt injection"])
    assert memory["memory_used"] is True
    assert memory["memory_items_considered"] == 3

    learning = svc.learning_explanation(
        adaptive_meta={"status": "success", "overused_topics": ["x", "y"]},
        learning_meta={"status": "success", "active_strategies": ["s1"]},
    )
    assert learning["adaptive"]["signals"] == 2
    assert learning["learning"]["strategies"] == 1


def test_zero_llm_calls() -> None:
    svc = ObservabilityService()
    svc.begin(agent_id="a1", cycle_id="c1")
    svc.record("research_started", stage="research", reason="start")
    payload = svc.finalize(cycle_status="success", publication={"status": "published"})
    assert svc.llm_calls == 0
    assert payload["published"] is True


def test_disabled_observability() -> None:
    svc = ObservabilityService(enabled=False)
    svc.begin(agent_id="a1", cycle_id="c1")
    assert svc.record("research_started", stage="research") is None
    payload = svc.finalize(cycle_status="success")
    assert payload["enabled"] is False
    assert payload["event_count"] == 0


def test_max_events_priority_eviction() -> None:
    collector = EventCollector(max_events=3)
    for i in range(5):
        collector.add(
            ObservabilityEvent(
                agent_id="a1",
                stage="research",
                event_type="research_started",
                reason=str(i),
                priority=10,
            )
        )
    collector.add(
        ObservabilityEvent(
            agent_id="a1",
            stage="cycle",
            event_type="cycle_completed",
            reason="keep",
            priority=95,
        )
    )
    assert len(collector.events) == 3
    assert collector.dropped >= 1
    assert any(e.event_type == "cycle_completed" for e in collector.events)


def test_config_validation_observability_settings() -> None:
    with pytest.raises(ValidationError):
        Settings(OBSERVABILITY_MAX_EVENTS=0)
    with pytest.raises(ValidationError):
        Settings(OBSERVABILITY_MAX_REASON_LENGTH=1)
    with pytest.raises(ValidationError):
        Settings(OBSERVABILITY_MAX_EVIDENCE_ITEMS=0)
    ok = Settings(OBSERVABILITY_MAX_EVENTS=50)
    assert ok.observability_max_events == 50
    assert ok.observability_enabled is True


def test_publication_explanation_model() -> None:
    expl = PublicationExplanation(
        published=False,
        final_reason="consistency_gate_blocked",
        stage="consistency",
        explanation="No finalist satisfied the minimum consistency requirement.",
    )
    assert expl.to_meta()["published"] is False
