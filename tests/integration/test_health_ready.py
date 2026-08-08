"""Health / readiness probes for production monitoring."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_ok(client: AsyncClient) -> None:
    res = await client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["service"] == "kestrel"
    assert "db" in body
    assert "llm" in body


@pytest.mark.asyncio
async def test_ready_ok(client: AsyncClient) -> None:
    res = await client.get("/ready")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["service"] == "kestrel"


@pytest.mark.asyncio
async def test_health_alive_without_openai_key(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    res = await client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["llm"] in {"available", "unavailable"}


@pytest.mark.asyncio
async def test_ready_fails_when_schema_missing(
    client: AsyncClient,
    engine,
) -> None:
    """SELECT 1 alone is not readiness — core tables must exist."""
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS agents"))

    res = await client.get("/ready")
    assert res.status_code == 503
    body = res.json()
    assert body["status"] == "unavailable"
    assert body["db"] == "error"
