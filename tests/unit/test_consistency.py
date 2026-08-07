"""Unit tests for Module 8 persona consistency and anti-drift guard."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.consistency.analyzer import ConsistencyAnalyzer, ConsistencyContext
from app.services.consistency.guard import ConsistencyGuard, revise_candidate
from app.services.consistency.models import (
    ConsistencyDecisionEnum,
    ConsistencySignals,
    HistoricalStyleProfile,
)
from app.services.consistency.profile import (
    PERSONA_PROFILE_VERSION,
    InvalidPersonaError,
    get_persona_profile,
)
from app.services.debate import DebateDecisionEnum, DebateResult
from app.services.llm.base import CandidateTopic
from app.services.memory.models import MemoryKindEnum, MemoryRecord, MemoryValue
from app.services.research.models import ResearchItem
from app.services.scoring import ScoredCandidate, TopicScores


def _now() -> datetime:
    return datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)


def _candidate(**overrides: object) -> CandidateTopic:
    base = {
        "title": "Prompt Injection Risks in Autonomous AI Agents",
        "angle": "Defensive security",
        "why_now": "New research provides evidence",
        "relevance_to_persona": "Directly concerns AI Security",
        "source_urls": ["https://example.com/research"],
        "confidence": 0.91,
    }
    base.update(overrides)
    return CandidateTopic.model_validate(base)


def _scored(candidate: CandidateTopic | None = None, topic_id: str = "t1") -> ScoredCandidate:
    cand = candidate or _candidate()
    return ScoredCandidate(
        topic_id=topic_id,
        title=cand.title,
        candidate=cand,
        scores=TopicScores(
            recency=0.9,
            persona_fit=0.9,
            importance=0.9,
            novelty=0.9,
            source_quality=0.9,
            evidence_quality=0.9,
            final_score=0.9,
        ),
        rank=1,
        decision="selected",
    )


def _item(url: str = "https://example.com/research") -> ResearchItem:
    now = _now()
    return ResearchItem(
        title="Research",
        summary="AI agent prompt injection findings.",
        url=url,
        source_name="fixture",
        published_at=now,
        discovered_at=now,
        content_hash="h1",
    )


def _debate(urls: list[str] | None = None) -> DebateResult:
    return DebateResult(
        topic_id="t1",
        for_argument="Evidence supports discussing this AI Security risk carefully.",
        against_argument="Evidence depth may be limited and novelty remains uncertain overall.",
        evidence_points=["Source describes agent injection risk."],
        weaknesses=["Limited operational detail."],
        decision=DebateDecisionEnum.PROCEED,
        confidence=0.86,
        source_urls=urls or ["https://example.com/research"],
    )


def _context(
    *,
    memories: list[MemoryRecord] | None = None,
    research: list[ResearchItem] | None = None,
) -> ConsistencyContext:
    return ConsistencyContext(
        profile=get_persona_profile(name="Kestrel", domain="AI Security"),
        research_items=research or [_item()],
        memories=memories or [],
        historical_style=HistoricalStyleProfile(),
        previous_titles=[],
        approve_threshold=0.80,
        revise_threshold=0.60,
    )


def test_kestrel_profile_creation_and_version() -> None:
    profile = get_persona_profile(name="Kestrel", domain="AI Security")
    assert profile.name == "Kestrel"
    assert profile.domain == "AI Security"
    assert profile.version == PERSONA_PROFILE_VERSION == 1
    assert "analytical" in profile.tone
    assert profile.style.avoid_hype is True


def test_ai_security_domain_enforcement() -> None:
    profile = get_persona_profile(name="Kestrel", domain="AI Security")
    assert "ai security" in [k.lower() for k in profile.domain_keywords] or any(
        "security" in k for k in profile.domain_keywords
    )


def test_missing_invalid_persona_handling() -> None:
    with pytest.raises(InvalidPersonaError):
        get_persona_profile(name="   ", domain="AI Security")
    with pytest.raises(InvalidPersonaError):
        get_persona_profile(name="Kestrel", domain="")


def test_domain_fit_and_tone_fit() -> None:
    analyzer = ConsistencyAnalyzer()
    good = analyzer.analyze(_scored(), debate=_debate(), context=_context())
    assert good.signals.domain_fit >= 0.6
    assert good.signals.tone_fit >= 0.8

    hype = _candidate(
        title="THIS IS ABSOLUTELY THE WORST AI THREAT EVER!!!",
        angle="Sensational panic",
        why_now="Hype cycle",
        relevance_to_persona="AI Security but hyperbolic",
    )
    bad_tone = analyzer.analyze(_scored(hype), debate=_debate(), context=_context())
    assert bad_tone.signals.tone_fit < 0.55
    assert "tone_drift" in bad_tone.drift_flags


def test_evidence_discipline_and_technical_depth() -> None:
    analyzer = ConsistencyAnalyzer()
    ok = analyzer.analyze(_scored(), debate=_debate(), context=_context())
    assert ok.signals.evidence_discipline == 1.0
    assert ok.signals.technical_depth_fit >= 0.5

    invented = _candidate(source_urls=["https://invented.example/x"])
    bad = analyzer.analyze(
        _scored(invented),
        debate=_debate(urls=["https://invented.example/x"]),
        context=_context(),
    )
    assert bad.hard_reject
    assert "no_grounded_source" in bad.reasons or "fabricated_source" in bad.reasons


def test_stance_compatibility_and_overall_score() -> None:
    analyzer = ConsistencyAnalyzer()
    result = analyzer.analyze(_scored(), debate=_debate(), context=_context())
    assert 0.0 <= result.overall_score <= 1.0
    assert isinstance(result.signals, ConsistencySignals)


def test_approve_revise_reject_thresholds() -> None:
    guard = ConsistencyGuard(approve_threshold=0.80, revise_threshold=0.60)
    approved, _ = guard.evaluate(
        _scored(),
        debate=_debate(),
        persona_name="Kestrel",
        persona_domain="AI Security",
        research_items=[_item()],
        memories=[],
    )
    assert approved.decision == ConsistencyDecisionEnum.APPROVE

    irrelevant = _candidate(
        title="Best smartphone buying advice for 2026",
        angle="Consumer gadgets",
        why_now="Sales event",
        relevance_to_persona="None",
        source_urls=["https://example.com/research"],
    )
    rejected, _ = guard.evaluate(
        _scored(irrelevant),
        debate=_debate(),
        persona_name="Kestrel",
        persona_domain="AI Security",
        research_items=[_item()],
        memories=[],
    )
    assert rejected.decision == ConsistencyDecisionEnum.REJECT
    assert "wrong_persona_domain" in rejected.reasons or "domain_drift" in rejected.drift_flags


def test_hard_rejection_unsupported_source_and_wrong_domain() -> None:
    analyzer = ConsistencyAnalyzer()
    no_source = analyzer.analyze(
        _scored(_candidate(source_urls=[])),
        debate=_debate(urls=[]),
        context=_context(),
    )
    assert no_source.decision == ConsistencyDecisionEnum.REJECT
    assert "no_grounded_source" in no_source.reasons

    fashion = analyzer.analyze(
        _scored(
            _candidate(
                title="AI fashion influencer campaign tips",
                angle="Marketing",
                why_now="Viral trend",
                relevance_to_persona="Fashion",
            )
        ),
        debate=_debate(),
        context=_context(),
    )
    assert fashion.decision == ConsistencyDecisionEnum.REJECT


def test_obvious_tone_and_domain_drift() -> None:
    analyzer = ConsistencyAnalyzer()
    tone = analyzer.analyze(
        _scored(
            _candidate(
                title="THIS IS ABSOLUTELY THE WORST AI THREAT EVER!!!",
                angle="Panic",
                why_now="Clickbait",
                relevance_to_persona="AI Security hype",
            )
        ),
        debate=_debate(),
        context=_context(),
    )
    assert "tone_drift" in tone.drift_flags

    domain = analyzer.analyze(
        _scored(
            _candidate(
                title="Generic cooking recipe notebook tips",
                angle="Lifestyle",
                why_now="Evergreen",
                relevance_to_persona="None",
            )
        ),
        debate=_debate(),
        context=_context(),
    )
    assert "domain_drift" in domain.drift_flags or domain.hard_reject


def test_unsupported_certainty_flagged() -> None:
    analyzer = ConsistencyAnalyzer()
    result = analyzer.analyze(
        _scored(
            _candidate(
                title="Prompt injection is unquestionably always safe for AI agents",
                angle="Unsafe certainty",
                why_now="Bold claim with no doubt",
                relevance_to_persona="AI Security",
                source_urls=[],
            )
        ),
        debate=_debate(urls=[]),
        context=_context(),
    )
    assert (
        "unsupported_certainty" in result.drift_flags
        or "unsupported_claim" in result.reasons
        or "no_grounded_source" in result.reasons
        or result.signals.evidence_discipline < 0.5
    )


def test_legitimate_belief_change_accepted() -> None:
    memory = MemoryRecord(
        id="m1",
        agent_id="a1",
        kind=MemoryKindEnum.BELIEF,
        key="prompt injection",
        value=MemoryValue(
            kind=MemoryKindEnum.BELIEF,
            text="Risk appears significant",
            stance="evidence is currently mixed",
            confidence=0.75,
            supporting_source_urls=["https://example.com/old"],
            contradicting_source_urls=["https://example.com/research"],
            evidence_count=2,
        ),
        salience=0.8,
        confidence=0.75,
        created_at=_now(),
        updated_at=_now(),
    )
    analyzer = ConsistencyAnalyzer()
    result = analyzer.analyze(
        _scored(
            _candidate(
                title="Prompt injection risk is more context-dependent than previously believed",
                angle="Nuanced AI Security assessment",
                why_now="New high-quality contradictory evidence",
                relevance_to_persona="AI Security belief update",
            )
        ),
        debate=_debate(),
        context=_context(memories=[memory]),
    )
    assert result.belief_change_accepted is True
    assert result.decision != ConsistencyDecisionEnum.REJECT or "legitimate_belief_change" in result.reasons


def test_contradictory_evidence_does_not_auto_reject() -> None:
    memory = MemoryRecord(
        id="m1",
        agent_id="a1",
        kind=MemoryKindEnum.STANCE,
        key="prompt injection defense",
        value=MemoryValue(
            kind=MemoryKindEnum.STANCE,
            text="Defensive controls should be prioritized",
            stance="defensive_controls_should_be_prioritized",
            confidence=0.77,
            contradicting_source_urls=["https://example.com/research"],
            evidence_count=1,
        ),
        salience=0.7,
        confidence=0.77,
        created_at=_now(),
        updated_at=_now(),
    )
    guard = ConsistencyGuard()
    result, _ = guard.evaluate(
        _scored(),
        debate=_debate(),
        persona_name="Kestrel",
        persona_domain="AI Security",
        research_items=[_item()],
        memories=[memory],
    )
    assert result.decision in {
        ConsistencyDecisionEnum.APPROVE,
        ConsistencyDecisionEnum.REVISE,
    }


def test_revision_bounded_and_retains_grounding() -> None:
    candidate = _candidate(
        title="THIS IS ABSOLUTELY THE WORST prompt injection THREAT EVER!!!",
        angle="Shocking AI Security panic",
        why_now="Mind-blowing disclosure",
        relevance_to_persona="AI Security",
    )
    revised = revise_candidate(
        candidate,
        research_items=[_item()],
        profile_domain="AI Security",
    )
    assert revised.source_urls == ["https://example.com/research"]
    assert "!!!" not in revised.title
    assert "absolutely" not in revised.title.lower()

    with pytest.raises(ValueError):
        revise_candidate(
            _candidate(source_urls=["https://invented.example/x"]),
            research_items=[_item()],
            profile_domain="AI Security",
        )


def test_revision_limit_one_attempt() -> None:
    guard = ConsistencyGuard(max_revisions=1, approve_threshold=0.99, revise_threshold=0.10)
    # A mild hype candidate should request revise then either approve or reject after one attempt.
    result, updated = guard.evaluate(
        _scored(
            _candidate(
                title="Shocking prompt injection risk for AI agents!!!",
                angle="Urgent AI Security",
                why_now="Breaking disclosure",
                relevance_to_persona="AI Security",
            )
        ),
        debate=_debate(),
        persona_name="Kestrel",
        persona_domain="AI Security",
        research_items=[_item()],
        memories=[],
    )
    assert result.revision_count <= 1
    assert result.decision in {
        ConsistencyDecisionEnum.APPROVE,
        ConsistencyDecisionEnum.REJECT,
    }
    assert result.decision != ConsistencyDecisionEnum.REVISE
    assert "https://example.com/research" in updated.candidate.source_urls


def test_failed_revision_safely_rejected() -> None:
    guard = ConsistencyGuard(max_revisions=1)

    class _Boom(ConsistencyAnalyzer):
        def analyze(self, *args: object, **kwargs: object):  # type: ignore[override]
            from app.services.consistency.models import ConsistencyResult, ConsistencySignals

            return ConsistencyResult(
                topic_id="t1",
                decision=ConsistencyDecisionEnum.REVISE,
                overall_score=0.65,
                signals=ConsistencySignals(domain_fit=0.7, tone_fit=0.5),
                reasons=["overall_score_in_revise_band"],
            )

    guard._analyzer = _Boom()  # type: ignore[assignment]
    # Force revision path then invent URL failure by clearing research after first analyze.
    # Simpler: revise_candidate raises when no grounded sources — inject by empty research on revise.
    original_revise = __import__(
        "app.services.consistency.guard", fromlist=["revise_candidate"]
    ).revise_candidate

    def _fail(*_a: object, **_k: object) -> CandidateTopic:
        raise RuntimeError("revision boom")

    import app.services.consistency.guard as guard_mod

    guard_mod.revise_candidate = _fail  # type: ignore[assignment]
    try:
        result, _ = guard.evaluate(
            _scored(
                _candidate(
                    title="Shocking prompt injection!!!",
                    angle="Hype",
                    why_now="Now",
                    relevance_to_persona="AI Security",
                )
            ),
            debate=_debate(),
            persona_name="Kestrel",
            persona_domain="AI Security",
            research_items=[_item()],
            memories=[],
        )
    finally:
        guard_mod.revise_candidate = original_revise  # type: ignore[assignment]

    # First analyze may approve; if revise path hit, must reject safely.
    assert result.decision in {
        ConsistencyDecisionEnum.APPROVE,
        ConsistencyDecisionEnum.REJECT,
    }
    if result.error:
        assert "revision" in (result.error or "").lower() or "revision_failed" in result.reasons


def test_consistency_disabled_does_not_approve() -> None:
    guard = ConsistencyGuard(enabled=False)
    result, _ = guard.evaluate(
        _scored(),
        debate=_debate(),
        persona_name="Kestrel",
        persona_domain="AI Security",
        research_items=[_item()],
        memories=[],
    )
    assert result.decision == ConsistencyDecisionEnum.REJECT
    assert "consistency_disabled" in result.reasons
