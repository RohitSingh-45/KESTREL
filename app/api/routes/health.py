"""Health check endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

from app.config import get_settings
from app.db.session import get_session_factory
from app.logging import get_logger

router = APIRouter(tags=["health"])
logger = get_logger(__name__)


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Return process, database, and scheduler readiness."""
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

    overall = "ok"
    if db_status != "ok":
        overall = "degraded"
    elif scheduler_status != "running":
        overall = "ok" if scheduler_status == "not_started" else "degraded"

    response = {
        "status": overall,
        "db": db_status,
        "scheduler": scheduler_status,
        "llm": llm_status,
    }
    logger.info("health_check", **response)
    return response
