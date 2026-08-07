"""Agent and feed application services."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.agent import InitResponse, Persona
from app.api.schemas.post import FeedResponse, PostResponse
from app.db.models import Agent, Post
from app.db.repositories.agents import AgentRepository
from app.db.repositories.posts import PostRepository
from app.logging import get_logger

logger = get_logger(__name__)


class AgentNotFoundError(Exception):
    """Raised when an agentId does not exist."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        super().__init__(f"Agent not found: {agent_id}")


class LifecycleRegistrar(Protocol):
    """Protocol for registering agents with the autonomous lifecycle."""

    async def register_agent(self, agent_id: str) -> None: ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_agent_id() -> str:
    """Cryptographically safe unique agent identifier (UUID4)."""
    return str(uuid.uuid4())


class AgentService:
    """Application logic for agent initialization."""

    def __init__(
        self,
        agents: AgentRepository,
        session: AsyncSession,
        lifecycle: LifecycleRegistrar | None = None,
    ) -> None:
        self._agents = agents
        self._session = session
        self._lifecycle = lifecycle

    async def initialize_agent(self, persona: Persona) -> InitResponse:
        agent_id = new_agent_id()
        now = utc_now()

        agent = await self._agents.create_agent(
            agent_id=agent_id,
            name=persona.name,
            domain=persona.domain,
            status="running",
            created_at=now,
            updated_at=now,
            cycle_count=0,
        )
        # Commit before lifecycle registration so an immediate tick can see the row.
        await self._session.commit()

        logger.info(
            "agent_initialized",
            agent_id=agent.id,
            name=agent.name,
            domain=agent.domain,
            status=agent.status,
        )

        # Register with scheduler without waiting for a generation cycle.
        if self._lifecycle is not None:
            await self._lifecycle.register_agent(agent.id)

        return InitResponse(agent_id=agent.id)

    async def get_agent_or_raise(self, agent_id: str) -> Agent:
        agent = await self._agents.get_by_id(agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        return agent


class FeedService:
    """Read-only feed retrieval. Never generates content."""

    def __init__(self, agents: AgentRepository, posts: PostRepository) -> None:
        self._agents = agents
        self._posts = posts

    async def get_feed(self, agent_id: str) -> FeedResponse:
        agent_id = agent_id.strip()
        if not agent_id:
            raise AgentNotFoundError(agent_id)

        exists = await self._agents.exists(agent_id)
        if not exists:
            raise AgentNotFoundError(agent_id)

        rows = await self._posts.list_by_agent_newest_first(agent_id)
        posts = [self._to_post_response(row) for row in rows]

        logger.info("feed_retrieved", agent_id=agent_id, post_count=len(posts))
        return FeedResponse(posts=posts)

    @staticmethod
    def _to_post_response(post: Post) -> PostResponse:
        sources = json.loads(post.sources_json)
        if not isinstance(sources, list):
            sources = []
        return PostResponse(
            id=post.id,
            created_at=post.created_at,
            text=post.text,
            rationale=post.rationale,
            sources=[str(item) for item in sources],
        )
