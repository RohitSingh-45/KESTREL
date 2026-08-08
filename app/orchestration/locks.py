"""Per-agent concurrency protection: in-memory locks + DB leases."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GenerationLease
from app.logging import get_logger

logger = get_logger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_lease_owner() -> str:
    return f"worker:{uuid.uuid4()}"


class AgentLockManager:
    """Non-blocking in-process lock registry keyed by agent_id."""

    def __init__(self) -> None:
        self._held: set[str] = set()
        self._guard = asyncio.Lock()

    async def try_acquire(self, agent_id: str) -> bool:
        async with self._guard:
            if agent_id in self._held:
                return False
            self._held.add(agent_id)
            return True

    async def release(self, agent_id: str) -> None:
        async with self._guard:
            self._held.discard(agent_id)

    def is_held(self, agent_id: str) -> bool:
        return agent_id in self._held


class GenerationLeaseManager:
    """Database-backed generation leases with TTL recovery."""

    def __init__(self, session: AsyncSession, lease_seconds: int) -> None:
        self._session = session
        self._lease_seconds = lease_seconds

    async def try_acquire(self, agent_id: str, lease_owner: str) -> bool:
        now = utc_now()
        expires_at = now + timedelta(seconds=self._lease_seconds)

        result = await self._session.execute(
            select(GenerationLease).where(GenerationLease.agent_id == agent_id)
        )
        existing = result.scalar_one_or_none()

        if existing is None:
            self._session.add(
                GenerationLease(
                    agent_id=agent_id,
                    lease_owner=lease_owner,
                    expires_at=expires_at,
                )
            )
            await self._session.flush()
            logger.info(
                "lease_acquired",
                agent_id=agent_id,
                lease_owner=lease_owner,
                expires_at=expires_at.isoformat(),
            )
            return True

        existing_expires = existing.expires_at
        if existing_expires.tzinfo is None:
            existing_expires = existing_expires.replace(tzinfo=timezone.utc)

        if existing_expires <= now:
            existing.lease_owner = lease_owner
            existing.expires_at = expires_at
            await self._session.flush()
            logger.info(
                "lease_acquired",
                agent_id=agent_id,
                lease_owner=lease_owner,
                expires_at=expires_at.isoformat(),
                recovered_expired=True,
            )
            return True

        if existing.lease_owner == lease_owner:
            existing.expires_at = expires_at
            await self._session.flush()
            return True

        logger.info(
            "lease_skipped",
            agent_id=agent_id,
            lease_owner=lease_owner,
            held_by=existing.lease_owner,
        )
        return False

    async def release(self, agent_id: str, lease_owner: str) -> None:
        result = await self._session.execute(
            select(GenerationLease).where(GenerationLease.agent_id == agent_id)
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            return
        if existing.lease_owner != lease_owner:
            return
        await self._session.delete(existing)
        await self._session.flush()
        logger.info("lease_released", agent_id=agent_id, lease_owner=lease_owner)

    async def clear_expired(self, *, now: datetime | None = None) -> int:
        now = now or utc_now()
        result = await self._session.execute(
            delete(GenerationLease).where(GenerationLease.expires_at <= now)
        )
        await self._session.flush()
        return int(result.rowcount or 0)

    async def clear_all(self) -> int:
        result = await self._session.execute(delete(GenerationLease))
        await self._session.flush()
        return int(result.rowcount or 0)

    async def get(self, agent_id: str) -> GenerationLease | None:
        result = await self._session.execute(
            select(GenerationLease).where(GenerationLease.agent_id == agent_id)
        )
        return result.scalar_one_or_none()
