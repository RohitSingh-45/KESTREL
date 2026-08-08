"""Health and readiness endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app.db.session import get_session_factory
from app.logging import get_logger

router = APIRouter(tags=["health"])
logger = get_logger(__name__)


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Liveness probe — HTTP 200 whenever this process can answer.

    Does NOT fail when the LLM provider is unavailable.
    Use /ready for database readiness (may return 503).
    """
    db_status = "ok"
    try:
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                await session.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"
        logger.exception("health_check_db_failed")

    lifecycle = getattr(request.app.state, "lifecycle", None)
    if lifecycle is not None and getattr(lifecycle, "is_scheduler_running", False):
        scheduler_status = "running"
    else:
        scheduler_status = "not_started"

    # Truthful probe-backed status (ready only after real connectivity success).
    if lifecycle is not None and getattr(lifecycle, "llm_status", None):
        llm_status = str(lifecycle.llm_status)
    else:
        llm_status = "unavailable"

    response = {
        "status": "ok",
        "service": "kestrel",
        "db": db_status,
        "scheduler": scheduler_status,
        "llm": llm_status,
    }
    logger.info("health_check", **response)
    return response


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, Any]:
    """Readiness probe — fails when the database/schema is unreachable."""
    lifecycle = getattr(request.app.state, "lifecycle", None)
    if lifecycle is not None and getattr(lifecycle, "llm_status", None):
        llm_status = str(lifecycle.llm_status)
    else:
        llm_status = "unavailable"
    try:
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                await session.execute(text("SELECT 1"))
                # Empty SQLite files pass SELECT 1 but have no app tables.
                await session.execute(text("SELECT 1 FROM agents LIMIT 1"))
    except Exception:
        logger.exception("readiness_check_failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unavailable",
            "service": "kestrel",
            "db": "error",
            "llm": llm_status,
            "detail": "database or schema not ready",
        }
    return {
        "status": "ok",
        "service": "kestrel",
        "db": "ok",
        "llm": llm_status,
    }
