"""Helpers to build compact DecisionContext from cycle state."""

from __future__ import annotations

from typing import Any

from app.services.decision.models import DecisionContext
from app.services.research.base import normalize_url


def count_grounded_research(research_items: list[object]) -> int:
    count = 0
    for item in research_items:
        url = str(getattr(item, "url", "") or "").strip()
        if url and (normalize_url(url) or url.startswith("http")):
            count += 1
    return count


def build_decision_context(
    *,
    agent_id: str,
    cycle_id: str | None = None,
    research_items: list[object] | None = None,
    candidates: list[object] | None = None,
    selected_count: int = 0,
    rejected_count: int = 0,
    debated_pairs: list[tuple[Any, Any]] | None = None,
    consistency_triples: list[tuple[Any, Any, Any, Any]] | None = None,
    memory_meta: dict[str, Any] | None = None,
    contradiction_meta: dict[str, Any] | None = None,
    intelligence_by_topic: dict[str, dict[str, Any]] | None = None,
    adaptive_meta: dict[str, Any] | None = None,
    learning_meta: dict[str, Any] | None = None,
    final_meta: dict[str, Any] | None = None,
    publication_meta: dict[str, Any] | None = None,
    generated_post: object | None = None,
    final_validation_passed: bool = False,
) -> DecisionContext:
    research_items = research_items or []
    candidates = candidates or []
    debated_pairs = debated_pairs or []
    consistency_triples = consistency_triples or []

    debate_proceed = 0
    debate_reject = 0
    for _scored, debate in debated_pairs:
        decision = getattr(debate, "decision", None)
        value = getattr(decision, "value", decision)
        if str(value) == "proceed":
            debate_proceed += 1
        elif str(value) == "reject":
            debate_reject += 1

    approve = 0
    reject = 0
    max_score = 0.0
    for _s, debate, consistency, _m in consistency_triples:
        decision = getattr(consistency, "decision", None)
        value = getattr(decision, "value", decision)
        score = float(getattr(consistency, "overall_score", 0.0) or 0.0)
        max_score = max(max_score, score)
        if str(value) == "approve":
            # Also require debate not rejected for generation readiness.
            d_dec = getattr(getattr(debate, "decision", None), "value", getattr(debate, "decision", None))
            if str(d_dec) != "reject" and not getattr(debate, "error", None):
                approve += 1
        elif str(value) == "reject":
            reject += 1

    diversity: float | None = None
    if intelligence_by_topic:
        scores = [
            float(v.get("source_diversity_score"))
            for v in intelligence_by_topic.values()
            if v.get("source_diversity_score") is not None
        ]
        if scores:
            diversity = sum(scores) / len(scores)

    has_post = generated_post is not None
    rationale = ""
    sources: list[str] = []
    if has_post:
        rationale = str(getattr(generated_post, "rationale", "") or "")
        sources = list(getattr(generated_post, "source_urls", None) or [])
        if not sources:
            sources = list(getattr(generated_post, "sources", None) or [])

    pub = publication_meta or {}
    return DecisionContext(
        agent_id=agent_id,
        cycle_id=cycle_id,
        research_count=len(research_items),
        grounded_research_count=count_grounded_research(research_items),
        candidate_count=len(candidates),
        selected_count=selected_count,
        rejected_count=rejected_count,
        debated_count=len(debated_pairs),
        debate_proceed_count=debate_proceed,
        debate_reject_count=debate_reject,
        memory_context_available=bool((memory_meta or {}).get("items_retrieved")),
        consistency_approve_count=approve,
        consistency_reject_count=reject,
        consistency_max_score=max_score,
        contradiction_detected=bool((contradiction_meta or {}).get("detected")),
        source_diversity_score=diversity,
        adaptive_guidance={
            k: (adaptive_meta or {}).get(k)
            for k in ("overused_topics", "quality_feedback", "status")
            if (adaptive_meta or {}).get(k) is not None
        },
        learning_guidance={
            k: (learning_meta or {}).get(k)
            for k in ("recommendations", "active_strategies", "status")
            if (learning_meta or {}).get(k) is not None
        },
        final_generation_status=(final_meta or {}).get("status"),
        final_validation_passed=final_validation_passed,
        publication_status=pub.get("status"),
        has_generated_post=has_post,
        has_grounded_sources=bool(sources) or count_grounded_research(research_items) > 0,
        has_rationale=bool(rationale.strip()),
        is_duplicate=pub.get("status") == "duplicate",
    )
