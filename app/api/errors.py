"""HTTP exception handlers for safe API error responses."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.logging import get_logger
from app.services.agent_service import AgentNotFoundError

logger = get_logger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AgentNotFoundError)
    async def agent_not_found_handler(
        _request: Request,
        exc: AgentNotFoundError,
    ) -> JSONResponse:
        logger.info("agent_not_found", agent_id=exc.agent_id)
        return JSONResponse(
            status_code=404,
            content={"detail": "Agent not found"},
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_error_handler(
        _request: Request,
        exc: SQLAlchemyError,
    ) -> JSONResponse:
        logger.exception("database_error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )
