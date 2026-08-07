"""Integration checks for LLM configuration and app startup."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.config import get_settings
from app.main import create_app
from app.services.llm.openai_client import build_llm_client


def test_app_starts_without_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.openai_api_key is None
    client = build_llm_client(api_key=settings.openai_api_key, model=settings.openai_model)
    assert client.available is False
    app = create_app()
    assert app.title == "Kestrel"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_health_includes_llm_field_without_breaking(lifespan_client: AsyncClient) -> None:
    response = await lifespan_client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["db"] == "ok"
    assert payload["scheduler"] == "running"
    assert payload["llm"] in {"available", "unavailable"}
