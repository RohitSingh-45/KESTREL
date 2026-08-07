"""Post persistence operations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Post


class PostRepository:
    """Data-access layer for posts. No business logic."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_agent_newest_first(self, agent_id: str) -> list[Post]:
        result = await self._session.execute(
            select(Post)
            .where(Post.agent_id == agent_id)
            .order_by(desc(Post.created_at), desc(Post.id))
        )
        return list(result.scalars().all())

    async def get_by_id(self, post_id: str) -> Post | None:
        result = await self._session.execute(select(Post).where(Post.id == post_id))
        return result.scalar_one_or_none()

    async def get_by_agent_content_hash(
        self,
        agent_id: str,
        content_hash: str,
    ) -> Post | None:
        result = await self._session.execute(
            select(Post).where(
                Post.agent_id == agent_id,
                Post.content_hash == content_hash,
            )
        )
        return result.scalar_one_or_none()

    async def create_post(
        self,
        *,
        post_id: str,
        agent_id: str,
        created_at: datetime,
        text: str,
        rationale: str,
        sources_json: str,
        content_hash: str,
        topic: str | None = None,
    ) -> Post:
        """Repository capability for later pipeline modules. Not exposed via API."""
        post = Post(
            id=post_id,
            agent_id=agent_id,
            created_at=created_at,
            text=text,
            rationale=rationale,
            sources_json=sources_json,
            topic=topic,
            content_hash=content_hash,
        )
        self._session.add(post)
        await self._session.flush()
        return post
