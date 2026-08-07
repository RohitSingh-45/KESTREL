"""Unit tests for deterministic topic scoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.llm.base import CandidateTopic
from app.services.research.models import ResearchItem
from app.services.scoring import (
    DEFAULT_WEIGHTS,
    HistoricalTopic,
    ScoringContext,
    TopicScoringService,
    compute_final_score,
    score_evidence_quality,
    score_importance,
    score_novelty,
    score_persona_fit,
    score_recency,
    score_source_quality,
)


def _now() -> datetime:
    return datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


def _candidate(**overrides: object) -> CandidateTopic:
    base = {
        "title": "Prompt injection bypass in production agents",
        "angle": "Operational AI security risk",
        "why_now": "Fresh advisory on LLM agent compromise",
        "relevance_to_persona": "Core AI Security topic",
        "source_urls": ["https://arxiv.org/abs/2608.1"],
        "confidence": 0.8,
    }
    base.update(overrides)
    return CandidateTopic.model_validate(base)


def _item(
    url: str = "https://arxiv.org/abs/2608.1",
    *,
    summary: str = "A detailed summary",
    published_at: datetime | None = None,
) -> ResearchItem:
    return ResearchItem(
        title="Paper",
        summary=summary,
        url=url,
        source_name="arXiv",
        published_at=published_at,
        discovered_at=_now(),
        content_hash=f"h-{url}",
    )


def test_score_bounds_and_weighted_final() -> None:
    dims = {
        "recency": 1.0,
        "persona_fit": 1.0,
        "importance": 1.0,
        "novelty": 1.0,
        "source_quality": 1.0,
        "evidence_quality": 1.0,
    }
    final = compute_final_score(dims, DEFAULT_WEIGHTS)
    assert final == pytest.approx(1.0)
    assert 0.0 <= final <= 1.0

    dims2 = {
        "recency": 0.90,
        "persona_fit": 0.80,
        "importance": 0.70,
        "novelty": 0.60,
        "source_quality": 0.50,
        "evidence_quality": 0.40,
    }
    expected = (
        0.90 * 0.20
        + 0.80 * 0.25
        + 0.70 * 0.20
        + 0.60 * 0.15
        + 0.50 * 0.10
        + 0.40 * 0.10
    )
    assert compute_final_score(dims2, DEFAULT_WEIGHTS) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("hours", "expected"),
    [
        (1, 1.00),
        (12, 0.90),
        (48, 0.75),
        (120, 0.55),
        (240, 0.30),
        (400, 0.10),
    ],
)
def test_recency_buckets(hours: int, expected: float) -> None:
    now = _now()
    ref = now - timedelta(hours=hours)
    assert score_recency(ref, now) == expected


def test_missing_timestamp_and_future_timestamp() -> None:
    now = _now()
    assert score_recency(None, now) == 0.10
    assert score_recency(now + timedelta(hours=2), now) == 1.0


def test_persona_fit_relevant_vs_unrelated() -> None:
    relevant = _candidate()
    unrelated = _candidate(
        title="Celebrity fashion trends",
        angle="Pop culture",
        why_now="Awards season",
        relevance_to_persona="Unrelated",
        source_urls=["https://example.com/fashion"],
    )
    assert score_persona_fit(relevant, persona_domain="AI Security") > 0.7
    assert score_persona_fit(unrelated, research_text="fashion awards", persona_domain="AI Security") < 0.4


def test_importance_security_vs_generic() -> None:
    vuln = _candidate(
        title="Critical CVE exploit bypasses LLM guardrails",
        why_now="Active exploitation risk",
    )
    generic = _candidate(
        title="AI product roadmap update",
        angle="Product news",
        why_now="Release notes",
        relevance_to_persona="Tangential",
    )
    assert score_importance(vuln) > score_importance(generic)


def test_novelty_history_behavior() -> None:
    assert score_novelty("Brand new topic", []) == 1.0
    identical = score_novelty(
        "Prompt injection bypass in production agents",
        ["Prompt injection bypass in production agents"],
    )
    assert identical == pytest.approx(0.0)
    related = score_novelty(
        "Prompt injection defenses for agents",
        ["Prompt injection bypass in production agents"],
    )
    assert 0.0 < related < 1.0


def test_source_quality_tiers() -> None:
    high = score_source_quality(
        ["https://www.cisa.gov/advisory"],
        tier1=("cisa.gov",),
        tier2=("schneier.com",),
        tier3=(),
    )
    unknown = score_source_quality(
        ["https://random-blog.example/post"],
        tier1=("cisa.gov",),
        tier2=("schneier.com",),
        tier3=(),
    )
    assert high == 1.0
    assert unknown == 0.40


def test_evidence_quality_levels() -> None:
    no_source = _candidate(source_urls=[], angle=" ", why_now=" ")
    assert score_evidence_quality(no_source, research_by_url={}) == 0.0

    url = "https://arxiv.org/abs/2608.1"
    # Empty angle/why_now to isolate base evidence ladder.
    one = _candidate(source_urls=[url], angle=" ", why_now=" ")
    item = _item(url, summary="", published_at=None)
    assert score_evidence_quality(one, research_by_url={url: item}) == 0.50

    item2 = _item(url, summary="Summary text", published_at=None)
    assert score_evidence_quality(one, research_by_url={url: item2}) == 0.65

    item3 = _item(url, summary="Summary text", published_at=_now())
    scored = score_evidence_quality(one, research_by_url={url: item3})
    assert scored >= 0.75

    multi = _candidate(source_urls=[url, "https://cisa.gov/a"], angle=" ", why_now=" ")
    research = {
        url: item3,
        "https://cisa.gov/a": _item("https://cisa.gov/a", summary="x", published_at=_now()),
    }
    assert score_evidence_quality(multi, research_by_url=research) >= 0.85


def test_deterministic_scoring_same_inputs() -> None:
    service = TopicScoringService()
    candidate = _candidate()
    ctx = ScoringContext(
        persona_name="Kestrel",
        persona_domain="AI Security",
        now=_now(),
        research_items=[_item(published_at=_now() - timedelta(hours=2))],
        previous_topics=[],
        weights=DEFAULT_WEIGHTS,
        top_k=3,
        tier1_domains=("arxiv.org",),
        tier2_domains=(),
        tier3_domains=(),
    )
    a = service.score_candidate(candidate, ctx, topic_id="t1")
    b = service.score_candidate(candidate, ctx, topic_id="t1")
    assert a.model_dump() == b.model_dump()


def test_ranking_and_top_k_and_rejection_metadata() -> None:
    service = TopicScoringService()
    now = _now()
    research = [
        _item("https://arxiv.org/abs/1", published_at=now - timedelta(hours=1)),
        _item("https://example.com/x", published_at=now - timedelta(days=20)),
    ]
    candidates = [
        (
            "t1",
            _candidate(
                title="Critical prompt injection vulnerability CVE advisory",
                source_urls=["https://arxiv.org/abs/1"],
            ),
        ),
        (
            "t2",
            _candidate(
                title="Celebrity gossip about AI",
                angle="Entertainment",
                why_now="Viral clip",
                relevance_to_persona="None",
                source_urls=["https://example.com/x"],
                confidence=0.1,
            ),
        ),
        (
            "t3",
            _candidate(
                title="LLM jailbreak security monitoring guidance",
                source_urls=["https://arxiv.org/abs/1"],
            ),
        ),
        (
            "t4",
            _candidate(
                title="Generic AI product launch notes",
                angle="Product",
                why_now="Launch day",
                relevance_to_persona="Weak",
                source_urls=["https://example.com/x"],
            ),
        ),
    ]
    ctx = ScoringContext(
        persona_name="Kestrel",
        persona_domain="AI Security",
        now=now,
        research_items=research,
        previous_topics=[HistoricalTopic("old", "Unrelated gardening tips")],
        weights=DEFAULT_WEIGHTS,
        top_k=3,
        tier1_domains=("arxiv.org",),
        tier2_domains=(),
        tier3_domains=(),
    )
    ranked = service.rank_candidates(candidates, ctx)
    assert [r.rank for r in ranked] == [1, 2, 3, 4]
    assert ranked[0].scores.final_score >= ranked[1].scores.final_score
    selected = [r for r in ranked if r.decision == "selected"]
    rejected = [r for r in ranked if r.decision == "rejected"]
    assert len(selected) == 3
    assert len(rejected) == 1
    assert rejected[0].decision == "rejected"
    assert rejected[0].rank == 4


def test_tie_breaking_is_deterministic() -> None:
    service = TopicScoringService()
    now = _now()
    # Force identical titles/content except ids for stable ordering.
    c1 = _candidate(title="Same topic title alpha")
    c2 = _candidate(title="Same topic title alpha")
    ctx = ScoringContext(
        persona_name="Kestrel",
        persona_domain="AI Security",
        now=now,
        research_items=[_item(published_at=now)],
        weights=DEFAULT_WEIGHTS,
        top_k=1,
        tier1_domains=("arxiv.org",),
    )
    first = service.rank_candidates([("b-id", c2), ("a-id", c1)], ctx)
    second = service.rank_candidates([("b-id", c2), ("a-id", c1)], ctx)
    assert [x.topic_id for x in first] == [x.topic_id for x in second]
