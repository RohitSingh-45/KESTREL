"""Integration tests for POST /api/agent/init."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.repositories.agents import AgentRepository
from app.db.session import get_session_factory


@pytest.mark.asyncio
async def test_init_creates_running_agent(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    response = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "agentId" in payload
    agent_id = payload["agentId"]
    assert isinstance(agent_id, str)
    assert agent_id.strip() != ""

    factory = get_session_factory()
    async with factory() as session:
        agent = await AgentRepository(session).get_by_id(agent_id)
        assert agent is not None
        assert agent.name == "Kestrel"
        assert agent.domain == "AI Security"
        assert agent.status == "running"
        assert agent.cycle_count == 0
        assert agent.created_at is not None
        assert agent.updated_at is not None


@pytest.mark.asyncio
async def test_init_creates_unique_agent_ids(client: AsyncClient) -> None:
    first = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    second = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["agentId"] != second.json()["agentId"]


@pytest.mark.asyncio
async def test_init_rejects_empty_persona_fields(client: AsyncClient) -> None:
    response = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "   ", "domain": "AI Security"}},
    )
    assert response.status_code == 422


def test_build_system_prompt_dynamic_persona() -> None:
    from app.services.llm.prompts import build_system_prompt

    prompt = build_system_prompt("Sentinel", "Cloud Security")
    assert "You are Sentinel, an autonomous Cloud Security persona." in prompt
