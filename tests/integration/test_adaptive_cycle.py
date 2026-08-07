"""Module 13 integration tests for adaptive learning in the generation cycle."""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.models import Topic
from app.db.repositories.agents import AgentRepository
from app.db.repositories.memory import MemoryRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.repositories.posts import PostRepository
from app.db.session import close_db, get_session_factory, init_db
from app.orchestration.locks import AgentLockManager, utc_now
from app.services.adaptive_learning import AdaptiveLearningService, AdaptiveSignalStore
from app.services.adaptive_learning.models import AdaptiveSignal
from app.services.candidates import CandidateGenerationService
from app.services.debate import DebateLLMResponse
from app.services.final_post.models import GeneratedPost
from app.services.generation_cycle import GenerationCycleService
from app.services.llm.base import CandidateTopicList
from app.services.memory.models import MemoryExtractionResponse, MemoryKindEnum, MemoryValue
from app.services.memory.store import MemoryStore
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem

STAGE_FULL = (
    "research_candidate_scoring_debate_memory_consistency_intelligence_"
    "adaptive_learning_final_generation_publishing_evaluation"
)


class _AdaptConnector:
    name = "fixture-adapt"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        now = utc_now()
        return [
            ResearchItem(
                title="Prompt injection technique against AI agents",
                summary="Researchers reported a prompt injection weakness.",
                url="https://arxiv.org/abs/adapt-1",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash="adapt-1",
            )
        ]


class _AdaptLLM:
    available = True

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        schema = kwargs.get("schema")
        url = "https://arxiv.org/abs/adapt-1"
        if schema is DebateLLMResponse:
            return DebateLLMResponse.model_validate(
                {
                    "for_argument": (
                        "Topic is timely AI Security evidence with grounded sources that "
                        "support further consideration."
                    ),
                    "against_argument": (
                        "Evidence depth may be limited and practical impact could be overstated."
                    ),
                    "evidence_points": ["Source describes an AI agent security issue."],
                    "weaknesses": ["Limited operational detail."],
                    "decision": "proceed",
                    "confidence": 0.86,
                    "source_urls": [url],
                }
            )
        if schema is MemoryExtractionResponse:
            return MemoryExtractionResponse.model_validate(
                {
                    "memories": [
                        {
                            "kind": "theme",
                            "key": "prompt injection",
                            "value": "Prompt injection remains a recurring AI-agent concern.",
                            "confidence": 0.8,
                            "relation": "supporting",
                            "source_urls": [url],
                        }
                    ]
                }
            )
        if schema is GeneratedPost:
            return GeneratedPost.model_validate(
                {
                    "text": (
                        "Fresh evidence on prompt injection against AI agents reinforces "
                        "why defensive controls remain a priority in AI Security."
                    ),
                    "rationale": (
                        "Selected for AI Security relevance and recent evidence over "
                        "lower-ranked alternatives that were less security-specific."
                    ),
                    "source_urls": [url],
                }
            )
        return CandidateTopicList.model_validate(
            {
                "candidates": [
                    {
                        "title": "Prompt injection attacks against autonomous AI agents",
                        "angle": "Agent exploitability",
                        "why_now": "Fresh disclosure",
                        "relevance_to_persona": "Core AI Security",
                        "source_urls": [url],
                        "confidence": 0.93,
                    }
                ]
            }
        )


async def _create_agent(agent_id: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id=agent_id,
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        await session.commit()


def _service(llm: _AdaptLLM | None = None) -> GenerationCycleService:
    llm = llm or _AdaptLLM()
    return GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(connectors=[_AdaptConnector()], max_items=20),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_pipeline_runs_adaptive_learning(engine: AsyncEngine) -> None:
    agent_id = "adapt-pipe-agent"
    await _create_agent(agent_id)
    assert await _service().execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].stage == STAGE_FULL
        meta = json.loads(runs[0].meta_json or "{}")
        assert meta["adaptive_learning"]["status"] == "success"
        assert meta["adaptive_learning"]["llm_calls"] == 0
        assert meta["summary"]["adaptive_learning"] == "success"


@pytest.mark.asyncio
async def test_adaptive_failure_does_not_kill_cycle(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = "adapt-fail-agent"
    await _create_agent(agent_id)

    async def boom(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("adaptive boom")

    monkeypatch.setattr(AdaptiveLearningService, "run", boom)
    result = await _service().execute(agent_id)
    assert result == "success"
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 1
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
            or "{}"
        )
        assert meta["adaptive_learning"]["status"] == "failed"


@pytest.mark.asyncio
async def test_memory_not_overwritten_by_adaptive(engine: AsyncEngine) -> None:
    """Adaptive learning may reference beliefs but must not mutate them."""
    agent_id = "adapt-mem-agent"
    await _create_agent(agent_id)
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await MemoryStore(session).create(
            agent_id=agent_id,
            kind=MemoryKindEnum.BELIEF,
            key="prompt injection defenses",
            value=MemoryValue(
                kind=MemoryKindEnum.BELIEF,
                text="Defense X reduces prompt injection risk for AI agents.",
                confidence=0.84,
                supporting_source_urls=["https://arxiv.org/abs/old"],
                evidence_count=2,
                history=[{"event": "seeded", "confidence": 0.84}],
            ),
            salience=0.8,
            now=now,
        )
        await session.commit()

        ctx = await AdaptiveLearningService().run(
            session,
            agent_id=agent_id,
            posts=[],
            topics=[],
            contradiction_meta={
                "detected": True,
                "memory_key": "prompt injection defenses",
                "type": "partial",
                "confidence": 0.7,
                "signal_count": 1,
                "evidence_urls": ["https://arxiv.org/abs/new"],
            },
        )
        await session.commit()
        assert ctx.status == "success"

        belief = await MemoryRepository(session).find_by_key(
            agent_id, kind="belief", key="prompt injection defenses"
        )
        assert belief is not None
        payload = json.loads(belief.value_json)
        assert payload["confidence"] == 0.84
        assert payload["evidence_count"] == 2
        assert "https://arxiv.org/abs/old" in payload["supporting_source_urls"]
        assert payload["history"] == [{"event": "seeded", "confidence": 0.84}]
        notes = await MemoryRepository(session).find_by_kind(agent_id, "note")
        assert any((n.key or "").startswith("adaptive") for n in notes)


@pytest.mark.asyncio
async def test_contradiction_creates_belief_pattern_signal(engine: AsyncEngine) -> None:
    agent_id = "adapt-contra-agent"
    await _create_agent(agent_id)
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await MemoryStore(session).create(
            agent_id=agent_id,
            kind=MemoryKindEnum.BELIEF,
            key="prompt injection defenses",
            value=MemoryValue(
                kind=MemoryKindEnum.BELIEF,
                text="Defense X helps.",
                confidence=0.9,
                supporting_source_urls=["https://arxiv.org/abs/old"],
                evidence_count=3,
                history=[{"event": "prior", "confidence": 0.9}],
            ),
            salience=0.9,
            now=now,
        )
        await session.commit()

        ctx = await AdaptiveLearningService().run(
            session,
            agent_id=agent_id,
            posts=[],
            topics=[],
            contradiction_meta={
                "detected": True,
                "memory_key": "prompt injection defenses",
                "type": "partial",
                "confidence": 0.81,
                "signal_count": 2,
                "evidence_urls": ["https://arxiv.org/abs/x"],
            },
        )
        await session.commit()
        assert ctx.status == "success"
        store = AdaptiveSignalStore(session)
        signals = await store.list_signals(agent_id)
        belief_signals = [s for s in signals if s.signal_type == "belief_pattern"]
        assert belief_signals
        assert belief_signals[0].value.get("status") == "contested"
        assert belief_signals[0].value.get("contradictions_seen") == 2

        belief = await MemoryRepository(session).find_by_key(
            agent_id, kind="belief", key="prompt injection defenses"
        )
        assert belief is not None
        payload = json.loads(belief.value_json)
        assert payload["confidence"] == 0.9
        assert payload["history"] == [{"event": "prior", "confidence": 0.9}]


@pytest.mark.asyncio
async def test_adaptive_signals_survive_restart(engine: AsyncEngine) -> None:
    agent_id = "adapt-restart-agent"
    await _create_agent(agent_id)
    factory = get_session_factory()
    async with factory() as session:
        await AdaptiveSignalStore(session).upsert_signal(
            agent_id,
            AdaptiveSignal(
                signal_type="source_pattern",
                key="arxiv.org",
                value={"dependency": "high", "recent_count": 8},
                confidence=0.8,
                evidence_count=8,
            ),
        )
        await session.commit()

    await close_db()
    await init_db(get_settings())
    factory = get_session_factory()
    async with factory() as session:
        signals = await AdaptiveSignalStore(session).list_signals(agent_id)
        assert any(s.key == "arxiv.org" for s in signals)


@pytest.mark.asyncio
async def test_feed_never_invokes_adaptive(
    client: AsyncClient,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    agent_id = init.json()["agentId"]
    called = {"n": 0}

    async def track(*_a: Any, **_k: Any) -> Any:
        called["n"] += 1
        raise AssertionError("adaptive must not run from feed")

    monkeypatch.setattr(AdaptiveLearningService, "run", track)
    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.status_code == 200
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_rejection_patterns_aggregated(engine: AsyncEngine) -> None:
    agent_id = "adapt-reject-agent"
    await _create_agent(agent_id)
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        for i, reason in enumerate(
            ["unsupported_claim", "persona_drift", "unsupported_claim", "repetitive_topic"]
        ):
            session.add(
                Topic(
                    id=f"rej-{i}",
                    agent_id=agent_id,
                    title=f"Rejected topic {i}",
                    status="rejected",
                    created_at=now,
                    consistency_json=json.dumps(
                        {"decision": "reject", "reasons": [reason]}
                    ),
                )
            )
        await session.commit()

        service = AdaptiveLearningService()
        result = await session.execute(select(Topic).where(Topic.agent_id == agent_id))
        topic_rows = list(result.scalars().all())
        ctx = await service.run(
            session,
            agent_id=agent_id,
            posts=[],
            topics=topic_rows,
        )
        await session.commit()
        keys = {p["key"] for p in ctx.rejection_patterns}
        assert "unsupported_claim" in keys


@pytest.mark.asyncio
async def test_multi_agent_adaptive_isolation(engine: AsyncEngine) -> None:
    await _create_agent("adapt-iso-a")
    await _create_agent("adapt-iso-b")
    await _service().execute("adapt-iso-a")
    factory = get_session_factory()
    async with factory() as session:
        a = await AdaptiveSignalStore(session).list_signals("adapt-iso-a")
        b = await AdaptiveSignalStore(session).list_signals("adapt-iso-b")
        assert a
        assert b == []


@pytest.mark.asyncio
async def test_adaptive_meta_includes_guidance_signals(engine: AsyncEngine) -> None:
    agent_id = "adapt-meta-agent"
    await _create_agent(agent_id)
    factory = get_session_factory()
    # Seed prior posts so coverage/dependency signals are non-empty.
    async with factory() as session:
        from app.services.final_post.models import GeneratedPost
        from app.services.publishing import PublishingService

        for i in range(4):
            await PublishingService(session).publish(
                agent_id=agent_id,
                generated_post=GeneratedPost.model_validate(
                    {
                        "text": f"Prompt injection defenses for AI agents note {i}.",
                        "rationale": "Seed history for adaptive coverage analysis.",
                        "source_urls": [f"https://arxiv.org/abs/seed-{i}"],
                    }
                ),
                grounded_urls=[f"https://arxiv.org/abs/seed-{i}"],
                consistency_approved=True,
                final_validation_passed=True,
            )
        await session.commit()

    assert await _service().execute(agent_id) == "success"
    async with factory() as session:
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
            or "{}"
        )
        adaptive = meta["adaptive_learning"]
        assert adaptive["status"] == "success"
        assert adaptive["llm_calls"] == 0
        assert isinstance(adaptive.get("overused_topics"), list)
        assert isinstance(adaptive.get("undercovered_topics"), list)
        signals = await AdaptiveSignalStore(session).list_signals(agent_id)
        types = {s.signal_type for s in signals}
        assert "topic_pattern" in types
        assert "source_pattern" in types


@pytest.mark.asyncio
async def test_pipeline_order_adaptive_before_final(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = "adapt-order-agent"
    await _create_agent(agent_id)
    order: list[str] = []
    original_run = AdaptiveLearningService.run

    async def track_run(self, *args: Any, **kwargs: Any) -> Any:
        order.append("adaptive")
        return await original_run(self, *args, **kwargs)

    from app.services.final_post import FinalPostGenerator

    original_generate = FinalPostGenerator.generate

    async def track_final(self, *args: Any, **kwargs: Any) -> Any:
        order.append("final")
        return await original_generate(self, *args, **kwargs)

    monkeypatch.setattr(AdaptiveLearningService, "run", track_run)
    monkeypatch.setattr(FinalPostGenerator, "generate", track_final)
    assert await _service().execute(agent_id) == "success"
    assert "adaptive" in order
    assert "final" in order
    assert order.index("adaptive") < order.index("final")
