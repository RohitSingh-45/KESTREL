"""Persistence-facing memory operations using domain models."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MemoryItem
from app.db.repositories.memory import MemoryRepository
from app.orchestration.locks import utc_now
from app.services.memory.models import (
    MemoryKindEnum,
    MemoryRecord,
    MemoryValue,
    clamp_unit,
)


def orm_to_record(item: MemoryItem) -> MemoryRecord:
    raw = json.loads(item.value_json or "{}")
    try:
        value = MemoryValue.model_validate(raw)
    except Exception:
        value = MemoryValue(
            kind=MemoryKindEnum(item.kind),
            text=str(raw.get("text") or raw.get("summary") or ""),
            confidence=float(raw.get("confidence") or 0.5),
        )
    return MemoryRecord(
        id=item.id,
        agent_id=item.agent_id,
        kind=MemoryKindEnum(item.kind),
        key=item.key or "",
        value=value,
        salience=float(item.salience),
        confidence=value.confidence,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


class MemoryStore:
    """Thin store adapting repository rows to MemoryRecord domain objects."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = MemoryRepository(session)

    @property
    def repository(self) -> MemoryRepository:
        return self._repo

    async def create(
        self,
        *,
        agent_id: str,
        kind: MemoryKindEnum,
        key: str,
        value: MemoryValue,
        salience: float,
        now: datetime | None = None,
        memory_id: str | None = None,
    ) -> MemoryRecord:
        stamp = now or utc_now()
        item = await self._repo.create(
            memory_id=memory_id or str(uuid.uuid4()),
            agent_id=agent_id,
            kind=kind.value,
            key=key,
            value_json=value.model_dump_json(),
            salience=clamp_unit(salience),
            created_at=stamp,
            updated_at=stamp,
        )
        return orm_to_record(item)

    async def get_by_id(self, memory_id: str) -> MemoryRecord | None:
        item = await self._repo.get_by_id(memory_id)
        return orm_to_record(item) if item else None

    async def find_by_key(
        self,
        agent_id: str,
        *,
        kind: MemoryKindEnum,
        key: str,
    ) -> MemoryRecord | None:
        item = await self._repo.find_by_key(agent_id, kind=kind.value, key=key)
        return orm_to_record(item) if item else None

    async def find_by_kind(self, agent_id: str, kind: MemoryKindEnum) -> list[MemoryRecord]:
        items = await self._repo.find_by_kind(agent_id, kind.value)
        return [orm_to_record(item) for item in items]

    async def list_for_agent(self, agent_id: str) -> list[MemoryRecord]:
        items = await self._repo.list_for_agent(agent_id)
        return [orm_to_record(item) for item in items]

    async def update(
        self,
        memory_id: str,
        *,
        value: MemoryValue | None = None,
        salience: float | None = None,
        key: str | None = None,
        now: datetime | None = None,
    ) -> MemoryRecord | None:
        item = await self._repo.update(
            memory_id,
            value_json=value.model_dump_json() if value is not None else None,
            salience=clamp_unit(salience) if salience is not None else None,
            key=key,
            updated_at=now or utc_now(),
        )
        return orm_to_record(item) if item else None

    async def upsert(
        self,
        *,
        agent_id: str,
        kind: MemoryKindEnum,
        key: str,
        value: MemoryValue,
        salience: float,
        now: datetime | None = None,
        memory_id: str | None = None,
    ) -> tuple[MemoryRecord, bool]:
        stamp = now or utc_now()
        item, created = await self._repo.upsert(
            memory_id=memory_id or str(uuid.uuid4()),
            agent_id=agent_id,
            kind=kind.value,
            key=key,
            value_json=value.model_dump_json(),
            salience=clamp_unit(salience),
            now=stamp,
        )
        return orm_to_record(item), created
