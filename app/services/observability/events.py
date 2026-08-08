"""Event type priorities for bounded observability buffers."""

from __future__ import annotations

# Higher number = more important (kept when buffer is full).
EVENT_PRIORITY: dict[str, int] = {
    "cycle_failed": 100,
    "cycle_skipped": 95,
    "cycle_completed": 95,
    "publishing_completed": 90,
    "publishing_skipped": 90,
    "decision_gate_blocked": 88,
    "final_validation_failed": 85,
    "consistency_rejected": 84,
    "evaluation_completed": 80,
    "publishing_started": 75,
    "final_generation_completed": 74,
    "final_generation_started": 70,
    "decision_gate_passed": 65,
    "candidate_selected": 60,
    "candidate_rejected": 58,
    "debate_completed": 55,
    "consistency_checked": 54,
    "contradiction_detected": 53,
    "adaptive_guidance_applied": 45,
    "learning_guidance_applied": 45,
    "intelligence_analyzed": 40,
    "memory_updated": 38,
    "memory_retrieved": 35,
    "candidate_generated": 30,
    "candidate_skipped": 55,
    "candidate_failed": 70,
    "ai_security_filter_completed": 28,
    "research_completed": 25,
    "research_started": 20,
}


def priority_for(event_type: str) -> int:
    return EVENT_PRIORITY.get(event_type, 50)
