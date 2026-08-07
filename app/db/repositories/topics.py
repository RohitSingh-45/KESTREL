"""Topic persistence operations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Topic


class TopicRepository:
    """Data-access layer for topics. No business logic."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_topic(
        self,
        *,
        topic_id: str,
        agent_id: str,
        title: str,
        status: str,
        created_at: datetime,
        scores_json: str | None = None,
        debate_json: str | None = None,
        consistency_json: str | None = None,
        embedding_json: str | None = None,
        decided_at: datetime | None = None,
    ) -> Topic:
        topic = Topic(
            id=topic_id,
            agent_id=agent_id,
            title=title,
            status=status,
            scores_json=scores_json,
            debate_json=debate_json,
            consistency_json=consistency_json,
            embedding_json=embedding_json,
            created_at=created_at,
            decided_at=decided_at,
        )
        self._session.add(topic)
        await self._session.flush()
        return topic

    async def list_by_agent(
        self,
        agent_id: str,
        *,
        status: str | None = None,
    ) -> list[Topic]:
        stmt = select(Topic).where(Topic.agent_id == agent_id)
        if status is not None:
            stmt = stmt.where(Topic.status == status)
        stmt = stmt.order_by(Topic.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_agent(self, agent_id: str, *, status: str | None = None) -> int:
        return len(await self.list_by_agent(agent_id, status=status))

    async def get_by_id(self, topic_id: str) -> Topic | None:
        result = await self._session.execute(select(Topic).where(Topic.id == topic_id))
        return result.scalar_one_or_none()

    async def update_topic(
        self,
        topic_id: str,
        *,
        status: str | None = None,
        scores_json: str | None = None,
        debate_json: str | None = None,
        consistency_json: str | None = None,
        decided_at: datetime | None = None,
    ) -> Topic | None:
        topic = await self.get_by_id(topic_id)
        if topic is None:
            return None
        if status is not None:
            topic.status = status
        if scores_json is not None:
            topic.scores_json = scores_json
        if debate_json is not None:
            topic.debate_json = debate_json
        if consistency_json is not None:
            topic.consistency_json = consistency_json
        if decided_at is not None:
            topic.decided_at = decided_at
        await self._session.flush()
        return topic
