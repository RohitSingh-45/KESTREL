"""Deterministic decision gates. Orchestration only — never mutate module results."""

from __future__ import annotations

from app.services.decision.models import DecisionContext, DecisionResult
from app.services.decision.policy import DecisionPolicy


def research_gate(ctx: DecisionContext, policy: DecisionPolicy) -> DecisionResult:
    evidence = {
        "research_count": ctx.research_count,
        "grounded_research_count": ctx.grounded_research_count,
    }
    if ctx.research_count < policy.min_research_items:
        return DecisionResult(
            action="skip",
            gate="research",
            reason="No research sources available.",
            confidence=0.95,
            evidence=evidence,
            blocking=False,
        )
    if ctx.grounded_research_count < policy.min_grounded_items:
        return DecisionResult(
            action="skip",
            gate="research",
            reason="Insufficient grounded research items.",
            confidence=0.9,
            evidence=evidence,
            blocking=False,
        )
    return DecisionResult(
        action="proceed",
        gate="research",
        reason=f"{ctx.research_count} valid research items available",
        confidence=0.9,
        evidence=evidence,
    )


def candidate_gate(ctx: DecisionContext, policy: DecisionPolicy) -> DecisionResult:
    evidence = {"candidate_count": ctx.candidate_count}
    if ctx.candidate_count < policy.min_candidates:
        return DecisionResult(
            action="skip",
            gate="candidate",
            reason="No grounded AI Security candidates available.",
            confidence=0.95,
            evidence=evidence,
        )
    return DecisionResult(
        action="proceed",
        gate="candidate",
        reason=f"{ctx.candidate_count} grounded candidates generated",
        confidence=0.9,
        evidence=evidence,
    )


def selection_gate(ctx: DecisionContext, policy: DecisionPolicy) -> DecisionResult:
    evidence = {
        "selected_count": ctx.selected_count,
        "rejected_count": ctx.rejected_count,
    }
    if ctx.selected_count < policy.min_selected:
        return DecisionResult(
            action="skip",
            gate="selection",
            reason="No candidates selected by scoring funnel.",
            confidence=0.92,
            evidence=evidence,
        )
    return DecisionResult(
        action="proceed",
        gate="selection",
        reason=f"{ctx.selected_count} candidates selected",
        confidence=0.9,
        evidence=evidence,
    )


def debate_gate(ctx: DecisionContext, policy: DecisionPolicy) -> DecisionResult:
    evidence = {
        "debated_count": ctx.debated_count,
        "debate_proceed_count": ctx.debate_proceed_count,
        "debate_reject_count": ctx.debate_reject_count,
    }
    if ctx.debated_count == 0:
        return DecisionResult(
            action="skip",
            gate="debate",
            reason="No debate results available for selected topics.",
            confidence=0.9,
            evidence=evidence,
        )
    if ctx.debate_proceed_count > 0:
        return DecisionResult(
            action="proceed",
            gate="debate",
            reason="Top candidate passed debate guard",
            confidence=0.88,
            evidence=evidence,
        )
    if policy.allow_uncertain_debate and ctx.debated_count > ctx.debate_reject_count:
        return DecisionResult(
            action="proceed",
            gate="debate",
            reason="Uncertain debate allowed by policy.",
            confidence=0.55,
            evidence=evidence,
        )
    return DecisionResult(
        action="skip",
        gate="debate",
        reason="All debated topics were rejected by debate guard.",
        confidence=0.9,
        evidence=evidence,
        blocking=False,
    )


def memory_gate(ctx: DecisionContext, _policy: DecisionPolicy) -> DecisionResult:
    """Inspect-only. MemoryService remains authoritative; never mutate."""
    evidence = {"memory_context_available": ctx.memory_context_available}
    return DecisionResult(
        action="proceed",
        gate="memory",
        reason=(
            "Memory context available for consistency."
            if ctx.memory_context_available
            else "Memory context empty; continuing with consistency authority."
        ),
        confidence=0.8 if ctx.memory_context_available else 0.6,
        evidence=evidence,
    )


def consistency_gate(ctx: DecisionContext, policy: DecisionPolicy) -> DecisionResult:
    evidence = {
        "consistency_approve_count": ctx.consistency_approve_count,
        "consistency_reject_count": ctx.consistency_reject_count,
        "consistency_max_score": ctx.consistency_max_score,
    }
    if ctx.consistency_approve_count <= 0:
        return DecisionResult(
            action="stop",
            gate="consistency",
            reason="Persona/evidence consistency hard-rejected all finalists.",
            confidence=0.95,
            evidence=evidence,
            blocking=True,
        )
    if ctx.consistency_max_score < policy.min_consistency_score:
        return DecisionResult(
            action="stop",
            gate="consistency",
            reason="Consistency score below decision policy threshold.",
            confidence=0.9,
            evidence=evidence,
            blocking=True,
        )
    return DecisionResult(
        action="proceed",
        gate="consistency",
        reason="Persona and evidence consistency passed",
        confidence=0.9,
        evidence=evidence,
    )


def intelligence_gate(ctx: DecisionContext, _policy: DecisionPolicy) -> DecisionResult:
    """Context only — never unconditional blocker for repeated sources."""
    evidence = {
        "contradiction_detected": ctx.contradiction_detected,
        "source_diversity_score": ctx.source_diversity_score,
    }
    return DecisionResult(
        action="proceed",
        gate="intelligence",
        reason=(
            "Intelligence metadata available; contradiction noted."
            if ctx.contradiction_detected
            else "Intelligence metadata inspected; continue."
        ),
        confidence=0.75,
        evidence=evidence,
    )


def adaptive_learning_gate(ctx: DecisionContext, _policy: DecisionPolicy) -> DecisionResult:
    """Guidance-only. Never mutates weights/persona/prompts."""
    has_guidance = bool(ctx.adaptive_guidance) or bool(ctx.learning_guidance)
    return DecisionResult(
        action="proceed",
        gate="learning" if ctx.learning_guidance else "adaptive",
        reason=(
            "Adaptive/learning guidance available for generation context."
            if has_guidance
            else "No adaptive/learning guidance; continue with defaults."
        ),
        confidence=0.7,
        evidence={
            "adaptive_keys": list(ctx.adaptive_guidance.keys())[:5],
            "learning_keys": list(ctx.learning_guidance.keys())[:5],
        },
    )


def generation_gate(ctx: DecisionContext, policy: DecisionPolicy) -> DecisionResult:
    evidence = {
        "selected_count": ctx.selected_count,
        "consistency_approve_count": ctx.consistency_approve_count,
        "debate_proceed_count": ctx.debate_proceed_count,
        "has_grounded_sources": ctx.has_grounded_sources,
    }
    if ctx.consistency_approve_count <= 0:
        return DecisionResult(
            action="skip",
            gate="generation",
            reason="No consistency-approved finalist for final generation.",
            confidence=0.95,
            evidence=evidence,
        )
    if ctx.debate_proceed_count <= 0 and not policy.allow_uncertain_debate:
        return DecisionResult(
            action="skip",
            gate="generation",
            reason="No debate-proceed finalist available for generation.",
            confidence=0.9,
            evidence=evidence,
        )
    if not ctx.has_grounded_sources and ctx.grounded_research_count < 1:
        return DecisionResult(
            action="skip",
            gate="generation",
            reason="Missing grounded sources for final generation.",
            confidence=0.95,
            evidence=evidence,
            blocking=True,
        )
    return DecisionResult(
        action="proceed",
        gate="generation",
        reason="All required inputs available",
        confidence=0.9,
        evidence=evidence,
    )


def publishing_gate(ctx: DecisionContext, _policy: DecisionPolicy) -> DecisionResult:
    evidence = {
        "has_generated_post": ctx.has_generated_post,
        "has_rationale": ctx.has_rationale,
        "has_grounded_sources": ctx.has_grounded_sources,
        "final_validation_passed": ctx.final_validation_passed,
        "consistency_approve_count": ctx.consistency_approve_count,
        "is_duplicate": ctx.is_duplicate,
        "publication_status": ctx.publication_status,
    }
    if ctx.is_duplicate or ctx.publication_status == "duplicate":
        return DecisionResult(
            action="skip",
            gate="publishing",
            reason="Duplicate content hash; skip publishing.",
            confidence=0.99,
            evidence=evidence,
        )
    if not ctx.has_generated_post:
        return DecisionResult(
            action="stop",
            gate="publishing",
            reason="No generated post available for publishing.",
            confidence=0.95,
            evidence=evidence,
            blocking=True,
        )
    if not ctx.has_rationale or not ctx.has_grounded_sources:
        return DecisionResult(
            action="stop",
            gate="publishing",
            reason="Generated post missing rationale or grounded sources.",
            confidence=0.95,
            evidence=evidence,
            blocking=True,
        )
    if not ctx.final_validation_passed:
        return DecisionResult(
            action="stop",
            gate="publishing",
            reason="Final validation failed; do not publish.",
            confidence=0.95,
            evidence=evidence,
            blocking=True,
        )
    if ctx.consistency_approve_count <= 0:
        return DecisionResult(
            action="stop",
            gate="publishing",
            reason="Consistency no longer approved; do not publish.",
            confidence=0.95,
            evidence=evidence,
            blocking=True,
        )
    return DecisionResult(
        action="proceed",
        gate="publishing",
        reason="Final validation passed",
        confidence=0.92,
        evidence=evidence,
    )
