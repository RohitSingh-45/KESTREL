"""Module 18 — production configuration and CORS readiness."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


def test_port_and_cors_settings() -> None:
    settings = Settings(
        PORT=9090,
        CORS_ORIGINS="https://app.example.com,https://www.example.com",
        APP_ENV="production",
    )
    assert settings.port == 9090
    assert settings.cors_origin_list == [
        "https://app.example.com",
        "https://www.example.com",
    ]


def test_database_url_postgres_normalization() -> None:
    settings = Settings(
        DATABASE_URL="postgres://user:pass@host:5432/db",
        APP_ENV="production",
        CORS_ORIGINS="https://x.example",
    )
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.is_postgres is True
    assert settings.is_sqlite is False


def test_production_empty_cors_is_same_origin() -> None:
    settings = Settings(APP_ENV="production", CORS_ORIGINS="")
    assert settings.cors_origin_list == []


def test_app_imports_and_health_shape(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    # Import path used by uvicorn
    from app.main import app

    assert app.title == "Kestrel"


def test_cors_headers_in_development(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CORS_ORIGINS", "")
    get_settings.cache_clear()
    application = create_app()
    client = TestClient(application)
    response = client.options(
        "/health",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code in {200, 204}
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
    get_settings.cache_clear()
