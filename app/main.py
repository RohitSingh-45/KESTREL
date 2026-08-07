"""FastAPI application entrypoint for Kestrel."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.routes.agent import router as agent_router
from app.api.routes.health import router as health_router
from app.config import get_settings
from app.db.base import Base
from app.db.session import close_db, get_engine, init_db, verify_db
from app.logging import configure_logging, get_logger
from app.orchestration.lifecycle import AgentLifecycleService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize DB + autonomous lifecycle; shut down cleanly."""
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger(__name__)

    log.info(
        "application_startup",
        version=__version__,
        app_env=settings.app_env,
        agent_cycle_seconds=settings.agent_cycle_seconds,
        generation_lease_seconds=settings.generation_lease_seconds,
    )

    await init_db(settings)
    # Tests use metadata.create_all; production uses Alembic migrations.
    if settings.app_env == "test":
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    await verify_db()

    lifecycle = AgentLifecycleService(settings)
    await lifecycle.start()
    app.state.lifecycle = lifecycle

    yield

    log.info("application_shutdown")
    await lifecycle.stop()
    app.state.lifecycle = None
    await close_db()


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()
    configure_logging(settings.log_level)

    application = FastAPI(
        title="Kestrel",
        description="Autonomous AI Security Persona",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.lifecycle = None
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(agent_router)
    return application


app = create_app()
