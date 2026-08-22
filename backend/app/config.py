"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_ROOT / "data"
SEEDS_DIR = DATA_DIR / "seeds"
VAR_DIR = BACKEND_ROOT / "var"

DataMode = Literal["live", "synthetic", "hybrid"]
JobBackend = Literal["inline", "celery"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "ChainWatch"
    app_env: Literal["dev", "test", "prod"] = "dev"
    debug: bool = True
    api_prefix: str = "/api/v1"

    # --- storage -------------------------------------------------------
    database_url: str = f"sqlite+aiosqlite:///{(VAR_DIR / 'chainwatch.db').as_posix()}"
    redis_url: str = "redis://localhost:6379/0"
    # When Redis is unreachable the cache/rate-limiter degrade to in-process
    # equivalents instead of failing the request.
    redis_required: bool = False

    # --- auth ----------------------------------------------------------
    secret_key: str = "dev-insecure-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 12 * 60
    bootstrap_admin_email: str = "admin@chainwatch.gov.in"
    bootstrap_admin_password: str = "ChangeMe#123"

    # --- data sourcing -------------------------------------------------
    data_mode: DataMode = "hybrid"
    etherscan_api_key: str | None = None
    trongrid_api_key: str | None = None
    blockchair_api_key: str | None = None
    http_timeout_seconds: float = 20.0
    upstream_cache_ttl_seconds: int = 900

    # Free tier: Etherscan 3 rps, TronGrid ~15 rps, Blockchair ~2 rps.
    etherscan_rate_per_sec: float = 3.0
    trongrid_rate_per_sec: float = 10.0
    blockchair_rate_per_sec: float = 2.0

    # --- tracing defaults ----------------------------------------------
    trace_max_hops: int = 5
    trace_max_fanout: int = 25
    trace_min_taint: float = 0.01
    trace_max_nodes: int = 1500
    vasp_terminal_confidence: float = 0.75

    # --- jobs ----------------------------------------------------------
    job_backend: JobBackend = "inline"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    # --- integrations ---------------------------------------------------
    ncrp_webhook_token: str = "ncrp-dev-token"
    reports_dir: Path = VAR_DIR / "reports"

    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
        ]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass
            return [item.strip().strip("[]\"'") for item in text.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _resolve_sqlite_path(self) -> Settings:
        """Keep the SQLite file under the backend tree regardless of cwd."""
        if not self.database_url.startswith("sqlite"):
            return self
        scheme, _, rest = self.database_url.partition(":///")
        if not rest or rest.startswith("/") or (len(rest) > 1 and rest[1] == ":"):
            return self
        resolved = (BACKEND_ROOT / rest).resolve()
        object.__setattr__(self, "database_url", f"{scheme}:///{resolved.as_posix()}")
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    def ensure_dirs(self) -> None:
        VAR_DIR.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


settings = get_settings()
