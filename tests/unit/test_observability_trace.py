"""Trace ordering and persistence helpers for Module 17."""

from __future__ import annotations

from app.services.observability import ObservabilityEvent, ObservabilityService
from app.services.observability.trace import build_stage_trace


def test_trace_ordering_is_chronological() -> None:
    svc = ObservabilityService()
    svc.begin(agent_id="a1", cycle_id="c1")
    svc.record("research_started", stage="research", reason="start")
    svc.record("candidate_generated", stage="candidate", reason="cand")
    svc.record("candidate_selected", stage="selection", action="select", reason="sel")
    svc.record("debate_completed", stage="debate", reason="deb")
    svc.record("publishing_completed", stage="publishing", reason="pub")
    payload = svc.finalize(
        cycle_status="success",
        publication={"status": "published"},
    )
    orders = [e["order"] for e in payload["events"]]
    assert orders == sorted(orders)
    stages = [t["stage"] for t in payload["trace"]]
    assert stages[0] == "research"
    assert "publishing" in stages


def test_build_stage_trace_includes_reasons() -> None:
    events = [
        ObservabilityEvent(
            agent_id="a1",
            order=1,
            stage="consistency",
            event_type="consistency_rejected",
            action="reject",
            status="blocked",
            reason="Unsupported certainty",
            evidence={"overall_score": 0.31},
        ),
        ObservabilityEvent(
            agent_id="a1",
            order=2,
            stage="publishing",
            event_type="publishing_skipped",
            action="skip",
            status="skipped",
            reason="no finalist",
        ),
    ]
    trace = build_stage_trace(events)
    assert trace[0]["reason"] == "Unsupported certainty"
    assert trace[0]["evidence"]["overall_score"] == 0.31
    assert trace[1]["event_type"] == "publishing_skipped"


def test_trace_bounded_by_max_events() -> None:
    svc = ObservabilityService(max_events=5)
    svc.begin(agent_id="a1", cycle_id="c1")
    for i in range(20):
        svc.record("research_started", stage="research", reason=str(i))
    payload = svc.finalize(cycle_status="skipped", cycle_reason="cap")
    assert payload["event_count"] <= 5
    assert payload["dropped_events"] >= 1


def test_not_published_explanation_from_finalize() -> None:
    svc = ObservabilityService()
    svc.begin(agent_id="a1", cycle_id="c1")
    svc.record(
        "consistency_rejected",
        stage="consistency",
        action="reject",
        reason="Unsupported certainty and insufficient grounding",
        evidence={"overall_score": 0.31},
    )
    payload = svc.finalize(
        cycle_status="success",
        publication={"status": "skipped", "reason": "no_generated_post"},
        final_generation={"status": "skipped", "reason": "no_approved_finalist"},
        consistency=[{"decision": "reject", "overall_score": 0.31}],
    )
    assert payload["published"] is False
    assert payload["why"]["final_reason"] == "consistency_gate_blocked"
    assert "consistency" in payload["why"]["stage"]
