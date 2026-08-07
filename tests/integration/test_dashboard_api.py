"""Module 18 — dashboard API is read-only and preserves evaluator contracts."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.decision import DecisionService
from app.services.generation_cycle import GenerationCycleService
from app.services.observability import ObservabilityService
from app.services.publishing import PublishingService
from app.services.research.hub import ResearchHub


@pytest.mark.asyncio
async def test_dashboard_agents_and_state_after_init(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    empty = await client.get("/api/dashboard/agents")
    assert empty.status_code == 200
    assert empty.json()["agents"] == []

    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    assert init.status_code == 200
    agent_id = init.json()["agentId"]

    agents = await client.get("/api/dashboard/agents")
    assert agents.status_code == 200
    rows = agents.json()["agents"]
    assert len(rows) == 1
    assert rows[0]["agentId"] == agent_id
    assert rows[0]["name"] == "Kestrel"

    state = await client.get("/api/dashboard/state", params={"agentId": agent_id})
    assert state.status_code == 200
    payload = state.json()["state"]
    assert payload["hero"]["brand"] == "Kestrel"
    assert payload["hero"]["tagline"] == "Autonomous AI Security Agent"
    assert payload["hero"]["agentId"] == agent_id
    assert isinstance(payload["pipeline"], list)
    assert len(payload["pipeline"]) >= 10
    assert "posts" in payload
    assert "memory" in payload
    assert "decision" in payload
    assert "evaluation" in payload


@pytest.mark.asyncio
async def test_dashboard_unknown_agent_404(client: AsyncClient, engine: AsyncEngine) -> None:
    res = await client.get("/api/dashboard/state", params={"agentId": "missing-agent"})
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_does_not_invoke_pipeline(
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

    def boom(*_a: Any, **_k: Any) -> Any:
        called["n"] += 1
        raise AssertionError("dashboard must not run pipeline")

    monkeypatch.setattr(GenerationCycleService, "execute", boom)
    monkeypatch.setattr(DecisionService, "evaluate_gate", boom)
    monkeypatch.setattr(ObservabilityService, "record", boom)
    monkeypatch.setattr(PublishingService, "publish", boom)
    monkeypatch.setattr(ResearchHub, "gather", boom)

    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.status_code == 200
    dash = await client.get("/api/dashboard/state", params={"agentId": agent_id})
    assert dash.status_code == 200
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_init_and_feed_contracts_unchanged(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    body = init.json()
    assert set(body.keys()) == {"agentId"}
    feed = await client.get("/api/agent/feed", params={"agentId": body["agentId"]})
    assert feed.status_code == 200
    assert "posts" in feed.json()
