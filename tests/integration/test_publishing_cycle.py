"""Integration tests for Module 10 publishing and durable feed."""

from __future__ import annotations

import json
from datetime import datetime

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
from app.services.publishing import PublishingService, compute_content_hash
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem


class _PubConnector:
    name = "fixture-publish"

    def __init__(self, suffix: str = "1") -> None:
        self.suffix = suffix

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        now = utc_now()
        return [
            ResearchItem(
                title=f"Prompt injection against AI agents {self.suffix}",
                summary="Critical exploit path for autonomous agents.",
                url=f"https://arxiv.org/abs/pub-{self.suffix}",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash=f"pub-{self.suffix}",
            )
        ]


def _candidates(suffix: str = "1") -> CandidateTopicList:
    return CandidateTopicList.model_validate(
        {
            "candidates": [
                {
                    "title": f"Prompt injection attacks against autonomous AI agents {suffix}",
                    "angle": "Agent exploitability",
                    "why_now": "Fresh disclosure",
                    "relevance_to_persona": "Core AI Security",
                    "source_urls": [f"https://arxiv.org/abs/pub-{suffix}"],
                    "confidence": 0.93,
                }
            ]
        }
    )


class _PublishLLM:
    available = True

    def __init__(self, suffix: str = "1") -> None:
        self.suffix = suffix
        self.call_count = 0

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        schema = kwargs.get("schema")
        url = f"https://arxiv.org/abs/pub-{self.suffix}"
        if schema is DebateLLMResponse:
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
                        f"Prompt injection against AI agents remains a concrete AI Security "
                        f"concern in cycle {self.suffix}."
                    ),
                    "rationale": (
                        "Selected for strong AI Security relevance, recent evidence, and "
                        "higher novelty than alternatives. Consistency confirmed domain and "
                        "evidence alignment."
                    ),
                    "source_urls": [url],
                }
            )
        return _candidates(self.suffix)


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


def _service(llm: _PublishLLM) -> GenerationCycleService:
    return GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(
            connectors=[_PubConnector(llm.suffix)],
            max_items=20,
        ),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_end_to_end_mocked_cycle_publishes(engine: AsyncEngine) -> None:
    agent_id = "pub-e2e-agent"
    await _create_agent(agent_id)
    assert await _service(_PublishLLM("1")).execute(agent_id) == "success"

    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].status == "success"
        assert (
            runs[0].stage
            == "research_candidate_scoring_debate_memory_consistency_intelligence_adaptive_learning_final_generation_publishing_evaluation"
        )
        meta = json.loads(runs[0].meta_json or "{}")
        assert meta["final_generation"]["status"] == "success"
        assert meta["publication"]["status"] == "published"
        assert meta["publication"]["published_post_id"]
        assert meta["publication"]["content_hash"]
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 1
        assert posts[0].rationale
        assert json.loads(posts[0].sources_json) == ["https://arxiv.org/abs/pub-1"]


@pytest.mark.asyncio
async def test_multiple_posts_newest_first_and_survive_restart(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    agent_id = init.json()["agentId"]

    await _service(_PublishLLM("1")).execute(agent_id)
    await _service(_PublishLLM("2")).execute(agent_id)
    await _service(_PublishLLM("3")).execute(agent_id)

    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.status_code == 200
    body = feed.json()
    assert len(body["posts"]) == 3
    times = [datetime.fromisoformat(p["createdAt"].replace("Z", "+00:00")) for p in body["posts"]]
    assert times == sorted(times, reverse=True)
    ids = [p["id"] for p in body["posts"]]
    assert len(ids) == len(set(ids))
    for post in body["posts"]:
        assert set(post.keys()) >= {"id", "createdAt", "text", "rationale", "sources"}
        assert post["createdAt"].endswith("Z")
        assert post["text"]
        assert post["rationale"]
        assert isinstance(post["sources"], list)
        assert all(s.startswith("http") for s in post["sources"])

    await close_db()
    await init_db(get_settings())
    feed2 = await client.get("/api/agent/feed", params={"agentId": agent_id})
    # client may be bound to old app; query DB directly after restart
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 3
        assert {p.id for p in posts} == set(ids)


@pytest.mark.asyncio
async def test_feed_isolation_no_generation_side_effects(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    agent_id = init.json()["agentId"]
    # Feed must remain a pure read even with no posts yet.
    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.json() == {"posts": []}


@pytest.mark.asyncio
async def test_existing_post_survives_new_post(engine: AsyncEngine) -> None:
    agent_id = "pub-survive-agent"
    await _create_agent(agent_id)
    await _service(_PublishLLM("a")).execute(agent_id)
    await _service(_PublishLLM("b")).execute(agent_id)
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 2
        texts = {p.text for p in posts}
        assert any("cycle a" in t for t in texts)
        assert any("cycle b" in t for t in texts)


@pytest.mark.asyncio
async def test_source_ordering_deterministic(engine: AsyncEngine) -> None:
    agent_id = "pub-order-agent"
    await _create_agent(agent_id)
    factory = get_session_factory()
    async with factory() as session:
        post = GeneratedPost.model_validate(
            {
                "text": "AI Security note on prompt injection defenses.",
                "rationale": "Selected with evidence for AI Security over alternatives.",
                "source_urls": [
                    "https://example.com/b",
                    "https://example.com/a",
                    "https://example.com/b",
                ],
            }
        )
        result = await PublishingService(session).publish(
            agent_id=agent_id,
            generated_post=post,
            grounded_urls=["https://example.com/a", "https://example.com/b"],
            consistency_approved=True,
            final_validation_passed=True,
        )
        await session.commit()
        assert result.status == "published"
        assert result.post is not None
        assert result.post.sources == ["https://example.com/b", "https://example.com/a"]


@pytest.mark.asyncio
async def test_pipeline_metadata_records_publication(engine: AsyncEngine) -> None:
    agent_id = "pub-meta-agent"
    await _create_agent(agent_id)
    await _service(_PublishLLM("meta")).execute(agent_id)
    factory = get_session_factory()
    async with factory() as session:
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
            or "{}"
        )
        pub = meta["publication"]
        assert pub["status"] == "published"
        assert pub["published_post_id"]
        assert len(pub["content_hash"]) == 64
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert posts[0].content_hash == pub["content_hash"]
        assert posts[0].content_hash == compute_content_hash(
            posts[0].text, json.loads(posts[0].sources_json)
        )


class _UnavailableLLM:
    available = False

    async def chat(self, **_kwargs: object) -> str:
        raise RuntimeError("unavailable")

    async def structured_output(self, **_kwargs: object) -> object:
        raise RuntimeError("unavailable")


class _EmptyResearchConnector:
    name = "empty"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        return []


@pytest.mark.asyncio
async def test_llm_unavailable_no_posts(engine: AsyncEngine) -> None:
    agent_id = "pub-no-llm"
    await _create_agent(agent_id)
    llm = _UnavailableLLM()
    service = GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(connectors=[_PubConnector()], max_items=20),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )
    assert await service.execute(agent_id) == "skipped"
    factory = get_session_factory()
    async with factory() as session:
        assert await PostRepository(session).list_by_agent_newest_first(agent_id) == []


@pytest.mark.asyncio
async def test_research_failure_no_posts(engine: AsyncEngine) -> None:
    agent_id = "pub-no-research"
    await _create_agent(agent_id)
    service = GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(connectors=[_EmptyResearchConnector()], max_items=20),
        candidate_service=CandidateGenerationService(_PublishLLM()),  # type: ignore[arg-type]
        llm_client=_PublishLLM(),  # type: ignore[arg-type]
    )
    assert await service.execute(agent_id) == "skipped"
    factory = get_session_factory()
    async with factory() as session:
        assert await PostRepository(session).list_by_agent_newest_first(agent_id) == []


@pytest.mark.asyncio
async def test_worker_continues_after_publish_failure(engine: AsyncEngine, monkeypatch) -> None:
    import app.services.generation_cycle as cycle_mod
    from app.services.publishing.models import PublicationResult
    from app.services.publishing.publisher import PublishingService as RealPublisher

    agent_id = "pub-continue-agent"
    await _create_agent(agent_id)

    calls = {"n": 0}
    original = RealPublisher.publish

    async def flaky(self, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            return PublicationResult(status="failed", reason="forced_publish_failure")
        return await original(self, **kwargs)

    monkeypatch.setattr(cycle_mod.PublishingService, "publish", flaky)

    first = await _service(_PublishLLM("fail1")).execute(agent_id)
    assert first == "failed"
    second = await _service(_PublishLLM("ok2")).execute(agent_id)
    assert second == "success"
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 1


@pytest.mark.asyncio
async def test_duplicate_cycle_is_skipped(engine: AsyncEngine) -> None:
    agent_id = "pub-dup-cycle"
    await _create_agent(agent_id)
    assert await _service(_PublishLLM("same")).execute(agent_id) == "success"
    assert await _service(_PublishLLM("same")).execute(agent_id) == "skipped"
    factory = get_session_factory()
    async with factory() as session:
        assert len(await PostRepository(session).list_by_agent_newest_first(agent_id)) == 1
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        statuses = {r.status for r in runs}
        assert "success" in statuses
        assert "skipped" in statuses


@pytest.mark.asyncio
async def test_evaluator_feed_schema_contract(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    assert init.status_code == 200
    agent_id = init.json()["agentId"]
    assert agent_id
    await _service(_PublishLLM("schema")).execute(agent_id)
    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.status_code == 200
    body = feed.json()
    assert set(body.keys()) == {"posts"}
    assert len(body["posts"]) == 1
    post = body["posts"][0]
    assert set(post.keys()) == {"id", "createdAt", "text", "rationale", "sources"}
    assert post["createdAt"].endswith("Z")
    datetime.fromisoformat(post["createdAt"].replace("Z", "+00:00"))
