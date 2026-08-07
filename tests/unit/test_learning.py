"""Unit tests for Module 15 controlled learning."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db.repositories.agents import AgentRepository
from app.db.session import get_session_factory
from app.orchestration.locks import utc_now
from app.services.adaptive_learning import AdaptiveSignalStore
from app.services.adaptive_learning.models import AdaptiveSignal
from app.services.evaluation.models import PostEvaluation
from app.services.evaluation.store import EvaluationStore
from app.services.learning import (
    ALLOWED_SIGNAL_TYPES,
    ControlledLearningService,
    LearningSignal,
    LearningStore,
    classify_state,
    compute_confidence,
    validate_learning_signal,
)
from app.services.learning.analyzer import LearningAnalyzer, Observation
from app.services.learning.policy import sanitize_recommendation, validate_signal_value
from app.services.learning.strategy import aggregate_observations


def test_allowed_signal_types_constrained() -> None:
    assert "strategy" in ALLOWED_SIGNAL_TYPES
    with pytest.raises(ValidationError):
        LearningSignal(signal_type="rewrite_persona", key="x")  # type: ignore[arg-type]


def test_min_observations_prevents_active_from_one_success() -> None:
    conf = compute_confidence(
        support_count=1, failure_count=0, evidence_count=1, min_observations=3
    )
    state = classify_state(
        confidence=conf,
        support_count=1,
        failure_count=0,
        evidence_count=1,
        influence=conf,
        min_observations=3,
        min_confidence=0.65,
    )
    assert state == "emerging"
    assert conf < 0.65 or state != "active"


def test_positive_and_negative_evidence_affect_confidence() -> None:
    strong = compute_confidence(
        support_count=5, failure_count=1, evidence_count=6, min_observations=3
    )
    weak = compute_confidence(
        support_count=1, failure_count=0, evidence_count=1, min_observations=3
    )
    assert strong > weak


def test_confidence_bounded() -> None:
    conf = compute_confidence(
        support_count=100, failure_count=0, evidence_count=100, min_observations=3
    )
    assert 0.0 <= conf <= 1.0
    assert conf <= 0.92


def test_decay_and_expired_state() -> None:
    now = utc_now()
    old = LearningSignal(
        signal_type="strategy",
        key="evidence_first",
        state="active",
        confidence=0.8,
        support_count=5,
        failure_count=1,
        evidence_count=6,
        last_observed_at=now - timedelta(days=40),
        created_at=now - timedelta(days=40),
        reason="old",
        value={"recommendation": "prefer strong evidence"},
    )
    signals = aggregate_observations(
        [],
        existing=[old],
        min_observations=3,
        min_confidence=0.65,
        decay_days=30,
        now=now,
    )
    assert signals
    assert signals[0].state == "expired"
    assert signals[0].influence == 0.0


def test_policy_rejects_unsafe_values() -> None:
    assert sanitize_recommendation("change persona identity") is None
    cleaned = validate_signal_value(
        {
            "recommendation": "prefer diverse sources",
            "openai_api_key": "secret",
            "score_weights": {"novelty": 0.9},
            "persona": "Other",
        }
    )
    assert "openai_api_key" not in cleaned
    assert "score_weights" not in cleaned
    assert "persona" not in cleaned
    assert cleaned["recommendation"] == "prefer diverse sources"


def test_validate_learning_signal_blocks_bad_type() -> None:
    # Construct via model_construct to bypass type enum, then validate.
    raw = LearningSignal.model_construct(
        signal_type="strategy",
        key="",
        confidence=0.9,
        value={"bypass_consistency": True},
    )
    assert validate_learning_signal(raw) is None


def test_analyzer_emits_evidence_and_novelty_observations() -> None:
    evaluations = [
        PostEvaluation(
            post_id=f"p{i}",
            agent_id="a",
            overall_score=0.8,
            quality_class="good",
            evidence_quality=0.9,
            novelty_score=0.4,
            source_diversity_score=0.4,
            reasoning_quality=0.8,
            rationale_quality=0.8,
            relevance_score=0.9,
            consistency_score=0.9,
            feedback={"novelty": "improve", "evidence_discipline": "maintain"},
        )
        for i in range(3)
    ]
    obs = LearningAnalyzer().analyze(
        evaluations=evaluations,
        adaptive_meta={
            "overused_topics": ["prompt_injection"],
            "source_dependencies": [{"domain": "arxiv.org", "dependency": "high"}],
        },
        quality_feedback={"novelty": "improve"},
    )
    types = {o.signal_type for o in obs}
    assert "strategy" in types or "evidence_preference" in types
    assert "novelty_preference" in types
    assert "source_preference" in types
    assert "topic_preference" in types


def test_aggregate_requires_min_observations_for_active() -> None:
    observations = [
        Observation(
            signal_type="strategy",
            key="evidence_first",
            success=True,
            confidence_hint=0.9,
            reason="strong evidence helped",
            evidence_ref=f"eval:{i}",
            recommendation="prefer topics with strong multi-source evidence",
        )
        for i in range(3)
    ]
    signals = aggregate_observations(
        observations,
        min_observations=3,
        min_confidence=0.5,
    )
    assert signals
    assert signals[0].key == "evidence_first"
    assert signals[0].evidence_count >= 3
    assert signals[0].state in {"active", "emerging"}


def test_config_validation_learning() -> None:
    with pytest.raises(ValidationError):
        Settings(LEARNING_MIN_OBSERVATIONS=0)
    with pytest.raises(ValidationError):
        Settings(LEARNING_DECAY_DAYS=0)
    ok = Settings(LEARNING_MIN_CONFIDENCE=0.7, LEARNING_MAX_SIGNALS=5)
    assert ok.learning_min_confidence == 0.7


@pytest.mark.asyncio
async def test_learning_persistence_and_isolation(engine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        for agent_id in ("learn-a", "learn-b"):
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
        store = LearningStore(session)
        await store.upsert_signal(
            "learn-a",
            LearningSignal(
                signal_type="strategy",
                key="evidence_first",
                state="active",
                confidence=0.8,
                support_count=5,
                failure_count=1,
                evidence_count=6,
                reason="Posts with stronger evidence scored higher.",
                value={"recommendation": "prefer topics with strong multi-source evidence"},
            ),
        )
        await session.commit()

    async with factory() as session:
        store = LearningStore(session)
        a = await store.list_signals("learn-a")
        b = await store.list_signals("learn-b")
        assert a
        assert all(s.key == "evidence_first" for s in a)
        assert b == []


@pytest.mark.asyncio
async def test_service_zero_llm_and_context_bounds(engine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id="learn-ctx",
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        eval_store = EvaluationStore(session)
        for i in range(4):
            await eval_store.save_evaluation(
                PostEvaluation(
                    post_id=f"pe{i}",
                    agent_id="learn-ctx",
                    overall_score=0.82,
                    quality_class="good",
                    evidence_quality=0.9,
                    novelty_score=0.45,
                    source_diversity_score=0.5,
                    reasoning_quality=0.8,
                    rationale_quality=0.8,
                    relevance_score=0.9,
                    consistency_score=0.9,
                    feedback={"novelty": "improve"},
                    status="success",
                )
            )
        await AdaptiveSignalStore(session).upsert_signal(
            "learn-ctx",
            AdaptiveSignal(
                signal_type="quality_feedback",
                key="latest",
                value={"actions": {"novelty": "improve", "evidence_discipline": "maintain"}},
                confidence=0.8,
                evidence_count=3,
            ),
        )
        await session.commit()

    async with factory() as session:
        service = ControlledLearningService(
            min_observations=3,
            min_confidence=0.5,
            max_context_items=3,
        )
        ctx = await service.run(
            session,
            agent_id="learn-ctx",
            adaptive_meta={
                "overused_topics": ["prompt_injection"],
                "quality_feedback": {"novelty": "improve"},
            },
        )
        await session.commit()
        assert ctx.status == "success"
        assert service.llm_calls == 0
        assert len(ctx.recommendations) <= 3
        assert len(ctx.relevant_signals) <= 3


@pytest.mark.asyncio
async def test_observe_evaluation_updates_signals(engine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id="learn-obs",
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        await session.commit()

    async with factory() as session:
        service = ControlledLearningService(min_observations=1, min_confidence=0.4)
        evaluation = PostEvaluation(
            post_id="obs1",
            agent_id="learn-obs",
            overall_score=0.4,
            quality_class="weak",
            evidence_quality=0.3,
            novelty_score=0.3,
            source_diversity_score=0.3,
            reasoning_quality=0.5,
            rationale_quality=0.5,
            relevance_score=0.8,
            consistency_score=0.8,
            feedback={"evidence_discipline": "improve"},
            status="success",
        )
        ctx = await service.observe_evaluation(
            session,
            agent_id="learn-obs",
            evaluation=evaluation,
        )
        await session.commit()
        assert ctx.status == "success"
        signals = await LearningStore(session).list_signals("learn-obs")
        assert signals


def test_weakening_when_failures_dominate() -> None:
    state = classify_state(
        confidence=0.7,
        support_count=2,
        failure_count=5,
        evidence_count=7,
        influence=0.7,
        min_observations=3,
        min_confidence=0.65,
        previous_state="active",
    )
    assert state == "weakening"
