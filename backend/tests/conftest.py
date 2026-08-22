"""Shared test fixtures.

Every test runs against a throwaway SQLite file with the synthetic dataset
seeded once per session, so tests exercise the same code path the demo uses.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

TEST_DB = Path(tempfile.gettempdir()) / "chainwatch_test.db"

os.environ.setdefault("APP_ENV", "test")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["DATA_MODE"] = "synthetic"
os.environ["JOB_BACKEND"] = "inline"
os.environ["REDIS_REQUIRED"] = "false"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "TestAdmin#123"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def _fresh_database():
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{TEST_DB}{suffix}")
        if candidate.exists():
            candidate.unlink()
    yield
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{TEST_DB}{suffix}")
        if candidate.exists():
            try:
                candidate.unlink()
            except PermissionError:
                pass


@pytest.fixture(scope="session")
async def seeded_database(_fresh_database) -> AsyncIterator[None]:
    from app.db.bootstrap import init_database
    from app.db.session import dispose_engine, session_scope
    from app.seeds.runner import seed_all

    await init_database()
    async with session_scope() as session:
        await seed_all(session, reset_synthetic=True)
    yield
    await dispose_engine()


@pytest.fixture
async def seeded_session(seeded_database) -> AsyncIterator:
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        yield session


@pytest.fixture
async def client(seeded_database) -> AsyncIterator:
    """HTTP client bound to the app, already authenticated as the seed admin."""
    import httpx

    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        # Trigger the lifespan-equivalent bootstrap without a second seed pass.
        response = await http.post(
            "/api/v1/auth/login",
            json={"email": "admin@chainwatch.gov.in", "password": "TestAdmin#123"},
        )
        response.raise_for_status()
        http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        yield http


@pytest.fixture
def anyio_backend():
    return "asyncio"
