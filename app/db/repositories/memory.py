"""Memory item persistence operations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MemoryItem


class MemoryRepository:
    """Data-access layer for memory_items. No business logic."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        memory_id: str,
        agent_id: str,
        kind: str,
        key: str | None,
        value_json: str,
        salience: float,
        created_at: datetime,
        updated_at: datetime,
    ) -> MemoryItem:
        item = MemoryItem(
            id=memory_id,
            agent_id=agent_id,
            kind=kind,
            key=key,
            value_json=value_json,
            salience=salience,
            created_at=created_at,
            updated_at=updated_at,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_by_id(self, memory_id: str) -> MemoryItem | None:
        result = await self._session.execute(
            select(MemoryItem).where(MemoryItem.id == memory_id)
        )
        return result.scalar_one_or_none()

    async def find_by_key(
        self,
        agent_id: str,
        *,
        kind: str,
        key: str,
    ) -> MemoryItem | None:
        result = await self._session.execute(
            select(MemoryItem).where(
                MemoryItem.agent_id == agent_id,
                MemoryItem.kind == kind,
                MemoryItem.key == key,
            )
        )
        return result.scalar_one_or_none()

    async def find_by_kind(self, agent_id: str, kind: str) -> list[MemoryItem]:
        result = await self._session.execute(
            select(MemoryItem)
            .where(MemoryItem.agent_id == agent_id, MemoryItem.kind == kind)
            .order_by(MemoryItem.updated_at.desc())
        )
        return list(result.scalars().all())

    async def list_for_agent(self, agent_id: str) -> list[MemoryItem]:
        result = await self._session.execute(
            select(MemoryItem)
            .where(MemoryItem.agent_id == agent_id)
            .order_by(MemoryItem.updated_at.desc())
        )
        return list(result.scalars().all())

    async def update(
        self,
        memory_id: str,
        *,
        value_json: str | None = None,
        salience: float | None = None,
        key: str | None = None,
        updated_at: datetime | None = None,
    ) -> MemoryItem | None:
        item = await self.get_by_id(memory_id)
        if item is None:
            return None
        if value_json is not None:
            item.value_json = value_json
        if salience is not None:
            item.salience = salience
        if key is not None:
            item.key = key
        if updated_at is not None:
            item.updated_at = updated_at
        await self._session.flush()
        return item

    async def upsert(
        self,
        *,
        memory_id: str,
        agent_id: str,
        kind: str,
        key: str,
        value_json: str,
        salience: float,
        now: datetime,
    ) -> tuple[MemoryItem, bool]:
        """Insert or update by (agent_id, kind, key). Returns (item, created)."""
        existing = await self.find_by_key(agent_id, kind=kind, key=key)
        if existing is None:
            item = await self.create(
                memory_id=memory_id,
                agent_id=agent_id,
                kind=kind,
                key=key,
                value_json=value_json,
                salience=salience,
                created_at=now,
                updated_at=now,
            )
            return item, True
        existing.value_json = value_json
        existing.salience = salience
        existing.updated_at = now
        await self._session.flush()
        return existing, False
