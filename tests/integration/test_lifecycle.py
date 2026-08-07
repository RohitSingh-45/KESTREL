"""Integration tests for autonomous lifecycle wiring."""

from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.db.session import close_db, init_db
from app.main import create_app
from app.orchestration.lifecycle import AgentLifecycleService
from tests.conftest import _cleanup_test_db


@pytest.mark.asyncio
async def test_scheduler_starts_during_lifespan(lifespan_client: AsyncClient) -> None:
    response = await lifespan_client.get("/health")
    assert response.status_code == 200
    assert response.json()["scheduler"] == "running"


@pytest.mark.asyncio
async def test_scheduler_shuts_down_cleanly() -> None:
    get_settings.cache_clear()
    await close_db()
    _cleanup_test_db()
    application = create_app()
    async with application.router.lifespan_context(application):
        lifecycle = application.state.lifecycle
        assert lifecycle is not None
        assert lifecycle.is_scheduler_running is True
    assert application.state.lifecycle is None
    await close_db()
    _cleanup_test_db()


@pytest.mark.asyncio
async def test_init_registers_exactly_one_scheduler_job(
    lifespan_client: AsyncClient,
) -> None:
    response = await lifespan_client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    assert response.status_code == 200
    agent_id = response.json()["agentId"]

    app = lifespan_client._transport.app  # type: ignore[attr-defined]
    lifecycle = app.state.lifecycle
    assert isinstance(lifecycle, AgentLifecycleService)
    assert lifecycle.scheduler.has_job(agent_id)
    assert lifecycle.scheduler.job_count() == 1


@pytest.mark.asyncio
async def test_register_same_agent_twice_no_duplicate_jobs() -> None:
    get_settings.cache_clear()
    await close_db()
    _cleanup_test_db()
    await init_db(get_settings())
    from app.db.base import Base
    from app.db.session import get_engine

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    lifecycle = AgentLifecycleService(get_settings())
    await lifecycle.start()
    try:
        await lifecycle.register_agent("dup-agent")
        await lifecycle.register_agent("dup-agent")
        assert lifecycle.scheduler.job_count() == 1
    finally:
        await lifecycle.stop()
        await close_db()
        _cleanup_test_db()


@pytest.mark.asyncio
async def test_init_returns_quickly_without_waiting_for_cycle(
    lifespan_client: AsyncClient,
) -> None:
    started = time.perf_counter()
    response = await lifespan_client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_feed_remains_read_only_with_scheduler(
    lifespan_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init = await lifespan_client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    agent_id = init.json()["agentId"]
    app = lifespan_client._transport.app  # type: ignore[attr-defined]
    lifecycle: AgentLifecycleService = app.state.lifecycle
    # Pause autonomous ticks so only the feed request is under test.
    lifecycle.scheduler.unregister_agent(agent_id)

    called = {"count": 0}

    async def tracked(agent_id: str) -> None:
        called["count"] += 1

    monkeypatch.setattr(lifecycle.worker, "run_cycle", tracked)

    feed = await lifespan_client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.status_code == 200
    assert feed.json() == {"posts": []}
    assert called["count"] == 0


@pytest.mark.asyncio
async def test_restart_recovery_reregisters_running_agent() -> None:
    get_settings.cache_clear()
    await close_db()
    _cleanup_test_db()

    application = create_app()
    agent_id: str
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            init = await client.post(
                "/api/agent/init",
                json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
            )
            agent_id = init.json()["agentId"]
            assert application.state.lifecycle.scheduler.has_job(agent_id)

    # Simulate restart with same DB file.
    application2 = create_app()
    async with application2.router.lifespan_context(application2):
        lifecycle = application2.state.lifecycle
        assert lifecycle is not None
        assert lifecycle.is_scheduler_running
        assert lifecycle.scheduler.has_job(agent_id)

    await close_db()
    _cleanup_test_db()
