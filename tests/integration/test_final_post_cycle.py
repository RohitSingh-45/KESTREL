"""Integration tests for Module 9 final post generation + Module 10 publishing."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.repositories.agents import AgentRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.repositories.posts import PostRepository
from app.db.session import close_db, get_session_factory, init_db
from app.orchestration.locks import AgentLockManager, utc_now
from app.services.candidates import CandidateGenerationService
from app.services.debate import DebateLLMResponse
from app.services.final_post.models import GeneratedPost
from app.services.generation_cycle import GenerationCycleService
from app.services.llm.base import CandidateTopicList
from app.services.memory.models import MemoryExtractionResponse
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem


class _FinalConnector:
    name = "fixture-final"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        now = utc_now()
        return [
            ResearchItem(
                title="Prompt injection against AI agents",
                summary="Critical exploit path for autonomous agents.",
                url="https://arxiv.org/abs/final-1",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash="f1",
            ),
            ResearchItem(
                title="Jailbreak defense guidance",
                summary="Enterprise monitoring controls.",
                url="https://www.cisa.gov/final-2",
                source_name="CISA",
                published_at=now,
                discovered_at=now,
                content_hash="f2",
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
                    "source_urls": ["https://arxiv.org/abs/final-1"],
                    "confidence": 0.93,
                },
                {
                    "title": "Enterprise jailbreak defense guidance",
                    "angle": "Mitigations",
                    "why_now": "New guidance",
                    "relevance_to_persona": "AI Security operations",
                    "source_urls": ["https://www.cisa.gov/final-2"],
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


class _FinalLLM:
    available = True

    def __init__(self, *, bad_first: bool = False, always_bad: bool = False) -> None:
        self.call_count = 0
        self.final_calls = 0
        self.bad_first = bad_first
        self.always_bad = always_bad

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        schema = kwargs.get("schema")
        if schema is DebateLLMResponse:
            prompt = str(kwargs.get("user_prompt") or "")
            url = "https://arxiv.org/abs/final-1"
            if "https://www.cisa.gov/final-2" in prompt:
                url = "https://www.cisa.gov/final-2"
            return _debate(url)
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
                            "source_urls": ["https://arxiv.org/abs/final-1"],
                        }
                    ]
                }
            )
        if schema is GeneratedPost:
            self.final_calls += 1
            if self.always_bad or (self.bad_first and self.final_calls == 1):
                return GeneratedPost.model_validate(
                    {
                        "text": "THIS IS ABSOLUTELY THE WORST AI THREAT EVER!!!",
                        "rationale": "Selected with evidence for AI Security relevance over alternatives.",
                        "source_urls": ["https://arxiv.org/abs/final-1", "https://fake-source.com/x"],
                    }
                )
            return GeneratedPost.model_validate(
                {
                    "text": (
                        "Prompt injection against autonomous AI agents remains a concrete "
                        "AI Security concern with implications for defensive monitoring."
                    ),
                    "rationale": (
                        "Selected for strong AI Security relevance, recent evidence, and "
                        "higher novelty than lower-ranked alternatives. Prior theme memory "
                        "around prompt injection increased relevance, and consistency checks "
                        "confirmed domain and evidence alignment."
                    ),
                    "source_urls": ["https://arxiv.org/abs/final-1"],
                }
            )
        return _candidates()


class _UnavailableLLM:
    available = False

    async def chat(self, **_kwargs: object) -> str:
        raise RuntimeError("unavailable")

    async def structured_output(self, **_kwargs: object) -> object:
        raise RuntimeError("unavailable")


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


def _service(llm: object) -> GenerationCycleService:
    return GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(connectors=[_FinalConnector()], max_items=20),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_final_generation_with_mocked_llm(engine: AsyncEngine) -> None:
    agent_id = "final-gen-agent"
    await _create_agent(agent_id)
    llm = _FinalLLM()
    assert await _service(llm).execute(agent_id) == "success"

    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].status == "success"
        assert (
            runs[0].stage
            == "research_candidate_scoring_debate_memory_consistency_intelligence_adaptive_learning_final_generation_publishing_evaluation"
        )
        meta = json.loads(runs[0].meta_json or "{}")
        final = meta["final_generation"]
        assert final["status"] == "success"
        post = final["generated_post"]
        assert post["text"]
        assert post["rationale"]
        assert "https://arxiv.org/abs/final-1" in post["source_urls"]
        assert "fake-source" not in json.dumps(post)
        assert meta["publication"]["status"] == "published"
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 1
    assert llm.final_calls >= 1


@pytest.mark.asyncio
async def test_final_generation_missing_llm_skips(engine: AsyncEngine) -> None:
    agent_id = "final-skip-llm-agent"
    await _create_agent(agent_id)
    # Candidate generation also needs LLM — unavailable skips earlier.
    assert await _service(_UnavailableLLM()).execute(agent_id) == "skipped"
    factory = get_session_factory()
    async with factory() as session:
        assert await PostRepository(session).list_by_agent_newest_first(agent_id) == []


@pytest.mark.asyncio
async def test_validation_failure_then_repair(engine: AsyncEngine) -> None:
    agent_id = "final-repair-agent"
    await _create_agent(agent_id)
    llm = _FinalLLM(bad_first=True)
    assert await _service(llm).execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
            or "{}"
        )
        final = meta["final_generation"]
        assert final["status"] == "success"
        assert final["repaired"] is True
        assert "https://fake-source.com" not in json.dumps(final["generated_post"])
        assert meta["publication"]["status"] == "published"
        assert len(await PostRepository(session).list_by_agent_newest_first(agent_id)) == 1
    assert llm.final_calls == 2


@pytest.mark.asyncio
async def test_repair_failure_keeps_cycle_healthy(engine: AsyncEngine) -> None:
    agent_id = "final-repair-fail-agent"
    await _create_agent(agent_id)
    llm = _FinalLLM(always_bad=True)
    assert await _service(llm).execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].status == "success"
        meta = json.loads(runs[0].meta_json or "{}")
        final = meta["final_generation"]
        assert final["status"] == "rejected"
        assert final.get("generated_post") is None
        assert await PostRepository(session).list_by_agent_newest_first(agent_id) == []


@pytest.mark.asyncio
async def test_final_feed_returns_published_post(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    agent_id = init.json()["agentId"]
    await _service(_FinalLLM()).execute(agent_id)
    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    body = feed.json()
    assert len(body["posts"]) == 1
    assert body["posts"][0]["text"]
    assert body["posts"][0]["rationale"]
    assert body["posts"][0]["sources"]
    factory = get_session_factory()
    async with factory() as session:
        assert len(await PostRepository(session).list_by_agent_newest_first(agent_id)) == 1


@pytest.mark.asyncio
async def test_restart_keeps_published_post(engine: AsyncEngine) -> None:
    agent_id = "final-restart-agent"
    await _create_agent(agent_id)
    await _service(_FinalLLM()).execute(agent_id)
    await close_db()
    await init_db(get_settings())
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 1
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        meta = json.loads(runs[0].meta_json or "{}")
        assert meta["final_generation"]["status"] == "success"
        assert meta["publication"]["status"] == "published"
