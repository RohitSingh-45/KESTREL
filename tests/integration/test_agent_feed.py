"""Integration tests for GET /api/agent/feed."""

from __future__ import annotations

import ast
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.repositories.agents import AgentRepository
from app.db.repositories.posts import PostRepository
from app.db.session import close_db, get_session_factory, init_db
from app.config import get_settings


FORBIDDEN_IMPORT_ROOTS = {
    "app.worker",
    "app.orchestration",
    "app.services.llm",
    "app.services.research",
    "app.services.generation_pipeline",
    "app.services.debate",
    "app.services.scoring",
    "app.services.consistency",
}


async def _init_agent(client: AsyncClient) -> str:
    response = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    assert response.status_code == 200
    return response.json()["agentId"]


@pytest.mark.asyncio
async def test_feed_empty_for_new_agent(client: AsyncClient) -> None:
    agent_id = await _init_agent(client)

    response = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert response.status_code == 200
    assert response.json() == {"posts": []}


@pytest.mark.asyncio
async def test_feed_unknown_agent_returns_404(client: AsyncClient) -> None:
    response = await client.get(
        "/api/agent/feed",
        params={"agentId": "does-not-exist"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Agent not found"


@pytest.mark.asyncio
async def test_feed_returns_posts_newest_first(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    agent_id = await _init_agent(client)
    base = datetime(2026, 8, 7, 10, 0, 0, tzinfo=timezone.utc)

    factory = get_session_factory()
    async with factory() as session:
        posts_repo = PostRepository(session)
        created_ids: list[str] = []
        for index, offset_minutes in enumerate((0, 10, 20)):
            post_id = f"p{index}"
            created_ids.append(post_id)
            created_at = base + timedelta(minutes=offset_minutes)
            text = f"post-{index}"
            await posts_repo.create_post(
                post_id=post_id,
                agent_id=agent_id,
                created_at=created_at,
                text=text,
                rationale=f"rationale-{index}",
                sources_json=json.dumps([f"https://example.com/{index}"]),
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                topic=f"topic-{index}",
            )
        await session.commit()

    response = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert response.status_code == 200
    payload = response.json()
    posts = payload["posts"]

    assert len(posts) == 3
    assert [post["id"] for post in posts] == ["p2", "p1", "p0"]
    assert posts[0]["text"] == "post-2"
    assert posts[-1]["text"] == "post-0"
    assert posts[0]["createdAt"] == "2026-08-07T10:20:00Z"
    assert posts[-1]["createdAt"] == "2026-08-07T10:00:00Z"
    assert posts[0]["sources"] == ["https://example.com/2"]
    assert isinstance(posts[0]["sources"], list)
    assert posts[0]["rationale"] == "rationale-2"


@pytest.mark.asyncio
async def test_feed_route_has_no_generation_imports() -> None:
    route_path = (
        Path(__file__).resolve().parents[2] / "app" / "api" / "routes" / "agent.py"
    )
    source = route_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    for module in imported_modules:
        for forbidden in FORBIDDEN_IMPORT_ROOTS:
            assert not (
                module == forbidden or module.startswith(forbidden + ".")
            ), f"feed route imports forbidden module: {module}"

    # Positive allow-list of expected local imports.
    assert "app.api.schemas.agent" in imported_modules
    assert "app.api.schemas.post" in imported_modules
    assert "app.api.deps" in imported_modules
    assert "app.services.agent_service" in imported_modules


@pytest.mark.asyncio
async def test_agent_and_posts_persist_across_sessions(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    agent_id = await _init_agent(client)

    factory = get_session_factory()
    async with factory() as session:
        text = "persisted-post"
        await PostRepository(session).create_post(
            post_id=str(uuid.uuid4()),
            agent_id=agent_id,
            created_at=datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc),
            text=text,
            rationale="persisted rationale",
            sources_json=json.dumps(["https://example.com/persist"]),
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        await session.commit()

    # Close and reopen the DB engine/session factory to prove durable persistence.
    await close_db()
    await init_db(get_settings())

    factory = get_session_factory()
    async with factory() as session:
        agent = await AgentRepository(session).get_by_id(agent_id)
        assert agent is not None
        assert agent.name == "Kestrel"
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 1
        assert posts[0].text == "persisted-post"

    response = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["posts"]) == 1
    assert payload["posts"][0]["text"] == "persisted-post"
    assert payload["posts"][0]["createdAt"] == "2026-08-07T12:00:00Z"
