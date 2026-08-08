"""Health and readiness endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app.config import get_settings
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

    settings = get_settings()
    if lifecycle is not None and hasattr(lifecycle, "llm_available"):
        llm_status = "available" if lifecycle.llm_available else "unavailable"
    else:
        llm_status = "available" if settings.llm_configured else "unavailable"

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
    settings = get_settings()
    lifecycle = getattr(request.app.state, "lifecycle", None)
    llm_status = (
        "available"
        if (
            (lifecycle is not None and getattr(lifecycle, "llm_available", False))
            or settings.llm_configured
        )
        else "unavailable"
    )
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
