"""Integration tests for research + candidate generation cycles."""

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
from app.db.session import get_session_factory
from app.orchestration.locks import AgentLockManager, utc_now
from app.services.candidates import CandidateGenerationService
from app.services.generation_cycle import GenerationCycleService
from app.services.debate import DebateLLMResponse
from app.services.final_post.models import GeneratedPost
from app.services.llm.base import CandidateTopicList
from app.services.memory.models import MemoryExtractionResponse
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem
from app.worker.generation_worker import GenerationWorker


class _FixtureConnector:
    name = "fixture"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        return [
            ResearchItem(
                title="LLM jailbreak technique disclosed",
                summary="Researchers describe a new AI security issue.",
                url="https://example.com/ai-security-1",
                source_name="fixture",
                discovered_at=utc_now(),
                content_hash="hash-ai-1",
            )
        ]


def _debate_response(url: str = "https://example.com/ai-security-1") -> DebateLLMResponse:
    return DebateLLMResponse.model_validate(
        {
            "for_argument": (
                "This disclosure is timely AI Security evidence of jailbreak risk "
                "and warrants further consideration for defenders."
            ),
            "against_argument": (
                "Evidence may be incomplete: the fixture summary is thin, novelty "
                "versus prior jailbreak discussions is unclear, and practical "
                "exploitability is not fully established by the source."
            ),
            "evidence_points": [
                "Source reports a newly disclosed LLM jailbreak technique."
            ],
            "weaknesses": ["Limited technical depth in the research summary."],
            "decision": "proceed",
            "confidence": 0.86,
            "source_urls": [url],
        }
    )


class _MockLLM:
    available = True
    call_count = 0

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        schema = kwargs.get("schema")
        if schema is DebateLLMResponse:
            return _debate_response()
        if schema is MemoryExtractionResponse:
            return MemoryExtractionResponse.model_validate(
                {
                    "memories": [
                        {
                            "kind": "theme",
                            "key": "prompt injection",
                            "value": "Prompt injection remains a recurring AI agent risk.",
                            "stance": "concerned",
                            "confidence": 0.8,
                            "relation": "supporting",
                            "source_urls": ["https://example.com/ai-security-1"],
                        }
                    ]
                }
            )
        if schema is GeneratedPost:
            return GeneratedPost.model_validate(
                {
                    "text": (
                        "New research describes an emerging jailbreak technique against "
                        "LLM systems, underscoring why AI-agent defenses need tighter monitoring."
                    ),
                    "rationale": (
                        "Selected for strong AI Security relevance and recent evidence. "
                        "The topic was preferred over weaker alternatives due to clearer "
                        "security specificity and grounded source support."
                    ),
                    "source_urls": ["https://example.com/ai-security-1"],
                }
            )
        return CandidateTopicList.model_validate(
            {
                "candidates": [
                    {
                        "title": "Emerging jailbreak risk",
                        "angle": "Defensive monitoring",
                        "why_now": "Fresh disclosure",
                        "relevance_to_persona": "Directly AI Security",
                        "source_urls": [
                            "https://example.com/ai-security-1",
                            "https://invented.example/nope",
                        ],
                        "confidence": 0.9,
                    }
                ]
            }
        )


class _UnavailableLLM:
    available = False

    async def chat(self, **_kwargs: object) -> str:
        raise RuntimeError("unavailable")

    async def structured_output(self, **_kwargs: object) -> CandidateTopicList:
        raise RuntimeError("unavailable")


class _FailingLLM:
    available = True

    async def chat(self, **_kwargs: object) -> str:
        raise RuntimeError("llm down")

    async def structured_output(self, **_kwargs: object) -> CandidateTopicList:
        raise RuntimeError("llm down")


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


@pytest.mark.asyncio
async def test_research_cycle_persists_candidates_not_posts(engine: AsyncEngine) -> None:
    agent_id = "research-cycle-agent"
    await _create_agent(agent_id)
    llm = _MockLLM()
    service = GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(connectors=[_FixtureConnector()], max_items=20),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )
    result = await service.execute(agent_id)
    assert result == "success"

    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].status == "success"
        assert runs[0].stage == "research_candidate_scoring_debate_memory_consistency_intelligence_adaptive_learning_final_generation_publishing_evaluation"

        topics = await TopicRepository(session).list_by_agent(agent_id, status="selected")
        assert len(topics) == 1
        assert topics[0].title == "Emerging jailbreak risk"
        scores = json.loads(topics[0].scores_json or "{}")
        assert scores["source_urls"] == ["https://example.com/ai-security-1"]
        assert scores["decision"] == "selected"
        assert "final_score" in scores
        debate = json.loads(topics[0].debate_json or "{}")
        assert debate["decision"] in {"proceed", "reject", "uncertain"}
        assert debate["for_argument"]
        assert debate["against_argument"]
        assert topics[0].embedding_json is None

        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 1


@pytest.mark.asyncio
async def test_llm_unavailable_cycle_skipped(engine: AsyncEngine) -> None:
    agent_id = "llm-skip-agent"
    await _create_agent(agent_id)
    llm = _UnavailableLLM()
    service = GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=300,
        research_hub=ResearchHub(connectors=[_FixtureConnector()], max_items=20),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )
    assert await service.execute(agent_id) == "skipped"

    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].status == "skipped"
        assert runs[0].stage == "candidate_generation"
        assert await TopicRepository(session).count_by_agent(agent_id) == 0
        assert await PostRepository(session).list_by_agent_newest_first(agent_id) == []


@pytest.mark.asyncio
async def test_llm_failure_does_not_crash_and_marks_failed(engine: AsyncEngine) -> None:
    agent_id = "llm-fail-agent"
    await _create_agent(agent_id)
    llm = _FailingLLM()
    worker = GenerationWorker(
        GenerationCycleService(
            session_factory=get_session_factory(),
            lock_manager=AgentLockManager(),
            lease_seconds=300,
            research_hub=ResearchHub(connectors=[_FixtureConnector()], max_items=20),
            candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
            llm_client=llm,  # type: ignore[arg-type]
        )
    )
    await worker.run_cycle(agent_id)

    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].status == "failed"
        assert await PostRepository(session).list_by_agent_newest_first(agent_id) == []


@pytest.mark.asyncio
async def test_feed_returns_post_after_research_cycle(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    agent_id = init.json()["agentId"]

    llm = _MockLLM()
    service = GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=300,
        research_hub=ResearchHub(connectors=[_FixtureConnector()], max_items=20),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )
    await service.execute(agent_id)

    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.status_code == 200
    assert len(feed.json()["posts"]) == 1
