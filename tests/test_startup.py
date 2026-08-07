"""Basic application startup tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.main import create_app


def test_create_app_returns_fastapi() -> None:
    application = create_app()
    assert application.title == "Kestrel"
    paths = set(application.openapi()["paths"])
    assert "/health" in paths
    assert "/api/agent/init" in paths
    assert "/api/agent/feed" in paths


@pytest.mark.asyncio
async def test_lifespan_starts_and_health_works(lifespan_client: AsyncClient) -> None:
    response = await lifespan_client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["db"] == "ok"
    assert payload["scheduler"] == "running"
