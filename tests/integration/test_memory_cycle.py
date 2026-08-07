"""Integration tests for Module 7 persistent memory in the generation cycle."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.repositories.agents import AgentRepository
from app.db.repositories.memory import MemoryRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.repositories.posts import PostRepository
from app.db.session import close_db, get_session_factory, init_db
from app.orchestration.locks import AgentLockManager, utc_now
from app.services.candidates import CandidateGenerationService
from app.services.debate import DebateLLMResponse
from app.services.final_post.models import GeneratedPost
from app.services.generation_cycle import GenerationCycleService
from app.services.llm.base import CandidateTopicList
from app.services.memory.models import MemoryExtractionResponse, MemoryKindEnum, MemoryValue
from app.services.memory.store import MemoryStore
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem


class _MemConnector:
    name = "fixture-memory"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        now = utc_now()
        return [
            ResearchItem(
                title="Prompt injection against AI agents",
                summary="Critical exploit path for autonomous agents.",
                url="https://arxiv.org/abs/mem-1",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash="m1",
            ),
            ResearchItem(
                title="Jailbreak defense guidance",
                summary="Enterprise monitoring controls.",
                url="https://www.cisa.gov/mem-2",
                source_name="CISA",
                published_at=now,
                discovered_at=now,
                content_hash="m2",
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
                    "source_urls": ["https://arxiv.org/abs/mem-1"],
                    "confidence": 0.93,
                },
                {
                    "title": "Enterprise jailbreak defense guidance",
                    "angle": "Mitigations",
                    "why_now": "New guidance",
                    "relevance_to_persona": "AI Security operations",
                    "source_urls": ["https://www.cisa.gov/mem-2"],
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
                "Evidence may be incomplete, novelty versus prior topics is uncertain, "
                "and practical impact could be overstated."
            ),
            "evidence_points": ["Source describes an AI agent security issue."],
            "weaknesses": ["Limited operational detail."],
            "decision": "proceed",
            "confidence": 0.86,
            "source_urls": [url],
        }
    )


class _MemoryAwareLLM:
    available = True

    def __init__(self) -> None:
        self.call_count = 0
        self.debate_prompts: list[str] = []
        self.extraction_calls = 0
        self.relation = "supporting"
        self.confidence = 0.84

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        schema = kwargs.get("schema")
        if schema is DebateLLMResponse:
            prompt = str(kwargs.get("user_prompt") or "")
            self.debate_prompts.append(prompt)
            url = "https://arxiv.org/abs/mem-1"
            if "https://www.cisa.gov/mem-2" in prompt:
                url = "https://www.cisa.gov/mem-2"
            return _debate(url)
        if schema is MemoryExtractionResponse:
            self.extraction_calls += 1
            return MemoryExtractionResponse.model_validate(
                {
                    "memories": [
                        {
                            "kind": "belief",
                            "key": "prompt injection",
                            "value": (
                                "Prompt injection is a significant security risk for AI agents."
                            ),
                            "subject": "prompt injection",
                            "predicate": "is a significant security risk for",
                            "object": "AI agents",
                            "stance": "concerned",
                            "confidence": self.confidence,
                            "relation": self.relation,
                            "source_urls": ["https://arxiv.org/abs/mem-1"],
                        },
                        {
                            "kind": "theme",
                            "key": "ai agents",
                            "value": "AI agents remain a recurring security theme.",
                            "confidence": 0.8,
                            "relation": "supporting",
                            "source_urls": ["https://arxiv.org/abs/mem-1"],
                        },
                    ]
                }
            )
        if schema is GeneratedPost:
            return GeneratedPost.model_validate(
                {
                    "text": (
                        "Prompt injection against autonomous AI agents remains a concrete "
                        "AI Security concern backed by current research disclosures."
                    ),
                    "rationale": (
                        "Selected for AI Security relevance and recent evidence, preferred "
                        "over alternatives with weaker security specificity."
                    ),
                    "source_urls": ["https://arxiv.org/abs/mem-1"],
                }
            )
        return _candidates()


class _MemoryFailExtractLLM(_MemoryAwareLLM):
    async def structured_output(self, **kwargs: object) -> object:
        schema = kwargs.get("schema")
        if schema is MemoryExtractionResponse:
            self.call_count += 1
            self.extraction_calls += 1
            raise RuntimeError("memory extraction boom")
        return await super().structured_output(**kwargs)


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
        research_hub=ResearchHub(connectors=[_MemConnector()], max_items=20),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_complete_cycle_extracts_and_persists_memory(engine: AsyncEngine) -> None:
    agent_id = "mem-cycle-agent"
    await _create_agent(agent_id)
    llm = _MemoryAwareLLM()
    assert await _service(llm).execute(agent_id) == "success"

    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].stage == "research_candidate_scoring_debate_memory_consistency_intelligence_adaptive_learning_final_generation_publishing_evaluation"
        meta = json.loads(runs[0].meta_json or "{}")
        assert meta["memory"]["status"] == "success"
        assert meta["memory"]["created"] >= 1
        assert llm.extraction_calls == 1

        memories = await MemoryRepository(session).list_for_agent(agent_id)
        assert len(memories) >= 1
        keys = {m.key for m in memories}
        assert "prompt injection" in keys
        belief = next(m for m in memories if m.key == "prompt injection")
        payload = json.loads(belief.value_json)
        assert payload["confidence"] >= 0.55
        assert "https://arxiv.org/abs/mem-1" in payload["supporting_source_urls"]
        assert meta["publication"]["status"] == "published"
        assert len(await PostRepository(session).list_by_agent_newest_first(agent_id)) == 1


@pytest.mark.asyncio
async def test_memory_passed_into_debate_context(engine: AsyncEngine) -> None:
    agent_id = "mem-debate-context-agent"
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
                subject="prompt injection",
                predicate="remains a major security concern for",
                object="AI agents",
                confidence=0.8,
                supporting_source_urls=["https://arxiv.org/abs/mem-1"],
                evidence_count=2,
            ),
            salience=0.8,
            now=now,
        )
        await session.commit()

    llm = _MemoryAwareLLM()
    await _service(llm).execute(agent_id)
    assert llm.debate_prompts
    assert any("previously stored memories" in p.lower() for p in llm.debate_prompts)
    assert any("prompt injection" in p.lower() for p in llm.debate_prompts)


@pytest.mark.asyncio
async def test_memory_survives_restart_and_second_cycle_retrieval(
    engine: AsyncEngine,
) -> None:
    agent_id = "mem-restart-agent"
    await _create_agent(agent_id)
    llm = _MemoryAwareLLM()
    await _service(llm).execute(agent_id)

    factory = get_session_factory()
    async with factory() as session:
        before = await MemoryRepository(session).list_for_agent(agent_id)
        assert before
        before_ids = {m.id: m.value_json for m in before}

    await close_db()
    await init_db(get_settings())
    factory = get_session_factory()
    async with factory() as session:
        after = await MemoryRepository(session).list_for_agent(agent_id)
        assert {m.id: m.value_json for m in after} == before_ids

    llm2 = _MemoryAwareLLM()
    llm2.relation = "supporting"
    await _service(llm2).execute(agent_id)
    assert any("prompt injection" in p.lower() for p in llm2.debate_prompts)

    async with factory() as session:
        memories = await MemoryRepository(session).list_for_agent(agent_id)
        belief = next(m for m in memories if m.key == "prompt injection")
        payload = json.loads(belief.value_json)
        assert payload["evidence_count"] >= 2
        assert len(payload.get("history") or []) >= 1


@pytest.mark.asyncio
async def test_supporting_and_contradicting_belief_evolution(engine: AsyncEngine) -> None:
    agent_id = "mem-evolve-agent"
    await _create_agent(agent_id)
    llm = _MemoryAwareLLM()
    llm.relation = "supporting"
    llm.confidence = 0.85
    await _service(llm).execute(agent_id)

    factory = get_session_factory()
    async with factory() as session:
        belief = await MemoryRepository(session).find_by_key(
            agent_id, kind="belief", key="prompt injection"
        )
        assert belief is not None
        before = json.loads(belief.value_json)
        before_conf = before["confidence"]

    llm2 = _MemoryAwareLLM()
    llm2.relation = "contradicting"
    llm2.confidence = 0.9
    await _service(llm2).execute(agent_id)

    async with factory() as session:
        belief = await MemoryRepository(session).find_by_key(
            agent_id, kind="belief", key="prompt injection"
        )
        assert belief is not None
        after = json.loads(belief.value_json)
        assert after["confidence"] < before_conf
        assert after["contradicting_source_urls"]
        assert before["supporting_source_urls"][0] in after["supporting_source_urls"]
        assert len(after["history"]) >= 2


@pytest.mark.asyncio
async def test_partial_memory_failure_does_not_kill_cycle(engine: AsyncEngine) -> None:
    agent_id = "mem-fail-agent"
    await _create_agent(agent_id)
    llm = _MemoryFailExtractLLM()
    assert await _service(llm).execute(agent_id) == "success"

    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].status == "success"
        meta = json.loads(runs[0].meta_json or "{}")
        assert meta["memory"]["status"] == "failed"
        # Memory failure must not block publishing of an approved finalist.
        assert len(await PostRepository(session).list_by_agent_newest_first(agent_id)) == 1


@pytest.mark.asyncio
async def test_memory_feed_isolation_and_durable_posts(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    agent_id = init.json()["agentId"]
    assert await _service(_MemoryAwareLLM()).execute(agent_id) == "success"
    # Identical content on retry must not create a second durable post.
    second = await _service(_MemoryAwareLLM()).execute(agent_id)
    assert second in {"skipped", "success"}
    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert len(feed.json()["posts"]) == 1

    factory = get_session_factory()
    async with factory() as session:
        assert len(await PostRepository(session).list_by_agent_newest_first(agent_id)) == 1
        assert await MemoryRepository(session).list_for_agent(agent_id)
