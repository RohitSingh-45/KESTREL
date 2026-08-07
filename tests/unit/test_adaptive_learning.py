"""Unit tests for Module 13 adaptive learning."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db.repositories.agents import AgentRepository
from app.db.session import get_session_factory
from app.orchestration.locks import utc_now
from app.services.adaptive_learning import (
    AdaptiveLearningService,
    AdaptiveSignalStore,
    TopicCoverageAnalyzer,
    classify_angle,
    decay_influence,
    normalize_concept,
)
from app.services.adaptive_learning.models import AdaptiveSignal
from app.services.llm.base import CandidateTopic


def _post(text: str, urls: list[str], *, days_ago: int = 0) -> SimpleNamespace:
    import json

    return SimpleNamespace(
        topic=text,
        text=text,
        sources_json=json.dumps(urls),
        created_at=utc_now() - timedelta(days=days_ago),
    )


def test_normalize_concept_and_angle() -> None:
    assert normalize_concept("Critical prompt injection in AI agents") == "prompt_injection"
    assert classify_angle("Enterprise risk from agent exploits") in {
        "enterprise_risk",
        "attack",
        "vulnerability",
    }


def test_topic_coverage_repeated_and_undercovered() -> None:
    analyzer = TopicCoverageAnalyzer(lookback_posts=20)
    posts = [
        _post("Prompt injection attacks against agents", ["https://arxiv.org/a"]),
        _post("Prompt injection bypass techniques", ["https://arxiv.org/b"]),
        _post("Prompt injection defenses for agents", ["https://arxiv.org/c"]),
        _post("Prompt injection monitoring guide", ["https://arxiv.org/d"]),
    ]
    result = analyzer.analyze_posts(posts)
    assert result["coverage"]["prompt_injection"]["count"] >= 3
    assert "prompt_injection" in result["overused_topics"]
    assert "privacy" in result["undercovered_topics"]


def test_angle_repetition_detected() -> None:
    analyzer = TopicCoverageAnalyzer(lookback_posts=10)
    posts = [
        _post("Defense controls for agents", ["https://example.com/1"]),
        _post("Defense hardening for LLMs", ["https://example.com/2"]),
        _post("Defense playbooks for AI", ["https://example.com/3"]),
    ]
    result = analyzer.analyze_posts(posts)
    assert result["angles"].get("defense", 0) >= 0.5
    assert "defense" in result["overused_angles"]


def test_source_dependency_high() -> None:
    analyzer = TopicCoverageAnalyzer(lookback_posts=10)
    posts = [
        _post(f"Prompt injection note {i}", ["https://arxiv.org/abs/" + str(i)])
        for i in range(8)
    ] + [
        _post("Other", ["https://www.cisa.gov/x"]),
        _post("Other2", ["https://github.com/y"]),
    ]
    result = analyzer.analyze_posts(posts)
    assert result["domains"]["arxiv.org"]["dependency"] == "high"
    assert result["domains"]["arxiv.org"]["recent_count"] == 8


def test_decay_influence_formula() -> None:
    now = utc_now()
    assert decay_influence(1.0, last_observed_at=now, now=now, decay_days=30) == 1.0
    old = now - timedelta(days=15)
    mid = decay_influence(1.0, last_observed_at=old, now=now, decay_days=30)
    assert 0.4 < mid < 0.6
    older = now - timedelta(days=30)
    assert decay_influence(1.0, last_observed_at=older, now=now, decay_days=30) == 0.0


def test_adaptive_context_bounds() -> None:
    service = AdaptiveLearningService(max_context_items=3, lookback_posts=50)
    analysis = {
        "coverage": {f"topic_{i}": {"count": 1, "recent_frequency": 0.1} for i in range(10)},
        "overused_topics": [f"t{i}" for i in range(10)],
        "undercovered_topics": [f"u{i}" for i in range(10)],
        "angles": {f"a{i}": 0.1 for i in range(10)},
        "overused_angles": [f"a{i}" for i in range(10)],
        "domains": {
            f"d{i}.com": {"recent_count": 2, "share": 0.4, "dependency": "moderate"}
            for i in range(10)
        },
    }
    ctx = service._build_context(
        analysis=analysis,
        signals=[
            AdaptiveSignal(
                signal_type="rejection_pattern",
                key=f"r{i}",
                value={"count": i},
                confidence=0.5,
                evidence_count=i,
            )
            for i in range(10)
        ],
        intelligence_by_topic={},
        selected_candidates=[],
        now=utc_now(),
    )
    assert len(ctx.recent_topic_patterns) <= 3
    assert len(ctx.overused_topics) <= 3
    assert len(ctx.source_dependencies) <= 3
    assert len(ctx.rejection_patterns) <= 3
    assert len(ctx.relevant_adaptive_signals) <= 3


def test_stale_and_revived_story_status() -> None:
    service = AdaptiveLearningService(stale_days=30)
    now = utc_now()
    assert (
        service._story_status(
            created_at=now - timedelta(days=40),
            story_type="new_story",
            now=now,
        )
        == "stale"
    )
    assert (
        service._story_status(
            created_at=now - timedelta(days=20),
            story_type="new_story",
            now=now,
        )
        == "aging"
    )
    assert (
        service._story_status(
            created_at=now - timedelta(days=20),
            story_type="continuing_story",
            now=now,
        )
        == "revived"
    )


def test_config_validation_adaptive() -> None:
    with pytest.raises(ValidationError):
        Settings(ADAPTIVE_LOOKBACK_POSTS=0)
    with pytest.raises(ValidationError):
        Settings(ADAPTIVE_STALE_DAYS=0)
    with pytest.raises(ValidationError):
        Settings(ADAPTIVE_DECAY_DAYS=0)
    with pytest.raises(ValidationError):
        Settings(ADAPTIVE_MAX_CONTEXT_ITEMS=0)
    ok = Settings(ADAPTIVE_MAX_SIGNALS=5, ADAPTIVE_MAX_CONTEXT_ITEMS=5)
    assert ok.adaptive_max_signals == 5
    assert ok.adaptive_learning_enabled is True


def test_decay_missing_timestamp_keeps_confidence() -> None:
    now = utc_now()
    assert (
        decay_influence(0.75, last_observed_at=None, now=now, decay_days=30) == 0.75
    )


@pytest.mark.asyncio
async def test_signal_persist_and_agent_isolation(engine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        for agent_id in ("adapt-a", "adapt-b"):
            await AgentRepository(session).create_agent(
                agent_id=agent_id,
                name="Kestrel",
                domain="AI Security",
                status="running",
                created_at=now,
                updated_at=now,
            )
        await session.commit()

    async with factory() as session:
        store = AdaptiveSignalStore(session)
        await store.upsert_signal(
            "adapt-a",
            AdaptiveSignal(
                signal_type="topic_pattern",
                key="prompt_injection",
                value={"count": 4},
                confidence=0.8,
                evidence_count=4,
            ),
        )
        await session.commit()

    async with factory() as session:
        store = AdaptiveSignalStore(session)
        a_signals = await store.list_signals("adapt-a")
        b_signals = await store.list_signals("adapt-b")
        assert a_signals
        assert all(s.key == "prompt_injection" for s in a_signals)
        assert b_signals == []


def test_topic_guidance_for_overused_concept() -> None:
    service = AdaptiveLearningService(max_context_items=10)
    analysis = {
        "coverage": {
            "prompt_injection": {
                "count": 5,
                "recent_frequency": 0.7,
                "last_seen_at": utc_now().isoformat(),
            }
        },
        "overused_topics": ["prompt_injection"],
        "undercovered_topics": ["privacy"],
        "angles": {"defense": 0.8},
        "overused_angles": ["defense"],
        "domains": {
            "arxiv.org": {"recent_count": 8, "share": 0.8, "dependency": "high"}
        },
    }
    ctx = service._build_context(
        analysis=analysis,
        signals=[],
        intelligence_by_topic={},
        selected_candidates=[
            CandidateTopic.model_validate(
                {
                    "title": "New prompt injection bypass",
                    "angle": "Attack",
                    "why_now": "Fresh disclosure",
                    "relevance_to_persona": "AI Security",
                    "source_urls": ["https://arxiv.org/abs/x"],
                    "confidence": 0.9,
                }
            )
        ],
        now=utc_now(),
    )
    guidance = next(iter(ctx.candidate_guidance.values()))
    assert guidance["coverage"] == "high"
    assert guidance["novelty_need"] == "high"
    assert guidance["source_diversity_need"] == "high"


@pytest.mark.asyncio
async def test_adaptive_disabled_skips() -> None:
    service = AdaptiveLearningService(enabled=False)
    ctx = await service.run(
        session=None,  # type: ignore[arg-type]
        agent_id="x",
        posts=[],
        topics=[],
    )
    assert ctx.status == "skipped"
    assert ctx.reason == "disabled"


@pytest.mark.asyncio
async def test_adaptive_service_zero_llm_calls(engine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id="adapt-llm0",
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        await session.commit()

    async with factory() as session:
        service = AdaptiveLearningService()
        posts = [
            _post("Prompt injection research finding", ["https://arxiv.org/abs/1"]),
            _post("Prompt injection defense update", ["https://arxiv.org/abs/2"]),
        ]
        ctx = await service.run(
            session,
            agent_id="adapt-llm0",
            posts=posts,
            topics=[],
            selected_candidates=[
                CandidateTopic.model_validate(
                    {
                        "title": "Prompt injection bypass",
                        "angle": "Attack",
                        "why_now": "Fresh",
                        "relevance_to_persona": "AI Security",
                        "source_urls": ["https://arxiv.org/abs/3"],
                        "confidence": 0.9,
                    }
                )
            ],
        )
        await session.commit()
        assert ctx.status == "success"
        assert service.llm_calls == 0
        assert ctx.candidate_guidance
