"""Integration tests for the Module 3 lifecycle worker."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.repositories.agents import AgentRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.repositories.posts import PostRepository
from app.db.session import get_session_factory
from app.orchestration.locks import AgentLockManager, GenerationLeaseManager, utc_now
from app.services.generation_cycle import GenerationCycleService
from app.worker.generation_worker import GenerationWorker


async def _create_running_agent(agent_id: str = "worker-agent") -> str:
    factory = get_session_factory()
    async with factory() as session:
        from app.services.agent_service import utc_now as now_fn

        now = now_fn()
        await AgentRepository(session).create_agent(
            agent_id=agent_id,
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
            cycle_count=0,
        )
        await session.commit()
    return agent_id


@pytest.mark.asyncio
async def test_worker_lifecycle_check_success(engine: AsyncEngine) -> None:
    agent_id = await _create_running_agent("cycle-success")
    locks = AgentLockManager()
    service = GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=locks,
        lease_seconds=get_settings().generation_lease_seconds,
        # Preserve Module 3-style controlled cycle for this regression test.
        cycle_hook=lambda: None,
    )
    worker = GenerationWorker(service)
    await worker.run_cycle(agent_id)

    factory = get_session_factory()
    async with factory() as session:
        agent = await AgentRepository(session).get_by_id(agent_id)
        assert agent is not None
        assert agent.cycle_count == 1
        assert agent.last_cycle_at is not None
        assert agent.status == "running"

        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert len(runs) == 1
        assert runs[0].status == "success"
        assert runs[0].stage == "lifecycle_check"

        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert posts == []


@pytest.mark.asyncio
async def test_worker_does_not_create_posts(
    lifespan_client: AsyncClient,
) -> None:
    init = await lifespan_client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    agent_id = init.json()["agentId"]
    app = lifespan_client._transport.app  # type: ignore[attr-defined]
    await app.state.lifecycle.worker.run_cycle(agent_id)

    feed = await lifespan_client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.json() == {"posts": []}


@pytest.mark.asyncio
async def test_simultaneous_worker_calls_do_not_overlap(engine: AsyncEngine) -> None:
    agent_id = await _create_running_agent("overlap-agent")
    locks = AgentLockManager()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_hook() -> None:
        started.set()
        await release.wait()

    service = GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=locks,
        lease_seconds=get_settings().generation_lease_seconds,
        cycle_hook=slow_hook,
    )

    async def run() -> str:
        return await service.execute(agent_id)

    task1 = asyncio.create_task(run())
    await started.wait()
    task2 = asyncio.create_task(run())
    # Give task2 a chance to attempt lock acquisition.
    await asyncio.sleep(0.05)
    second = await task2
    release.set()
    first = await task1

    assert first == "success"
    assert second == "skipped"

    factory = get_session_factory()
    async with factory() as session:
        agent = await AgentRepository(session).get_by_id(agent_id)
        assert agent is not None
        assert agent.cycle_count == 1
        running = await PipelineRunRepository(session).count_running_for_agent(agent_id)
        assert running == 0


@pytest.mark.asyncio
async def test_lease_prevents_duplicate_cycle(engine: AsyncEngine) -> None:
    agent_id = await _create_running_agent("lease-agent")
    factory = get_session_factory()
    async with factory() as session:
        leases = GenerationLeaseManager(session, lease_seconds=300)
        assert await leases.try_acquire(agent_id, "worker:external") is True
        await session.commit()

    # Memory lock free, but DB lease held by another owner.
    locks = AgentLockManager()
    service = GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=locks,
        lease_seconds=300,
    )
    result = await service.execute(agent_id)
    assert result == "skipped"

    async with factory() as session:
        agent = await AgentRepository(session).get_by_id(agent_id)
        assert agent is not None
        assert agent.cycle_count == 0
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert any(run.status == "skipped" for run in runs)


@pytest.mark.asyncio
async def test_worker_exception_marks_failed_and_releases_lease(
    engine: AsyncEngine,
) -> None:
    agent_id = await _create_running_agent("fail-agent")

    def boom() -> None:
        raise RuntimeError("simulated cycle failure")

    locks = AgentLockManager()
    service = GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=locks,
        lease_seconds=300,
        cycle_hook=boom,
    )
    worker = GenerationWorker(service)
    await worker.run_cycle(agent_id)  # must not raise

    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert "simulated cycle failure" in (runs[0].error or "")

        lease = await GenerationLeaseManager(session, 300).get(agent_id)
        assert lease is None

        agent = await AgentRepository(session).get_by_id(agent_id)
        assert agent is not None
        assert agent.status == "running"
        assert agent.cycle_count == 0

    assert locks.is_held(agent_id) is False


@pytest.mark.asyncio
async def test_expired_lease_recovered_by_cycle(engine: AsyncEngine) -> None:
    agent_id = await _create_running_agent("expired-lease-agent")
    factory = get_session_factory()
    async with factory() as session:
        leases = GenerationLeaseManager(session, lease_seconds=300)
        await leases.try_acquire(agent_id, "worker:stale")
        lease = await leases.get(agent_id)
        assert lease is not None
        lease.expires_at = utc_now() - timedelta(seconds=5)
        await session.commit()

    service = GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=300,
        cycle_hook=lambda: None,
    )
    assert await service.execute(agent_id) == "success"
