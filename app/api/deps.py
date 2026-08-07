"""FastAPI dependency injection wiring."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.agents import AgentRepository
from app.db.repositories.posts import PostRepository
from app.db.session import get_session_factory
from app.services.agent_service import AgentService, FeedService


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a short-lived session; commit on success, rollback on error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_agent_service(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> AgentService:
    lifecycle = getattr(request.app.state, "lifecycle", None)
    return AgentService(
        AgentRepository(session),
        session=session,
        lifecycle=lifecycle,
    )


async def get_feed_service(
    session: AsyncSession = Depends(get_db_session),
) -> FeedService:
    return FeedService(AgentRepository(session), PostRepository(session))
