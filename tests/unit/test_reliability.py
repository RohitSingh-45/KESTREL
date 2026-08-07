"""Unit tests for Module 11 reliability helpers."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from io import StringIO

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.db.repositories.agents import AgentRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.session import get_session_factory
from app.logging import configure_logging, get_logger, redact_secrets
from app.orchestration.locks import GenerationLeaseManager, utc_now
from app.services.reliability.recovery import recover_stale_runtime_state
from app.services.reliability.retry_policy import (
    LLM_STRUCTURED,
    RESEARCH_CONNECTOR,
    llm_structured_max_http_calls,
    llm_transport_max_attempts,
    research_connector_max_attempts,
)
from app.services.reliability.secrets import REDACTED, sanitize_error, sanitize_text


def test_config_rejects_invalid_cycle_and_log_level() -> None:
    with pytest.raises(ValidationError):
        Settings(AGENT_CYCLE_SECONDS=0)
    with pytest.raises(ValidationError):
        Settings(GENERATION_LEASE_SECONDS=-1)
    with pytest.raises(ValidationError):
        Settings(LOG_LEVEL="VERBOSE")
    with pytest.raises(ValidationError):
        Settings(OPENAI_MODEL="   ")
    with pytest.raises(ValidationError):
        Settings(OPENAI_TIMEOUT_SECONDS=0)
    with pytest.raises(ValidationError):
        Settings(RESEARCH_MAX_ITEMS=0)
    ok = Settings(AGENT_CYCLE_SECONDS=30, LOG_LEVEL="debug", OPENAI_MODEL=" gpt-4o-mini ")
    assert ok.agent_cycle_seconds == 30
    assert ok.log_level == "DEBUG"
    assert ok.openai_model == "gpt-4o-mini"


def test_retry_budgets_prevent_nested_explosion() -> None:
    assert llm_transport_max_attempts(3) == 3
    assert llm_transport_max_attempts(0) == 1
    # structured = transport * (1 + 1 repair) — never multiplies by research retries
    assert llm_structured_max_http_calls(3) == 6
    assert research_connector_max_attempts() == 1
    assert RESEARCH_CONNECTOR.max_attempts == 1
    assert LLM_STRUCTURED.structured_repair_attempts == 1


def test_secret_sanitization() -> None:
    key = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    text = f"Authorization: Bearer abc.def.ghi failed with key={key}"
    cleaned = sanitize_text(text)
    assert key not in cleaned
    assert "Bearer abc.def.ghi" not in cleaned
    assert REDACTED in cleaned
    assert key not in sanitize_error(RuntimeError(f"boom {key}"))


def test_structlog_redacts_sensitive_fields() -> None:
    event = {
        "event": "test",
        "openai_api_key": "sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "authorization": "Bearer tokensecret",
        "message": "using sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "level": "info",
    }
    out = dict(redact_secrets(logging.getLogger("t"), "info", event))
    assert out["openai_api_key"] == REDACTED
    assert out["authorization"] == REDACTED
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in out["message"]


def test_configure_logging_never_emits_api_key() -> None:
    stream = StringIO()
    configure_logging("INFO")
    root = logging.getLogger()
    previous = list(root.handlers)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.handlers = [handler]
    try:
        log = get_logger("secret-test")
        secret = "sk-TESTSECRETKEYVALUE1234567890ABCD"
        log.info("cycle_failed", openai_api_key=secret, error=f"auth {secret}")
        payload = stream.getvalue()
        assert secret not in payload
        line = payload.strip().splitlines()[-1]
        data = json.loads(line)
        assert data.get("openai_api_key") == REDACTED
        assert secret not in data.get("error", "")
    finally:
        root.handlers = previous


@pytest.mark.asyncio
async def test_stale_pipeline_run_recovery(engine: AsyncEngine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id="stale-agent",
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        await PipelineRunRepository(session).create_run(
            run_id="stale-run-1",
            agent_id="stale-agent",
            started_at=now - timedelta(minutes=10),
            status="running",
            stage="publishing",
        )
        await session.commit()

    async with factory() as session:
        result = await recover_stale_runtime_state(session, lease_seconds=300)
        await session.commit()
        assert result["stale_pipeline_runs"] == 1
        run = await PipelineRunRepository(session).get_by_id("stale-run-1")
        assert run is not None
        assert run.status == "failed"
        assert run.finished_at is not None


@pytest.mark.asyncio
async def test_expired_lease_cleared_on_recovery(engine: AsyncEngine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id="lease-agent",
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        leases = GenerationLeaseManager(session, 300)
        assert await leases.try_acquire("lease-agent", "worker:old") is True
        lease = await leases.get("lease-agent")
        assert lease is not None
        lease.expires_at = now - timedelta(seconds=5)
        await session.commit()

    async with factory() as session:
        result = await recover_stale_runtime_state(session, lease_seconds=300)
        await session.commit()
        assert result["expired_leases_cleared"] == 1
        assert await GenerationLeaseManager(session, 300).get("lease-agent") is None


@pytest.mark.asyncio
async def test_active_lease_not_cleared_by_recovery(engine: AsyncEngine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id="active-lease",
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        leases = GenerationLeaseManager(session, 300)
        assert await leases.try_acquire("active-lease", "worker:live") is True
        await session.commit()

    async with factory() as session:
        result = await recover_stale_runtime_state(session, lease_seconds=300)
        await session.commit()
        assert result["expired_leases_cleared"] == 0
        leases = GenerationLeaseManager(session, 300)
        lease = await leases.get("active-lease")
        assert lease is not None
        assert lease.lease_owner == "worker:live"
        # Active lease still rejects other owners.
        assert await leases.try_acquire("active-lease", "worker:thief") is False


@pytest.mark.asyncio
async def test_sqlite_busy_timeout_pragma(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        value = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
    assert int(value) == 5000


@pytest.mark.asyncio
async def test_db_rollback_then_next_op_succeeds(engine: AsyncEngine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id="rollback-agent",
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        await session.commit()

    async with factory() as session:
        try:
            await AgentRepository(session).create_agent(
                agent_id="rollback-agent",
                name="Dup",
                domain="AI Security",
                status="running",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            await session.commit()
        except Exception:
            await session.rollback()

    async with factory() as session:
        agent = await AgentRepository(session).get_by_id("rollback-agent")
        assert agent is not None
        assert agent.name == "Kestrel"
        await session.execute(text("SELECT 1"))
