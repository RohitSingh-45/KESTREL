"""Async SQLAlchemy engine and session management."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings
from app.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def ensure_sqlite_directory(settings: Settings) -> None:
    """Create the SQLite parent directory when using a file-backed database."""
    path = settings.sqlite_path()
    if path is None:
        return
    directory = path.parent
    if str(directory) not in ("", "."):
        directory.mkdir(parents=True, exist_ok=True)


def _attach_sqlite_pragmas(
    engine: AsyncEngine,
    *,
    busy_timeout_ms: int = 5000,
) -> None:
    """Enable WAL mode, foreign keys, and busy timeout on each SQLite connection."""

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        cursor.close()


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine is not initialized. Call init_db() first.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Session factory is not initialized. Call init_db() first.")
    return _session_factory


async def init_db(settings: Settings | None = None) -> AsyncEngine:
    """Create engine/session factory and ensure SQLite data directory exists."""
    global _engine, _session_factory

    settings = settings or get_settings()
    ensure_sqlite_directory(settings)

    logger.info(
        "database_initializing",
        database_url=_redact_url(settings.database_url),
        app_env=settings.app_env,
    )

    engine_kwargs: dict[str, object] = {
        "echo": False,
        "pool_pre_ping": True,
    }
    if settings.is_postgres:
        # Bounded pool for long-lived PaaS web processes (48h+ uptime).
        engine_kwargs.update(
            {
                "pool_size": 5,
                "max_overflow": 10,
                "pool_recycle": 1800,
            }
        )

    engine = create_async_engine(settings.database_url, **engine_kwargs)

    if settings.is_sqlite:
        _attach_sqlite_pragmas(
            engine,
            busy_timeout_ms=settings.sqlite_busy_timeout_ms,
        )

    _engine = engine
    _session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    logger.info("database_initialized")
    return engine


async def verify_db() -> None:
    """Verify the database is reachable with a short transaction."""
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await session.execute(text("SELECT 1"))
    logger.info("database_verified")


async def verify_schema() -> bool:
    """Return True when core application tables exist (agents)."""
    factory = get_session_factory()
    try:
        async with factory() as session:
            async with session.begin():
                await session.execute(text("SELECT 1 FROM agents LIMIT 1"))
        return True
    except Exception:
        logger.warning("database_schema_missing", table="agents")
        return False


async def close_db() -> None:
    """Dispose engine and clear globals."""
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
        logger.info("database_disposed")
    _engine = None
    _session_factory = None


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a short-lived session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


def _redact_url(url: str) -> str:
    """Avoid logging credentials if present in future non-SQLite URLs."""
    if "@" not in url:
        return url
    scheme, rest = url.split("://", maxsplit=1)
    return f"{scheme}://***@{rest.split('@', maxsplit=1)[-1]}"


def resolve_data_dir(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = settings.sqlite_path()
    if path is None:
        return Path("./data")
    return path.parent
