"""Module 12 integration: diversity/story/contradiction in the generation cycle."""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.repositories.agents import AgentRepository
from app.db.repositories.memory import MemoryRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.repositories.posts import PostRepository
from app.db.repositories.topics import TopicRepository
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


class _IntelConnector:
    name = "fixture-intel"

    def __init__(self, *, bypass: bool = False) -> None:
        self.bypass = bypass

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        now = utc_now()
        if self.bypass:
            return [
                ResearchItem(
                    title="Defense X bypassed under attack Y for AI agents",
                    summary=(
                        "Researchers demonstrate Defense X was bypassed and is ineffective "
                        "against prompt injection on AI agents."
                    ),
                    url="https://arxiv.org/abs/intel-bypass",
                    source_name="arXiv",
                    published_at=now,
                    discovered_at=now,
                    content_hash="intel-bypass",
                )
            ]
        return [
            ResearchItem(
                title="Prompt injection technique against AI agents",
                summary="Researchers reported a prompt injection weakness affecting AI agents.",
                url="https://arxiv.org/abs/intel-1",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash="intel-1",
            ),
            ResearchItem(
                title="Enterprise jailbreak defense guidance",
                summary="CISA guidance for monitoring jailbreaks.",
                url="https://www.cisa.gov/intel-2",
                source_name="CISA",
                published_at=now,
                discovered_at=now,
                content_hash="intel-2",
            ),
        ]


class _IntelLLM:
    available = True

    def __init__(self, *, url: str = "https://arxiv.org/abs/intel-1") -> None:
        self.url = url
        self.debate_prompts: list[str] = []
        self.final_prompts: list[str] = []

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        schema = kwargs.get("schema")
        prompt = str(kwargs.get("user_prompt") or "")
        if schema is DebateLLMResponse:
            self.debate_prompts.append(prompt)
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
                    "source_urls": [self.url],
                }
            )
        if schema is MemoryExtractionResponse:
            return MemoryExtractionResponse.model_validate(
                {
                    "memories": [
                        {
                            "kind": "belief",
                            "key": "prompt injection defenses",
                            "value": (
                                "Defense X reduces prompt injection risk for AI agents."
                            ),
                            "subject": "Defense X",
                            "predicate": "reduces risk for",
                            "object": "prompt injection",
                            "confidence": 0.84,
                            "relation": "supporting",
                            "source_urls": [self.url],
                        }
                    ]
                }
            )
        if schema is GeneratedPost:
            self.final_prompts.append(prompt)
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
                    "source_urls": [self.url],
                }
            )
        # Candidate generation
        return CandidateTopicList.model_validate(
            {
                "candidates": [
                    {
                        "title": (
                            "Prompt injection technique against autonomous AI agents"
                            if "bypass" not in self.url
                            else "Defense X bypass under attack Y for AI agents"
                        ),
                        "angle": "Agent exploitability",
                        "why_now": (
                            "Fresh disclosure"
                            if "bypass" not in self.url
                            else "Mitigation bypassed under new attack"
                        ),
                        "relevance_to_persona": "Core AI Security",
                        "source_urls": [self.url],
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


def _service(llm: _IntelLLM, *, bypass: bool = False) -> GenerationCycleService:
    return GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(
            connectors=[_IntelConnector(bypass=bypass)],
            max_items=20,
        ),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_cycle_persists_diversity_and_story_meta(engine: AsyncEngine) -> None:
    agent_id = "intel-meta-agent"
    await _create_agent(agent_id)
    assert await _service(_IntelLLM()).execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        topics = await TopicRepository(session).list_by_agent(agent_id, status="selected")
        assert topics
        meta = json.loads(topics[0].scores_json or "{}").get("meta") or {}
        assert "source_diversity_score" in meta
        assert "story_type" in meta
        assert meta["story_type"] in {"new_story", "continuing_story", "uncertain"}
        # Module 5 scores still present and authoritative.
        scores = json.loads(topics[0].scores_json or "{}")["scores"]
        assert set(scores) == {
            "recency",
            "persona_fit",
            "importance",
            "novelty",
            "source_quality",
            "evidence_quality",
        }


@pytest.mark.asyncio
async def test_debate_receives_intelligence_context(engine: AsyncEngine) -> None:
    agent_id = "intel-debate-agent"
    await _create_agent(agent_id)
    llm = _IntelLLM()
    await _service(llm).execute(agent_id)
    assert llm.debate_prompts
    assert any("Intelligence context" in p for p in llm.debate_prompts)


@pytest.mark.asyncio
async def test_final_generation_receives_story_context(engine: AsyncEngine) -> None:
    agent_id = "intel-final-agent"
    await _create_agent(agent_id)
    llm = _IntelLLM()
    await _service(llm).execute(agent_id)
    assert llm.final_prompts
    assert any("Story context" in p for p in llm.final_prompts)


@pytest.mark.asyncio
async def test_contradiction_forwards_to_memory_without_overwrite(
    engine: AsyncEngine,
) -> None:
    agent_id = "intel-contradict-agent"
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
            ),
            salience=0.8,
            now=now,
        )
        await session.commit()

    llm = _IntelLLM(url="https://arxiv.org/abs/intel-bypass")
    assert await _service(llm, bypass=True).execute(agent_id) == "success"

    async with factory() as session:
        belief = await MemoryRepository(session).find_by_key(
            agent_id, kind="belief", key="prompt injection defenses"
        )
        assert belief is not None
        payload = json.loads(belief.value_json)
        assert payload["confidence"] < 0.84
        assert payload["confidence"] > 0.0
        assert "https://arxiv.org/abs/intel-bypass" in payload["contradicting_source_urls"]
        assert "https://arxiv.org/abs/old" in payload["supporting_source_urls"]
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        intel = json.loads(runs[0].meta_json or "{}").get("intelligence") or {}
        assert intel.get("contradiction", {}).get("detected") is True


@pytest.mark.asyncio
async def test_story_meta_survives_restart(engine: AsyncEngine) -> None:
    agent_id = "intel-restart-agent"
    await _create_agent(agent_id)
    await _service(_IntelLLM()).execute(agent_id)
    factory = get_session_factory()
    async with factory() as session:
        before = {
            t.id: t.scores_json
            for t in await TopicRepository(session).list_by_agent(agent_id)
        }
        assert before
    await close_db()
    await init_db(get_settings())
    factory = get_session_factory()
    async with factory() as session:
        after = {
            t.id: t.scores_json
            for t in await TopicRepository(session).list_by_agent(agent_id)
        }
        assert after == before
        meta = json.loads(next(iter(after.values())) or "{}").get("meta") or {}
        assert "story_type" in meta


@pytest.mark.asyncio
async def test_intelligence_failure_does_not_kill_worker(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = "intel-fail-agent"
    await _create_agent(agent_id)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("diversity boom")

    monkeypatch.setattr(
        "app.services.source_diversity.analyzer.SourceDiversityAnalyzer.analyze_candidate",
        boom,
    )
    # Should still complete publishing path if other stages succeed.
    result = await _service(_IntelLLM()).execute(agent_id)
    assert result in {"success", "skipped", "failed"}
    # Worker boundary: execute returned; server would remain alive.
    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs


@pytest.mark.asyncio
async def test_multi_agent_intelligence_isolation(engine: AsyncEngine) -> None:
    a = "intel-a"
    b = "intel-b"
    await _create_agent(a)
    await _create_agent(b)
    await _service(_IntelLLM()).execute(a)
    factory = get_session_factory()
    async with factory() as session:
        a_topics = await TopicRepository(session).list_by_agent(a)
        b_topics = await TopicRepository(session).list_by_agent(b)
        assert a_topics
        assert b_topics == []
        a_posts = await PostRepository(session).list_by_agent_newest_first(a)
        b_posts = await PostRepository(session).list_by_agent_newest_first(b)
        assert a_posts
        assert b_posts == []


@pytest.mark.asyncio
async def test_feed_still_read_only_after_intelligence(
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
        raise AssertionError("story tracker must not run from feed")

    monkeypatch.setattr(
        "app.services.story_tracking.tracker.StoryTracker.match_candidate",
        track,
    )
    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.status_code == 200
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_continuing_story_on_second_cycle(engine: AsyncEngine) -> None:
    agent_id = "intel-continue-agent"
    await _create_agent(agent_id)
    await _service(_IntelLLM()).execute(agent_id)
    # Second cycle with related bypass development.
    llm2 = _IntelLLM(url="https://arxiv.org/abs/intel-bypass")
    await _service(llm2, bypass=True).execute(agent_id)
    factory = get_session_factory()
    async with factory() as session:
        topics = await TopicRepository(session).list_by_agent(agent_id)
        metas = [
            (json.loads(t.scores_json or "{}").get("meta") or {})
            for t in topics
            if t.scores_json
        ]
        # At least one topic should carry story metadata.
        assert any("story_type" in m for m in metas)
