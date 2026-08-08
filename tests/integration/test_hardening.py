"""Module 11 integration: isolation, feed read-only, research/LLM failure isolation."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.repositories.agents import AgentRepository
from app.db.repositories.memory import MemoryRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.repositories.posts import PostRepository
from app.db.repositories.topics import TopicRepository
from app.db.session import get_session_factory
from app.orchestration.lifecycle import AgentLifecycleService
from app.orchestration.locks import AgentLockManager, GenerationLeaseManager, utc_now
from app.orchestration.scheduler import AgentScheduler
from app.services.candidates import CandidateGenerationService
from app.services.final_post.models import GeneratedPost
from app.services.generation_cycle import GenerationCycleService
from app.services.llm.base import CandidateTopicList
from app.services.publishing import PublishingService
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem
from app.worker.generation_worker import GenerationWorker


class _OkConnector:
    name = "ok"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        now = utc_now()
        return [
            ResearchItem(
                title="Prompt injection advisory",
                summary="AI agent exploit path.",
                url="https://arxiv.org/abs/hard-1",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash="hard-1",
            )
        ]


class _TimeoutConnector:
    name = "timeout"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        raise TimeoutError("rss timeout")


class _MalformedConnector:
    name = "malformed"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        raise ValueError("malformed feed")


class _FailAllConnector:
    name = "fail-all"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        raise RuntimeError("connector down")


class _HardLLM:
    available = True

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        schema = kwargs.get("schema")
        if schema is GeneratedPost:
            return GeneratedPost.model_validate(
                {
                    "text": (
                        "Prompt injection remains a concrete AI Security risk for "
                        "autonomous agents under active research scrutiny."
                    ),
                    "rationale": (
                        "Selected for AI Security relevance and grounded evidence over "
                        "weaker alternatives."
                    ),
                    "source_urls": ["https://arxiv.org/abs/hard-1"],
                }
            )
        # Minimal candidates; debate/memory/consistency paths use other schemas in full cycle.
        from app.services.debate import DebateLLMResponse
        from app.services.memory.models import MemoryExtractionResponse

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
                    "source_urls": ["https://arxiv.org/abs/hard-1"],
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
                            "source_urls": ["https://arxiv.org/abs/hard-1"],
                        }
                    ]
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
                        "source_urls": ["https://arxiv.org/abs/hard-1"],
                        "confidence": 0.93,
                    }
                ]
            }
        )


async def _create_agent(agent_id: str, *, name: str = "Kestrel", domain: str = "AI Security") -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id=agent_id,
            name=name,
            domain=domain,
            status="running",
            created_at=now,
            updated_at=now,
        )
        await session.commit()


def _cycle(
    llm: object,
    *,
    connectors: list[object] | None = None,
) -> GenerationCycleService:
    return GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(
            connectors=connectors or [_OkConnector()],  # type: ignore[arg-type]
            max_items=20,
        ),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_research_partial_connector_failure(engine: AsyncEngine) -> None:
    hub = ResearchHub(
        connectors=[_OkConnector(), _TimeoutConnector(), _MalformedConnector()],
        max_items=20,
    )
    items = await hub.gather(agent_id="a", cycle_id="c")
    assert len(items) >= 1
    assert any(i.url.endswith("hard-1") for i in items)


@pytest.mark.asyncio
async def test_all_research_connectors_fail_skips_cycle(engine: AsyncEngine) -> None:
    agent_id = "hard-no-research"
    await _create_agent(agent_id)
    result = await _cycle(
        _HardLLM(),
        connectors=[_FailAllConnector(), _TimeoutConnector()],
    ).execute(agent_id)
    assert result == "skipped"
    factory = get_session_factory()
    async with factory() as session:
        assert await PostRepository(session).list_by_agent_newest_first(agent_id) == []
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].status == "skipped"


@pytest.mark.asyncio
async def test_worker_survives_unhandled_cycle_exception(engine: AsyncEngine) -> None:
    agent_id = "hard-worker-survive"
    await _create_agent(agent_id)

    class Boom(GenerationCycleService):
        async def execute(self, _agent_id: str) -> str:  # type: ignore[override]
            raise RuntimeError("boom")

    worker = GenerationWorker(
        Boom(
            session_factory=get_session_factory(),
            lock_manager=AgentLockManager(),
            lease_seconds=30,
        )
    )
    await worker.run_cycle(agent_id)  # must not raise


@pytest.mark.asyncio
async def test_scheduler_multi_agent_no_duplicate_jobs() -> None:
    async def _noop(_agent_id: str) -> None:
        return None

    scheduler = AgentScheduler(cycle_seconds=60, job_func=_noop)
    scheduler.start()
    try:
        for agent_id in ("agent-a", "agent-b", "agent-c"):
            scheduler.register_agent(agent_id)
            scheduler.register_agent(agent_id)  # idempotent
        assert scheduler.job_count() == 3
        assert scheduler.has_job("agent-a")
        assert scheduler.has_job("agent-b")
        assert scheduler.has_job("agent-c")
    finally:
        scheduler.stop(wait=False)


@pytest.mark.asyncio
async def test_lifecycle_register_running_agents_idempotent(engine: AsyncEngine) -> None:
    await _create_agent("life-a")
    await _create_agent("life-b")
    lifecycle = AgentLifecycleService(get_settings())
    # Minimal start path pieces: recover + scheduler register
    lifecycle._scheduler = AgentScheduler(
        cycle_seconds=60,
        job_func=AsyncMock(),
    )
    lifecycle._scheduler.start()
    lifecycle._started = True
    try:
        await lifecycle.register_running_agents()
        await lifecycle.register_running_agents()
        # Only the two running agents, despite double registration.
        assert lifecycle.scheduler.job_count() == 2
    finally:
        await lifecycle.stop(wait=False)


@pytest.mark.asyncio
async def test_multi_agent_data_isolation(engine: AsyncEngine) -> None:
    a = "iso-a"
    b = "iso-b"
    await _create_agent(a, name="Kestrel", domain="AI Security")
    await _create_agent(b, name="Other", domain="General")
    assert await _cycle(_HardLLM()).execute(a) == "success"

    factory = get_session_factory()
    async with factory() as session:
        # Give B a lease/topic/post that must not leak into A.
        now = utc_now()
        await GenerationLeaseManager(session, 300).try_acquire(b, "worker:b")
        from app.db.models import Topic

        session.add(
            Topic(
                id=str(uuid.uuid4()),
                agent_id=b,
                title="Unrelated topic for B",
                status="selected",
                created_at=now,
            )
        )
        await PublishingService(session).publish(
            agent_id=b,
            generated_post=GeneratedPost.model_validate(
                {
                    "text": "Agent B isolated post about general news.",
                    "rationale": "Isolation fixture for agent B only.",
                    "source_urls": ["https://example.com/b-only"],
                }
            ),
            grounded_urls=["https://example.com/b-only"],
            consistency_approved=True,
            final_validation_passed=True,
        )
        await session.commit()

        a_posts = await PostRepository(session).list_by_agent_newest_first(a)
        b_posts = await PostRepository(session).list_by_agent_newest_first(b)
        a_topics = await TopicRepository(session).list_by_agent(a)
        b_topics = await TopicRepository(session).list_by_agent(b)
        a_mem = await MemoryRepository(session).list_for_agent(a)
        b_mem = await MemoryRepository(session).list_for_agent(b)
        a_lease = await GenerationLeaseManager(session, 300).get(a)
        b_lease = await GenerationLeaseManager(session, 300).get(b)

        assert a_posts and b_posts
        assert {p.id for p in a_posts}.isdisjoint({p.id for p in b_posts})
        assert {t.id for t in a_topics}.isdisjoint({t.id for t in b_topics})
        assert {m.id for m in a_mem}.isdisjoint({m.id for m in b_mem})
        assert b_lease is not None
        if a_lease is not None:
            assert a_lease.lease_owner != b_lease.lease_owner


@pytest.mark.asyncio
async def test_feed_never_invokes_generation_stack(
    client: AsyncClient,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    agent_id = init.json()["agentId"]

    llm_mock = MagicMock()
    research_mock = AsyncMock()
    cycle_mock = AsyncMock()
    publish_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.llm.openai_client.OpenAIClient.chat",
        llm_mock,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.research.hub.ResearchHub.gather",
        research_mock,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.generation_cycle.GenerationCycleService.execute",
        cycle_mock,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.publishing.publisher.PublishingService.publish",
        publish_mock,
        raising=False,
    )

    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.status_code == 200
    assert feed.json() == {"posts": []}
    llm_mock.assert_not_called()
    research_mock.assert_not_called()
    cycle_mock.assert_not_called()
    publish_mock.assert_not_called()


@pytest.mark.asyncio
async def test_publishing_atomicity_db_failure(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    agent_id = "hard-atomic"
    await _create_agent(agent_id)
    factory = get_session_factory()
    async with factory() as session:
        pub = PublishingService(session)

        async def boom(**_kwargs: Any) -> Any:
            raise RuntimeError("insert failed")

        monkeypatch.setattr(pub._posts, "create_post", boom)
        result = await pub.publish(
            agent_id=agent_id,
            generated_post=GeneratedPost.model_validate(
                {
                    "text": "Atomicity check for AI Security publishing path.",
                    "rationale": "Selected to prove rollback safety.",
                    "source_urls": ["https://example.com/atomic"],
                }
            ),
            grounded_urls=["https://example.com/atomic"],
            consistency_approved=True,
            final_validation_passed=True,
        )
        assert result.status == "failed"
        await session.rollback()
        assert await PostRepository(session).list_by_agent_newest_first(agent_id) == []


@pytest.mark.asyncio
async def test_duplicate_publish_same_content_once(engine: AsyncEngine) -> None:
    agent_id = "hard-dup"
    await _create_agent(agent_id)
    post = GeneratedPost.model_validate(
        {
            "text": "Duplicate protection for AI Security content hashing.",
            "rationale": "Selected once; second attempt must skip.",
            "source_urls": ["https://example.com/dup"],
        }
    )
    factory = get_session_factory()
    async with factory() as session:
        pub = PublishingService(session)
        first = await pub.publish(
            agent_id=agent_id,
            generated_post=post,
            grounded_urls=["https://example.com/dup"],
            consistency_approved=True,
            final_validation_passed=True,
        )
        second = await pub.publish(
            agent_id=agent_id,
            generated_post=post,
            grounded_urls=["https://example.com/dup"],
            consistency_approved=True,
            final_validation_passed=True,
        )
        await session.commit()
        assert first.status == "published"
        assert second.status == "duplicate"
        assert len(await PostRepository(session).list_by_agent_newest_first(agent_id)) == 1


@pytest.mark.asyncio
async def test_pipeline_meta_summary_compact(engine: AsyncEngine) -> None:
    agent_id = "hard-meta"
    await _create_agent(agent_id)
    assert await _cycle(_HardLLM()).execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json or "{}"
        )
        summary = meta["summary"]
        assert summary["research_items"] >= 1
        assert summary["candidate_count"] >= 1
        assert summary["publication"] == "published"
        assert "openai_api_key" not in json.dumps(meta).lower()
        assert "sk-" not in json.dumps(meta)


@pytest.mark.asyncio
async def test_health_ok_when_llm_unavailable(lifespan_client: AsyncClient) -> None:
    health = await lifespan_client.get("/health")
    body = health.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["scheduler"] == "running"
    assert body["llm"] in {
        "ready",
        "unavailable",
        "authentication_failed",
        "unstable",
        "available",
    }


@pytest.mark.asyncio
async def test_graceful_shutdown_stops_scheduler(engine: AsyncEngine) -> None:
    lifecycle = AgentLifecycleService(get_settings())
    await lifecycle.start()
    assert lifecycle.is_scheduler_running is True
    await lifecycle.stop(wait=False)
    assert lifecycle.is_scheduler_running is False


@pytest.mark.asyncio
async def test_expired_lease_recovery_after_simulated_crash(engine: AsyncEngine) -> None:
    agent_id = "hard-crash-lease"
    await _create_agent(agent_id)
    factory = get_session_factory()
    async with factory() as session:
        leases = GenerationLeaseManager(session, 300)
        assert await leases.try_acquire(agent_id, "worker:crashed") is True
        lease = await leases.get(agent_id)
        assert lease is not None
        lease.expires_at = utc_now() - timedelta(seconds=1)
        await PipelineRunRepository(session).create_run(
            run_id="crashed-run",
            agent_id=agent_id,
            started_at=utc_now() - timedelta(minutes=5),
            status="running",
            stage="final_generation",
        )
        await session.commit()

    lifecycle = AgentLifecycleService(get_settings())
    recovered = await lifecycle.recover_stale_state()
    assert recovered["stale_pipeline_runs"] == 1
    assert recovered["expired_leases_cleared"] == 1

    async with factory() as session:
        run = await PipelineRunRepository(session).get_by_id("crashed-run")
        assert run is not None and run.status == "failed"
        assert await GenerationLeaseManager(session, 300).get(agent_id) is None
        # New cycle can acquire lease again.
        assert await GenerationLeaseManager(session, 300).try_acquire(
            agent_id, "worker:restart"
        )
        await session.commit()
