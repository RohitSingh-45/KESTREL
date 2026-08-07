"""Unit tests for in-memory locks and DB leases."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.session import get_session_factory
from app.orchestration.locks import (
    AgentLockManager,
    GenerationLeaseManager,
    utc_now,
)


@pytest.mark.asyncio
async def test_memory_lock_prevents_overlap() -> None:
    locks = AgentLockManager()
    assert await locks.try_acquire("a1") is True
    assert await locks.try_acquire("a1") is False
    await locks.release("a1")
    assert await locks.try_acquire("a1") is True
    await locks.release("a1")


@pytest.mark.asyncio
async def test_memory_lock_allows_different_agents() -> None:
    locks = AgentLockManager()
    assert await locks.try_acquire("a1") is True
    assert await locks.try_acquire("a2") is True
    await locks.release("a1")
    await locks.release("a2")


async def _ensure_agent(agent_id: str) -> None:
    from app.db.repositories.agents import AgentRepository
    from app.services.agent_service import utc_now as now_fn

    factory = get_session_factory()
    async with factory() as session:
        repo = AgentRepository(session)
        if await repo.get_by_id(agent_id) is None:
            now = now_fn()
            await repo.create_agent(
                agent_id=agent_id,
                name="Kestrel",
                domain="AI Security",
                status="running",
                created_at=now,
                updated_at=now,
            )
            await session.commit()


@pytest.mark.asyncio
async def test_db_lease_acquire_and_release(engine: AsyncEngine) -> None:
    await _ensure_agent("agent-1")
    factory = get_session_factory()
    async with factory() as session:
        leases = GenerationLeaseManager(session, lease_seconds=300)
        assert await leases.try_acquire("agent-1", "worker:one") is True
        assert await leases.try_acquire("agent-1", "worker:two") is False
        await leases.release("agent-1", "worker:one")
        assert await leases.try_acquire("agent-1", "worker:two") is True
        await session.commit()


@pytest.mark.asyncio
async def test_expired_lease_can_be_recovered(engine: AsyncEngine) -> None:
    await _ensure_agent("agent-exp")
    factory = get_session_factory()
    async with factory() as session:
        leases = GenerationLeaseManager(session, lease_seconds=300)
        assert await leases.try_acquire("agent-exp", "worker:old") is True
        lease = await leases.get("agent-exp")
        assert lease is not None
        lease.expires_at = utc_now() - timedelta(seconds=1)
        await session.commit()

    async with factory() as session:
        leases = GenerationLeaseManager(session, lease_seconds=300)
        assert await leases.try_acquire("agent-exp", "worker:new") is True
        lease = await leases.get("agent-exp")
        assert lease is not None
        assert lease.lease_owner == "worker:new"
        await session.commit()


@pytest.mark.asyncio
async def test_concurrent_memory_lock_only_one_wins() -> None:
    locks = AgentLockManager()
    results: list[bool] = []

    async def attempt() -> None:
        results.append(await locks.try_acquire("shared"))

    await asyncio.gather(*[attempt() for _ in range(20)])
    assert results.count(True) == 1
    assert results.count(False) == 19
    await locks.release("shared")
