"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

TEST_DB_PATH = Path(__file__).resolve().parent / "test_kestrel.db"

os.environ["APP_ENV"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["AGENT_CYCLE_SECONDS"] = "3600"
os.environ["GENERATION_LEASE_SECONDS"] = "300"
# Module 20 weight defaults (override stale shell SCORE_WEIGHT_* values).
os.environ["SCORE_WEIGHT_RECENCY"] = "0.15"
os.environ["SCORE_WEIGHT_PERSONA_FIT"] = "0.15"
os.environ["SCORE_WEIGHT_IMPORTANCE"] = "0.15"
os.environ["SCORE_WEIGHT_NOVELTY"] = "0.10"
os.environ["SCORE_WEIGHT_SOURCE_QUALITY"] = "0.10"
os.environ["SCORE_WEIGHT_EVIDENCE"] = "0.10"
os.environ["SCORE_WEIGHT_AI_SECURITY"] = "0.25"
os.environ["AI_SECURITY_FILTER_ENABLED"] = "true"

from app.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import close_db, init_db  # noqa: E402
from app.main import create_app  # noqa: E402


def _cleanup_test_db() -> None:
    for path in (
        TEST_DB_PATH,
        Path(str(TEST_DB_PATH) + "-wal"),
        Path(str(TEST_DB_PATH) + "-shm"),
    ):
        try:
            if path.exists():
                path.unlink()
        except PermissionError:
            # Windows may briefly keep handles; next init overwrites schema via create_all.
            pass


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    get_settings.cache_clear()
    await close_db()
    _cleanup_test_db()

    eng = await init_db(get_settings())
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield eng

    await close_db()
    _cleanup_test_db()


@pytest_asyncio.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """HTTP client against the app with an initialized test database."""
    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def lifespan_client() -> AsyncIterator[AsyncClient]:
    """HTTP client that runs the real FastAPI lifespan (DB + scheduler)."""
    get_settings.cache_clear()
    await close_db()
    _cleanup_test_db()

    application = create_app()
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    await close_db()
    _cleanup_test_db()
