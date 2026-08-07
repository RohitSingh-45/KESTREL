"""Unit tests for Module 14 post evaluation."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db.repositories.agents import AgentRepository
from app.db.session import get_session_factory
from app.orchestration.locks import utc_now
from app.services.adaptive_learning import AdaptiveLearningService, AdaptiveSignalStore
from app.services.adaptive_learning.models import AdaptiveSignal
from app.services.evaluation import (
    EVAL_WEIGHTS,
    EvaluationLLMResponse,
    PostEvaluation,
    PostEvaluator,
    QualityFeedback,
    build_quality_feedback,
    classify_quality,
    merge_feedback_history,
    weighted_overall,
)
from app.services.evaluation.models import DimensionScores
from app.services.evaluation.rubric import (
    score_evidence_quality,
    score_novelty,
    score_rationale_quality,
    score_reasoning_quality,
    score_relevance,
)
from app.services.evaluation.store import EvaluationStore
from app.services.llm.base import UnavailableLLMClient


def test_evaluation_model_validation() -> None:
    ev = PostEvaluation(
        post_id="p1",
        agent_id="a1",
        overall_score=1.5,
        evidence_quality=-1,
        relevance_score=0.9,
    )
    assert ev.overall_score == 1.0
    assert ev.evidence_quality == 0.0
    assert abs(sum(EVAL_WEIGHTS.values()) - 1.0) < 1e-9


def test_deterministic_evidence_scoring() -> None:
    score, strengths, weaknesses = score_evidence_quality(
        post_urls=["https://arxiv.org/abs/1", "https://evil.example/fake"],
        grounded_urls={"https://arxiv.org/abs/1"},
        research_urls={"https://arxiv.org/abs/1"},
    )
    assert 0.0 <= score <= 1.0
    assert "ungrounded_source_urls" in weaknesses
    good, s2, w2 = score_evidence_quality(
        post_urls=["https://arxiv.org/abs/1"],
        grounded_urls={"https://arxiv.org/abs/1"},
        research_urls={"https://arxiv.org/abs/1"},
    )
    assert good > score
    assert "sources_grounded" in s2


def test_persona_relevance_scoring() -> None:
    high, strengths, _ = score_relevance(
        text="Prompt injection against AI agents remains a security concern.",
        topic="Agent prompt injection",
        persona_domain="AI Security",
    )
    low, _, weaknesses = score_relevance(
        text="Celebrity fashion week smartphone review recipe tips.",
        topic="Fashion week",
        persona_domain="AI Security",
    )
    assert high > low
    assert "ai_security_relevance" in strengths
    assert "domain_drift" in weaknesses


def test_novelty_scoring() -> None:
    prev = [
        SimpleNamespace(
            topic="Prompt injection attacks against agents",
            text="Prompt injection attacks against agents detailed note.",
        )
    ]
    low, _, weaknesses = score_novelty(
        text="Prompt injection attacks against agents detailed note.",
        topic="Prompt injection attacks against agents",
        previous_posts=prev,
    )
    high, strengths, _ = score_novelty(
        text="Novel supply-chain poisoning in agent toolchains.",
        topic="Model supply-chain poisoning for agents",
        previous_posts=prev,
    )
    assert high > low
    assert weaknesses
    assert "sufficient_novelty" in strengths or high >= 0.7


def test_reasoning_quality_scoring() -> None:
    good, strengths, _ = score_reasoning_quality(
        text="Agents face prompt injection risk.",
        rationale=(
            "Selected for AI Security relevance and stronger evidence than "
            "lower-ranked alternatives."
        ),
    )
    bad, _, weaknesses = score_reasoning_quality(
        text="x",
        rationale="",
    )
    assert good > bad
    assert "selection_explained" in strengths
    assert "missing_rationale" in weaknesses


def test_source_diversity_and_consistency_reuse() -> None:
    from app.services.evaluation.rubric import (
        reuse_consistency_score,
        reuse_source_diversity_score,
    )

    assert reuse_consistency_score({"decision": "approve", "overall_score": 0.91}) == 0.91
    assert reuse_source_diversity_score({"source_diversity_score": 0.62}) == 0.62


def test_weighted_overall_and_classification() -> None:
    dims = DimensionScores(
        evidence_quality=0.9,
        relevance_score=0.95,
        novelty_score=0.8,
        reasoning_quality=0.85,
        source_diversity_score=0.7,
        consistency_score=0.9,
        rationale_quality=0.85,
    )
    overall = weighted_overall(dims)
    assert 0.8 <= overall <= 1.0
    assert classify_quality(0.90) == "excellent"
    assert classify_quality(0.75) == "good"
    assert classify_quality(0.60) == "acceptable"
    assert classify_quality(0.45) == "weak"
    assert classify_quality(0.20) == "failed"


def test_excellent_weak_failed_classification() -> None:
    assert classify_quality(0.85) == "excellent"
    assert classify_quality(0.40) == "weak"
    assert classify_quality(0.399) == "failed"


def test_rationale_quality_scoring() -> None:
    score, strengths, _ = score_rationale_quality(
        rationale=(
            "Selected because evidence supports an AI Security impact for agents."
        ),
        text="Prompt injection risks for autonomous agents.",
    )
    assert score >= 0.5
    assert strengths


def test_config_validation_evaluation_thresholds() -> None:
    with pytest.raises(ValidationError):
        Settings(
            EVALUATION_EXCELLENT_THRESHOLD=0.5,
            EVALUATION_GOOD_THRESHOLD=0.8,
        )
    ok = Settings(EVALUATION_ENABLED=True, EVALUATION_WEAK_THRESHOLD=0.4)
    assert ok.evaluation_enabled is True


def test_feedback_generation() -> None:
    evaluation = PostEvaluation(
        post_id="p1",
        agent_id="a1",
        overall_score=0.5,
        quality_class="weak",
        evidence_quality=0.4,
        novelty_score=0.5,
        source_diversity_score=0.4,
        reasoning_quality=0.9,
        relevance_score=0.9,
        rationale_quality=0.85,
        consistency_score=0.9,
    )
    feedback = build_quality_feedback(evaluation)
    assert feedback.actions["evidence_discipline"] == "improve"
    assert feedback.actions["novelty"] == "improve"
    assert feedback.actions["reasoning_quality"] == "maintain"


def test_feedback_decay() -> None:
    now = utc_now()
    recent = QualityFeedback(
        actions={"novelty": "improve"},
        confidence=1.0,
        last_observed_at=now,
    )
    old = QualityFeedback(
        actions={"novelty": "maintain"},
        confidence=1.0,
        last_observed_at=now - timedelta(days=40),
    )
    merged = merge_feedback_history(
        [recent, old], now=now, decay_days=30, max_items=10
    )
    assert merged["novelty"] == "improve"


@pytest.mark.asyncio
async def test_malformed_llm_reflection_isolated(engine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id="eval-bad-llm",
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        await session.commit()

    class _BadLLM:
        available = True

        async def chat(self, **_k: object) -> str:
            return "{}"

        async def structured_output(self, **_k: object) -> object:
            raise RuntimeError("timeout")

    async with factory() as session:
        post = SimpleNamespace(
            id="post-1",
            text=(
                "Prompt injection against AI agents remains a concrete AI Security concern."
            ),
            rationale=(
                "Selected for AI Security relevance and stronger evidence than alternatives."
            ),
            sources=["https://arxiv.org/abs/1"],
            topic="Prompt injection against AI agents",
        )
        evaluator = PostEvaluator(_BadLLM(), reflection_enabled=True)
        result = await evaluator.evaluate(
            session,
            agent_id="eval-bad-llm",
            post=post,
            grounded_urls=["https://arxiv.org/abs/1"],
            research_urls=["https://arxiv.org/abs/1"],
            previous_posts=[],
            consistency_meta={"decision": "approve", "overall_score": 0.9},
            intelligence_meta={"source_diversity_score": 0.7},
        )
        await session.commit()
        assert result.status == "success"
        assert result.reflection_status == "failed"
        assert evaluator.llm_calls == 1


@pytest.mark.asyncio
async def test_missing_api_key_reflection(engine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id="eval-nokey",
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        await session.commit()

    async with factory() as session:
        post = SimpleNamespace(
            id="post-2",
            text="Agent identity spoofing is an AI Security risk for tool-using agents.",
            rationale=(
                "Selected for AI Security relevance with grounded evidence over weaker topics."
            ),
            sources=["https://arxiv.org/abs/2"],
            topic="Agent identity spoofing",
        )
        evaluator = PostEvaluator(UnavailableLLMClient(), reflection_enabled=True)
        result = await evaluator.evaluate(
            session,
            agent_id="eval-nokey",
            post=post,
            grounded_urls=["https://arxiv.org/abs/2"],
            previous_posts=[],
            consistency_meta={"decision": "approve", "overall_score": 0.88},
            intelligence_meta={"source_diversity_score": 0.6},
        )
        await session.commit()
        assert result.status == "success"
        assert result.reflection_status == "unavailable"
        assert evaluator.llm_calls == 0


@pytest.mark.asyncio
async def test_source_grounding_rejection(engine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id="eval-ground",
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        await session.commit()

    async with factory() as session:
        post = SimpleNamespace(
            id="post-3",
            text="Prompt injection defenses for AI agents need layered controls.",
            rationale=(
                "Selected for AI Security impact and evidence quality versus alternatives."
            ),
            sources=["https://arxiv.org/abs/ok", "https://evil.example/invented"],
            topic="Prompt injection defenses",
        )
        result = await PostEvaluator(UnavailableLLMClient()).evaluate(
            session,
            agent_id="eval-ground",
            post=post,
            grounded_urls=["https://arxiv.org/abs/ok"],
            previous_posts=[],
            consistency_meta={"decision": "approve", "overall_score": 0.9},
            intelligence_meta={"source_diversity_score": 0.5},
        )
        await session.commit()
        assert "https://evil.example/invented" not in result.source_urls
        assert "https://arxiv.org/abs/ok" in result.source_urls


@pytest.mark.asyncio
async def test_evaluation_persistence_and_isolation(engine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        for agent_id in ("eval-a", "eval-b"):
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
        post = SimpleNamespace(
            id="post-a1",
            text="Prompt injection monitoring for AI agents is essential.",
            rationale=(
                "Selected for AI Security relevance with clear evidence grounding."
            ),
            sources=["https://arxiv.org/abs/a1"],
            topic="Prompt injection monitoring",
        )
        result = await PostEvaluator(UnavailableLLMClient()).evaluate(
            session,
            agent_id="eval-a",
            post=post,
            grounded_urls=["https://arxiv.org/abs/a1"],
            previous_posts=[],
            consistency_meta={"decision": "approve", "overall_score": 0.9},
            intelligence_meta={"source_diversity_score": 0.8},
        )
        await session.commit()
        assert result.status == "success"

    async with factory() as session:
        store = EvaluationStore(session)
        a = await store.list_evaluations("eval-a")
        b = await store.list_evaluations("eval-b")
        assert a
        assert b == []
        got = await store.get_evaluation("eval-a", "post-a1")
        assert got is not None
        assert got.overall_score == result.overall_score


@pytest.mark.asyncio
async def test_adaptive_learning_includes_quality_feedback(engine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id="eval-adapt",
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        await AdaptiveSignalStore(session).upsert_signal(
            "eval-adapt",
            AdaptiveSignal(
                signal_type="quality_feedback",
                key="latest",
                value={
                    "actions": {
                        "novelty": "improve",
                        "evidence_discipline": "maintain",
                    }
                },
                confidence=0.8,
                evidence_count=2,
                last_observed_at=now,
            ),
        )
        await session.commit()

    async with factory() as session:
        ctx = await AdaptiveLearningService().run(
            session,
            agent_id="eval-adapt",
            posts=[],
            topics=[],
        )
        await session.commit()
        assert ctx.quality_feedback.get("novelty") == "improve"


@pytest.mark.asyncio
async def test_reflection_llm_response_schema() -> None:
    raw = EvaluationLLMResponse.model_validate(
        {
            "strengths": ["clear AI Security framing"],
            "weaknesses": ["limited novelty"],
            "improvement_actions": ["vary angles"],
            "confidence": 1.4,
        }
    )
    assert raw.confidence == 1.0
    assert raw.strengths
