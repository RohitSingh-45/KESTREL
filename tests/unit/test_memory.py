"""Unit tests for Module 7 persistent memory and belief updates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.db.repositories.agents import AgentRepository
from app.db.session import get_session_factory
from app.orchestration.locks import utc_now
from app.services.llm.base import CandidateTopic
from app.services.memory.models import (
    Belief,
    MemoryCandidate,
    MemoryExtractionResponse,
    MemoryKindEnum,
    MemoryRecord,
    MemoryValue,
    normalize_memory_key,
)
from app.services.memory.retrieval import (
    MemoryRetrievalService,
    compute_salience,
    recency_factor,
)
from app.services.memory.store import MemoryStore
from app.services.memory.update import (
    MemoryUpdateService,
    apply_evidence_update,
    ground_memory_urls,
)
from app.services.research.base import normalize_url
from app.services.scoring import ScoredCandidate, TopicScores


def _now() -> datetime:
    return datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


def test_memory_model_validation() -> None:
    belief = Belief(
        subject="prompt injection",
        predicate="is a significant security risk for",
        object="AI agents",
        stance="concerned",
        confidence=0.84,
        supporting_source_urls=["https://example.com/a"],
        evidence_count=3,
    )
    assert belief.confidence == 0.84
    value = MemoryValue(
        kind=MemoryKindEnum.BELIEF,
        text="Prompt injection remains a major AI-agent concern.",
        subject=belief.subject,
        predicate=belief.predicate,
        object=belief.object,
        confidence=belief.confidence,
        supporting_source_urls=belief.supporting_source_urls,
        evidence_count=belief.evidence_count,
    )
    assert value.kind == MemoryKindEnum.BELIEF


def test_memory_key_normalization() -> None:
    assert normalize_memory_key("Prompt Injection") == "prompt injection"
    assert normalize_memory_key("prompt-injection") == "prompt injection"
    assert normalize_memory_key("prompt_injection") == "prompt injection"
    assert normalize_memory_key("  Prompt   Injection!! ") == "prompt injection"


def test_memory_deduplication_keys_align() -> None:
    keys = [
        normalize_memory_key("Prompt Injection"),
        normalize_memory_key("prompt-injection"),
        normalize_memory_key("prompt_injection"),
    ]
    assert len(set(keys)) == 1


def test_confidence_bounds() -> None:
    belief = Belief(
        subject="a",
        predicate="b",
        object="c",
        confidence=1.8,
    )
    assert belief.confidence == 1.0
    value = MemoryValue(kind=MemoryKindEnum.NOTE, text="x", confidence=-2)
    assert value.confidence == 0.0


def test_salience_calculation() -> None:
    now = _now()
    high = compute_salience(
        confidence=0.9,
        evidence_count=5,
        source_count=4,
        updated_at=now,
        now=now,
    )
    low = compute_salience(
        confidence=0.2,
        evidence_count=0,
        source_count=0,
        updated_at=now - timedelta(days=60),
        now=now,
    )
    assert 0.0 <= low < high <= 1.0


def test_recency_handling() -> None:
    now = _now()
    assert recency_factor(now, now=now) == 1.0
    assert recency_factor(now - timedelta(days=40), now=now) < recency_factor(
        now - timedelta(hours=2), now=now
    )


def test_supporting_evidence_update() -> None:
    now = _now()
    value = MemoryValue(
        kind=MemoryKindEnum.BELIEF,
        text="Prompt injection risk",
        confidence=0.70,
        supporting_source_urls=["https://example.com/old"],
        evidence_count=1,
        history=[],
    )
    updated = apply_evidence_update(
        value,
        relation="supporting",
        new_confidence=0.9,
        source_urls=["https://example.com/new"],
        now=now,
        candidate_text="Additional supporting evidence",
    )
    assert updated.confidence > value.confidence
    assert "https://example.com/new" in updated.supporting_source_urls
    assert updated.evidence_count >= 2
    assert updated.history[-1]["event"] == "supporting"
    assert updated.history[-1]["old_confidence"] == 0.70


def test_contradicting_evidence_update() -> None:
    now = _now()
    value = MemoryValue(
        kind=MemoryKindEnum.BELIEF,
        text="Prompt injection risk",
        confidence=0.82,
        supporting_source_urls=["https://example.com/old"],
        evidence_count=2,
        history=[{"at": now.isoformat(), "event": "created", "old_confidence": None}],
    )
    updated = apply_evidence_update(
        value,
        relation="contradicting",
        new_confidence=0.8,
        source_urls=["https://example.com/contra"],
        now=now,
        candidate_text="Contradicting evidence arrived",
    )
    assert updated.confidence < 0.82
    assert "https://example.com/contra" in updated.contradicting_source_urls
    assert len(updated.history) >= 2
    assert updated.supporting_source_urls == ["https://example.com/old"]


def test_memory_source_grounding_and_unknown_rejection() -> None:
    allowed_key = normalize_url("https://arxiv.org/abs/1") or "https://arxiv.org/abs/1"
    grounded = ground_memory_urls(
        ["https://arxiv.org/abs/1", "https://invented.example/x"],
        {allowed_key},
        {allowed_key: "https://arxiv.org/abs/1"},
    )
    assert grounded == ["https://arxiv.org/abs/1"]


def test_malformed_memory_extraction_rejected() -> None:
    with pytest.raises(ValidationError):
        MemoryExtractionResponse.model_validate(
            {
                "memories": [
                    {
                        "kind": "not-a-kind",
                        "key": "x",
                        "value": "y",
                        "confidence": 0.5,
                    }
                ]
            }
        )


def test_memory_retrieval_relevance_and_limit() -> None:
    now = _now()
    memories = []
    for i, key in enumerate(
        [
            "prompt injection",
            "ai agents",
            "fashion trends",
            "jailbreak defenses",
            "cooking recipes",
        ]
    ):
        memories.append(
            MemoryRecord(
                id=f"m{i}",
                agent_id="a1",
                kind=MemoryKindEnum.THEME,
                key=key,
                value=MemoryValue(
                    kind=MemoryKindEnum.THEME,
                    text=f"Memory about {key}",
                    confidence=0.8,
                    evidence_count=2,
                ),
                salience=0.7,
                confidence=0.8,
                created_at=now - timedelta(days=i),
                updated_at=now - timedelta(days=i),
            )
        )
    candidate = CandidateTopic.model_validate(
        {
            "title": "Prompt injection attacks against autonomous AI agents",
            "angle": "Agent security",
            "why_now": "Fresh advisory",
            "relevance_to_persona": "Core AI Security",
            "source_urls": ["https://example.com/a"],
            "confidence": 0.9,
        }
    )
    scored = ScoredCandidate(
        topic_id="t1",
        title=candidate.title,
        candidate=candidate,
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
    selected = MemoryRetrievalService().retrieve_for_topic(
        memories,
        scored=scored,
        max_items=2,
        now=now,
    )
    assert len(selected) <= 2
    keys = {m.key for m in selected}
    assert "prompt injection" in keys or "ai agents" in keys
    assert "cooking recipes" not in keys


@pytest.mark.asyncio
async def test_memory_repository_create_update_upsert(engine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id="mem-repo-agent",
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        store = MemoryStore(session)
        created = await store.create(
            agent_id="mem-repo-agent",
            kind=MemoryKindEnum.THEME,
            key="prompt injection",
            value=MemoryValue(
                kind=MemoryKindEnum.THEME,
                text="Initial theme memory",
                confidence=0.6,
                evidence_count=1,
            ),
            salience=0.5,
            now=now,
        )
        assert created.id
        updated = await store.update(
            created.id,
            value=MemoryValue(
                kind=MemoryKindEnum.THEME,
                text="Updated theme memory",
                confidence=0.7,
                evidence_count=2,
            ),
            salience=0.6,
            now=now,
        )
        assert updated is not None
        assert updated.value.text == "Updated theme memory"

        upserted, was_created = await store.upsert(
            agent_id="mem-repo-agent",
            kind=MemoryKindEnum.THEME,
            key="prompt injection",
            value=MemoryValue(
                kind=MemoryKindEnum.THEME,
                text="Upserted theme memory",
                confidence=0.75,
                evidence_count=3,
            ),
            salience=0.7,
            now=now,
        )
        assert was_created is False
        assert upserted.value.text == "Upserted theme memory"
        assert upserted.id == created.id

        second, created_new = await store.upsert(
            agent_id="mem-repo-agent",
            kind=MemoryKindEnum.ENTITY,
            key="ai agents",
            value=MemoryValue(
                kind=MemoryKindEnum.ENTITY,
                text="AI agents entity",
                confidence=0.7,
            ),
            salience=0.55,
            now=now,
        )
        assert created_new is True
        assert second.key == "ai agents"
        await session.commit()


@pytest.mark.asyncio
async def test_llm_unavailable_memory_extraction() -> None:
    class _Down:
        available = False

        async def chat(self, **_kwargs: object) -> str:
            raise RuntimeError("down")

        async def structured_output(self, **_kwargs: object) -> object:
            raise RuntimeError("down")

    service = MemoryUpdateService(_Down())  # type: ignore[arg-type]
    # No session needed; extract short-circuits on unavailable.
    result = await service.extract_and_upsert(
        store=None,  # type: ignore[arg-type]
        agent_id="a",
        persona_name="Kestrel",
        persona_domain="AI Security",
        debated=[],
        research_items=[],
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "no_debated_topics"

    # With debated but unavailable LLM:
    from app.services.debate import DebateDecisionEnum, DebateResult

    scored = ScoredCandidate(
        topic_id="t1",
        title="Prompt injection",
        candidate=CandidateTopic.model_validate(
            {
                "title": "Prompt injection",
                "angle": "risk",
                "why_now": "now",
                "relevance_to_persona": "core",
                "source_urls": ["https://example.com/a"],
                "confidence": 0.9,
            }
        ),
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
    debate = DebateResult(
        topic_id="t1",
        for_argument="for",
        against_argument="against with enough substance for critique depth",
        evidence_points=[],
        weaknesses=[],
        decision=DebateDecisionEnum.PROCEED,
        confidence=0.8,
        source_urls=["https://example.com/a"],
    )
    result = await service.extract_and_upsert(
        store=None,  # type: ignore[arg-type]
        agent_id="a",
        persona_name="Kestrel",
        persona_domain="AI Security",
        debated=[(scored, debate)],
        research_items=[],
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "llm_unavailable"
    assert service.extraction_call_count == 0


def test_memory_candidate_schema() -> None:
    candidate = MemoryCandidate.model_validate(
        {
            "kind": "stance",
            "key": "prompt_injection_defense",
            "value": "Defensive controls should be prioritized",
            "stance": "defensive_controls_should_be_prioritized",
            "confidence": 0.77,
            "relation": "supporting",
            "source_urls": ["https://example.com/a"],
        }
    )
    assert candidate.kind == MemoryKindEnum.STANCE
