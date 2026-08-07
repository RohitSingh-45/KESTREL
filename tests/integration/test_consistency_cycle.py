"""Integration tests for Module 8 consistency in the generation cycle."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.repositories.agents import AgentRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.repositories.posts import PostRepository
from app.db.repositories.topics import TopicRepository
from app.db.session import close_db, get_session_factory, init_db
from app.orchestration.locks import AgentLockManager, utc_now
from app.services.candidates import CandidateGenerationService
from app.services.consistency import ConsistencyGuard
from app.services.consistency.analyzer import ConsistencyAnalyzer
from app.services.consistency.models import ConsistencyDecisionEnum, ConsistencyResult
from app.services.debate import DebateLLMResponse
from app.services.final_post.models import GeneratedPost
from app.services.generation_cycle import GenerationCycleService
from app.services.llm.base import CandidateTopic, CandidateTopicList
from app.services.memory.models import MemoryExtractionResponse, MemoryKindEnum, MemoryValue
from app.services.memory.store import MemoryStore
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem
from app.services.scoring import ScoredCandidate, TopicScores


class _ConsConnector:
    name = "fixture-consistency"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        now = utc_now()
        return [
            ResearchItem(
                title="Prompt injection against AI agents",
                summary="Critical exploit path for autonomous agents.",
                url="https://arxiv.org/abs/cons-1",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash="c1",
            ),
            ResearchItem(
                title="Jailbreak defense guidance",
                summary="Enterprise monitoring controls.",
                url="https://www.cisa.gov/cons-2",
                source_name="CISA",
                published_at=now,
                discovered_at=now,
                content_hash="c2",
            ),
        ]


def _candidates() -> CandidateTopicList:
    return CandidateTopicList.model_validate(
        {
            "candidates": [
                {
                    "title": "Prompt injection attacks against autonomous AI agents",
                    "angle": "Agent exploitability",
                    "why_now": "Fresh disclosure",
                    "relevance_to_persona": "Core AI Security",
                    "source_urls": ["https://arxiv.org/abs/cons-1"],
                    "confidence": 0.93,
                },
                {
                    "title": "Enterprise jailbreak defense guidance",
                    "angle": "Mitigations",
                    "why_now": "New guidance",
                    "relevance_to_persona": "AI Security operations",
                    "source_urls": ["https://www.cisa.gov/cons-2"],
                    "confidence": 0.88,
                },
            ]
        }
    )


def _debate(url: str) -> DebateLLMResponse:
    return DebateLLMResponse.model_validate(
        {
            "for_argument": (
                "Topic is timely AI Security evidence with grounded sources that "
                "support further consideration."
            ),
            "against_argument": (
                "Evidence depth may be limited, novelty versus prior topics is "
                "uncertain, and practical impact could be overstated."
            ),
            "evidence_points": ["Source describes an AI agent security issue."],
            "weaknesses": ["Limited operational detail."],
            "decision": "proceed",
            "confidence": 0.86,
            "source_urls": [url],
        }
    )


class _ConsistencyLLM:
    available = True

    def __init__(self) -> None:
        self.call_count = 0

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        schema = kwargs.get("schema")
        if schema is DebateLLMResponse:
            prompt = str(kwargs.get("user_prompt") or "")
            url = "https://arxiv.org/abs/cons-1"
            if "https://www.cisa.gov/cons-2" in prompt:
                url = "https://www.cisa.gov/cons-2"
            return _debate(url)
        if schema is MemoryExtractionResponse:
            return MemoryExtractionResponse.model_validate(
                {
                    "memories": [
                        {
                            "kind": "belief",
                            "key": "prompt injection",
                            "value": "Prompt injection is a significant risk for AI agents.",
                            "subject": "prompt injection",
                            "predicate": "is a significant security risk for",
                            "object": "AI agents",
                            "stance": "concerned",
                            "confidence": 0.84,
                            "relation": "supporting",
                            "source_urls": ["https://arxiv.org/abs/cons-1"],
                        }
                    ]
                }
            )
        if schema is GeneratedPost:
            return GeneratedPost.model_validate(
                {
                    "text": (
                        "Prompt injection attacks against AI agents remain a timely "
                        "AI Security issue with grounded research support."
                    ),
                    "rationale": (
                        "Selected for AI Security relevance and recent evidence over "
                        "lower-ranked alternatives that were less security-specific."
                    ),
                    "source_urls": ["https://arxiv.org/abs/cons-1"],
                }
            )
        return _candidates()


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


def _service(
    llm: object,
    *,
    consistency_guard: ConsistencyGuard | None = None,
) -> GenerationCycleService:
    return GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(connectors=[_ConsConnector()], max_items=20),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
        consistency_guard=consistency_guard,
    )


@pytest.mark.asyncio
async def test_full_cycle_records_consistency_stage(engine: AsyncEngine) -> None:
    agent_id = "cons-cycle-agent"
    await _create_agent(agent_id)
    assert await _service(_ConsistencyLLM()).execute(agent_id) == "success"

    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].stage == "research_candidate_scoring_debate_memory_consistency_intelligence_adaptive_learning_final_generation_publishing_evaluation"
        meta = json.loads(runs[0].meta_json or "{}")
        assert meta["consistency"]
        assert all(item["decision"] in {"approve", "revise", "reject"} for item in meta["consistency"])

        selected = await TopicRepository(session).list_by_agent(agent_id, status="selected")
        assert selected
        for topic in selected:
            scores = json.loads(topic.scores_json or "{}")
            debate = json.loads(topic.debate_json or "{}")
            consistency = json.loads(topic.consistency_json or "{}")
            assert "final_score" in scores
            assert "decision" in debate
            assert consistency["version"] == 1
            assert "signals" in consistency
            assert consistency["decision"] in {"approve", "revise", "reject"}
            # Module 5/6 metadata untouched conceptually (still present).
            assert scores["decision"] == "selected"

        assert meta["publication"]["status"] == "published"
        assert len(await PostRepository(session).list_by_agent_newest_first(agent_id)) == 1


@pytest.mark.asyncio
async def test_consistency_metadata_survives_restart(engine: AsyncEngine) -> None:
    agent_id = "cons-restart-agent"
    await _create_agent(agent_id)
    await _service(_ConsistencyLLM()).execute(agent_id)

    factory = get_session_factory()
    async with factory() as session:
        before = {
            t.id: t.consistency_json
            for t in await TopicRepository(session).list_by_agent(agent_id, status="selected")
        }
        assert before
        assert all(v for v in before.values())

    await close_db()
    await init_db(get_settings())
    factory = get_session_factory()
    async with factory() as session:
        after = {
            t.id: t.consistency_json
            for t in await TopicRepository(session).list_by_agent(agent_id, status="selected")
        }
        assert after == before


@pytest.mark.asyncio
async def test_memory_influences_consistency_and_scores_preserved(
    engine: AsyncEngine,
) -> None:
    agent_id = "cons-memory-agent"
    await _create_agent(agent_id)
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await MemoryStore(session).create(
            agent_id=agent_id,
            kind=MemoryKindEnum.BELIEF,
            key="prompt injection",
            value=MemoryValue(
                kind=MemoryKindEnum.BELIEF,
                text="Prompt injection remains a major AI-agent security concern",
                stance="evidence is currently mixed",
                confidence=0.75,
                supporting_source_urls=["https://arxiv.org/abs/cons-1"],
                contradicting_source_urls=[],
                evidence_count=2,
            ),
            salience=0.8,
            now=now,
        )
        await session.commit()

    await _service(_ConsistencyLLM()).execute(agent_id)
    async with factory() as session:
        topics = await TopicRepository(session).list_by_agent(agent_id, status="selected")
        for topic in topics:
            scores = topic.scores_json
            debate = topic.debate_json
            consistency = json.loads(topic.consistency_json or "{}")
            assert scores
            assert debate
            assert consistency.get("signals")
            # Re-read unchanged fields
            assert json.loads(scores)["decision"] == "selected"


@pytest.mark.asyncio
async def test_analyzer_failure_does_not_kill_cycle(engine: AsyncEngine) -> None:
    agent_id = "cons-fail-agent"
    await _create_agent(agent_id)

    class _FailAnalyzer(ConsistencyAnalyzer):
        def analyze(self, *args: object, **kwargs: object) -> ConsistencyResult:  # type: ignore[override]
            raise RuntimeError("analyzer boom")

    guard = ConsistencyGuard(analyzer=_FailAnalyzer())
    assert await _service(_ConsistencyLLM(), consistency_guard=guard).execute(agent_id) == "success"

    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].status == "success"
        topics = await TopicRepository(session).list_by_agent(agent_id, status="selected")
        for topic in topics:
            payload = json.loads(topic.consistency_json or "{}")
            assert payload["decision"] == "reject"
            assert "consistency_analysis_failed" in payload.get("reasons", [])
        assert await PostRepository(session).list_by_agent_newest_first(agent_id) == []


@pytest.mark.asyncio
async def test_consistency_feed_returns_published_post(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    agent_id = init.json()["agentId"]
    await _service(_ConsistencyLLM()).execute(agent_id)
    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert len(feed.json()["posts"]) == 1


@pytest.mark.asyncio
async def test_outside_domain_candidate_rejected_by_guard(engine: AsyncEngine) -> None:
    """Direct guard check for evaluator-style domain drift fixture."""
    from app.services.debate import DebateDecisionEnum, DebateResult
    from app.services.research.models import ResearchItem as RI

    now = utc_now()
    research = [
        RI(
            title="x",
            summary="y",
            url="https://example.com/research",
            source_name="f",
            published_at=now,
            discovered_at=now,
            content_hash="z",
        )
    ]
    scored = ScoredCandidate(
        topic_id="drift-1",
        title="Best smartphone buying advice",
        candidate=CandidateTopic.model_validate(
            {
                "title": "Best smartphone buying advice",
                "angle": "Consumer",
                "why_now": "Sale",
                "relevance_to_persona": "None",
                "source_urls": ["https://example.com/research"],
                "confidence": 0.5,
            }
        ),
        scores=TopicScores(
            recency=0.5,
            persona_fit=0.1,
            importance=0.1,
            novelty=0.5,
            source_quality=0.5,
            evidence_quality=0.5,
            final_score=0.3,
        ),
        rank=1,
        decision="selected",
    )
    debate = DebateResult(
        topic_id="drift-1",
        for_argument="Weak for argument about phones.",
        against_argument="Outside domain and not evidence-driven for AI Security work.",
        evidence_points=[],
        weaknesses=["irrelevant"],
        decision=DebateDecisionEnum.REJECT,
        confidence=0.2,
        source_urls=["https://example.com/research"],
    )
    result, _ = ConsistencyGuard().evaluate(
        scored,
        debate=debate,
        persona_name="Kestrel",
        persona_domain="AI Security",
        research_items=research,
        memories=[],
    )
    assert result.decision == ConsistencyDecisionEnum.REJECT
