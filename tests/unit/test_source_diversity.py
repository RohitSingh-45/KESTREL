"""Unit tests for Module 12 source diversity."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.llm.base import CandidateTopic
from app.services.source_diversity import SourceDiversityAnalyzer, extract_domain


def _candidate(urls: list[str], title: str = "Prompt injection update") -> CandidateTopic:
    return CandidateTopic.model_validate(
        {
            "title": title,
            "angle": "AI agent security",
            "why_now": "Fresh evidence",
            "relevance_to_persona": "Core AI Security",
            "source_urls": urls,
            "confidence": 0.9,
        }
    )


def test_url_normalization_same_domain() -> None:
    assert extract_domain("https://arxiv.org/abs/123") == "arxiv.org"
    assert extract_domain("https://arxiv.org/pdf/123") == "arxiv.org"
    assert extract_domain("https://www.bleepingcomputer.com/news") == "bleepingcomputer.com"


def test_repeated_same_domain_low_diversity() -> None:
    analyzer = SourceDiversityAnalyzer(lookback=10)
    recent = ["arxiv.org"] * 8
    result = analyzer.analyze_candidate(
        _candidate(["https://arxiv.org/abs/999"]),
        recent_domains=recent,
    )
    assert result.source_repetition > 0.5
    assert result.source_diversity < 0.5
    assert "arxiv.org" in result.repeated_domains


def test_multiple_domains_higher_diversity() -> None:
    analyzer = SourceDiversityAnalyzer(lookback=10)
    recent = ["arxiv.org", "cisa.gov"]
    result = analyzer.analyze_candidate(
        _candidate(
            [
                "https://www.nist.gov/a",
                "https://www.cisa.gov/b",
            ]
        ),
        recent_domains=recent,
    )
    assert result.source_domain_count == 2
    assert result.source_diversity > 0.4


def test_lookback_limit() -> None:
    analyzer = SourceDiversityAnalyzer(lookback=2)
    posts = [
        SimpleNamespace(sources_json='["https://arxiv.org/1"]'),
        SimpleNamespace(sources_json='["https://arxiv.org/2"]'),
        SimpleNamespace(sources_json='["https://example.com/3"]'),
    ]
    domains = analyzer.recent_domains_from_posts(posts)
    assert domains == ["arxiv.org", "arxiv.org"]
    assert "example.com" not in domains


def test_multi_agent_recent_domains_are_caller_scoped() -> None:
    """Analyzer only sees domains passed in — callers must scope per agent."""
    analyzer = SourceDiversityAnalyzer(lookback=10)
    agent_a = analyzer.analyze_candidate(
        _candidate(["https://arxiv.org/abs/1"]),
        recent_domains=["arxiv.org", "arxiv.org"],
    )
    agent_b = analyzer.analyze_candidate(
        _candidate(["https://arxiv.org/abs/1"]),
        recent_domains=["cisa.gov", "nist.gov"],
    )
    assert agent_a.source_repetition > agent_b.source_repetition


def test_high_quality_repeat_can_still_beat_low_quality_diverse() -> None:
    """Diversity is secondary: repeated high-quality source must not auto-lose."""
    from app.services.scoring import (
        ScoringContext,
        TopicScoringService,
        HistoricalTopic,
    )
    from app.orchestration.locks import utc_now
    from app.services.research.models import ResearchItem

    now = utc_now()
    research = [
        ResearchItem(
            title="Critical prompt injection CVE",
            summary="Exploit against AI agents.",
            url="https://arxiv.org/abs/hq",
            source_name="arXiv",
            published_at=now,
            discovered_at=now,
            content_hash="hq",
        ),
        ResearchItem(
            title="Random blog tip",
            summary="Generic tip.",
            url="https://example.com/low",
            source_name="blog",
            published_at=now,
            discovered_at=now,
            content_hash="lq",
        ),
    ]
    ctx = ScoringContext(
        persona_name="Kestrel",
        persona_domain="AI Security",
        now=now,
        research_items=research,
        previous_topics=[],
        previous_post_titles=[],
        weights={
            "recency": 0.20,
            "persona_fit": 0.25,
            "importance": 0.20,
            "novelty": 0.15,
            "source_quality": 0.10,
            "evidence_quality": 0.10,
        },
        top_k=2,
        tier1_domains=("arxiv.org",),
        tier2_domains=(),
        tier3_domains=(),
    )
    high = _candidate(
        ["https://arxiv.org/abs/hq"],
        title="Critical prompt injection vulnerability in AI agents",
    )
    low = _candidate(
        ["https://example.com/low"],
        title="Generic AI notebook productivity tips",
    )
    ranked = TopicScoringService().rank_candidates(
        [("t1", high), ("t2", low)],
        ctx,
    )
    by_id = {r.topic_id: r for r in ranked}
    assert by_id["t1"].scores.final_score > by_id["t2"].scores.final_score
    # Diversity may mark arxiv as repetitive, but Module 5 ranking still prefers it.
    analyzer = SourceDiversityAnalyzer(lookback=10)
    div_high = analyzer.analyze_candidate(high, recent_domains=["arxiv.org"] * 5)
    div_low = analyzer.analyze_candidate(low, recent_domains=["arxiv.org"] * 5)
    assert div_low.source_diversity >= div_high.source_diversity
