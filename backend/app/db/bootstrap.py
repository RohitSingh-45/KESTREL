"""First-run database bootstrap: schema creation and the seed admin account."""

from __future__ import annotations

from sqlalchemy import select

from app.config import settings
from app.core.enums import UserRole
from app.core.security import hash_password
from app.db import models  # noqa: F401 - ensures all tables are registered
from app.db.base import Base
from app.db.session import get_engine, session_scope
from app.logging_config import get_logger

logger = get_logger(__name__)


async def create_schema() -> None:
    """Create any missing tables.

    Alembic owns migrations for real deployments; this keeps `uvicorn app.main:app`
    usable with zero setup steps on a fresh checkout.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("schema_ready", tables=len(Base.metadata.tables))


async def ensure_bootstrap_admin() -> None:
    async with session_scope() as session:
        email = settings.bootstrap_admin_email.lower().strip()
        existing = await session.execute(
            select(models.User).where(models.User.email == email)
        )
        if existing.scalar_one_or_none() is not None:
            return
        session.add(
            models.User(
                email=email,
                full_name="System Administrator",
                hashed_password=hash_password(settings.bootstrap_admin_password),
                role=UserRole.ADMIN,
                agency="I4C",
            )
        )
        logger.info("bootstrap_admin_created", email=email)


async def init_database() -> None:
    await create_schema()
    await ensure_bootstrap_admin()
