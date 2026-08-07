"""Agent persistence operations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent


class AgentRepository:
    """Data-access layer for agents. No business logic."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_agent(
        self,
        *,
        agent_id: str,
        name: str,
        domain: str,
        status: str,
        created_at: datetime,
        updated_at: datetime,
        cycle_count: int = 0,
        config_json: str | None = None,
    ) -> Agent:
        agent = Agent(
            id=agent_id,
            name=name,
            domain=domain,
            status=status,
            created_at=created_at,
            updated_at=updated_at,
            last_cycle_at=None,
            cycle_count=cycle_count,
            config_json=config_json,
        )
        self._session.add(agent)
        await self._session.flush()
        return agent

    async def get_by_id(self, agent_id: str) -> Agent | None:
        result = await self._session.execute(select(Agent).where(Agent.id == agent_id))
        return result.scalar_one_or_none()

    async def exists(self, agent_id: str) -> bool:
        result = await self._session.execute(
            select(func.count()).select_from(Agent).where(Agent.id == agent_id)
        )
        return int(result.scalar_one()) > 0

    async def count(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(Agent))
        return int(result.scalar_one())

    async def list_by_status(self, status: str) -> list[Agent]:
        result = await self._session.execute(select(Agent).where(Agent.status == status))
        return list(result.scalars().all())

    async def list_all(self, *, limit: int = 50) -> list[Agent]:
        result = await self._session.execute(
            select(Agent).order_by(Agent.created_at.desc()).limit(max(1, limit))
        )
        return list(result.scalars().all())

    async def record_cycle_success(
        self,
        agent_id: str,
        *,
        last_cycle_at: datetime,
        updated_at: datetime,
    ) -> Agent | None:
        agent = await self.get_by_id(agent_id)
        if agent is None:
            return None
        agent.last_cycle_at = last_cycle_at
        agent.cycle_count = int(agent.cycle_count) + 1
        agent.updated_at = updated_at
        await self._session.flush()
        return agent
