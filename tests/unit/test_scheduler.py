"""Unit tests for AgentScheduler job registration."""

from __future__ import annotations

import pytest

from app.orchestration.scheduler import AgentScheduler, job_id_for_agent


async def _noop(_agent_id: str) -> None:
    return None


@pytest.mark.asyncio
async def test_register_agent_creates_single_job() -> None:
    scheduler = AgentScheduler(cycle_seconds=60, job_func=_noop)
    scheduler.start()
    try:
        scheduler.register_agent("agent-a")
        assert scheduler.has_job("agent-a")
        assert scheduler.job_count() == 1
        assert scheduler.scheduler.get_job(job_id_for_agent("agent-a")) is not None
    finally:
        scheduler.stop(wait=True)


@pytest.mark.asyncio
async def test_register_same_agent_twice_does_not_duplicate() -> None:
    scheduler = AgentScheduler(cycle_seconds=60, job_func=_noop)
    scheduler.start()
    try:
        scheduler.register_agent("agent-a")
        scheduler.register_agent("agent-a")
        assert scheduler.job_count() == 1
    finally:
        scheduler.stop(wait=True)


@pytest.mark.asyncio
async def test_scheduler_start_stop() -> None:
    scheduler = AgentScheduler(cycle_seconds=60, job_func=_noop)
    assert scheduler.is_running is False
    scheduler.start()
    assert scheduler.is_running is True
    scheduler.stop(wait=True)
    assert scheduler.is_running is False
