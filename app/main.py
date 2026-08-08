"""FastAPI application entrypoint for Kestrel."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.routes.agent import router as agent_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.config import get_settings
from app.db.base import Base
from app.db.session import close_db, get_engine, init_db, verify_db, verify_schema
from app.logging import configure_logging, get_logger
from app.orchestration.lifecycle import AgentLifecycleService

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _run_alembic_upgrade(*, reason: str) -> None:
    """Apply Alembic migrations to head via subprocess.

    Must not call alembic ``command.upgrade`` inside the FastAPI event loop:
    ``alembic/env.py`` uses ``asyncio.run``, which cannot nest.
    """
    import subprocess
    import sys

    settings = get_settings()
    log = get_logger(__name__)
    root = Path(__file__).resolve().parent.parent
    log.info(
        "migrations_starting",
        app_env=settings.app_env,
        reason=reason,
    )
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(root),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade failed with exit code {completed.returncode}"
        )
    log.info("migrations_completed", reason=reason)


def _maybe_run_migrations() -> None:
    """Optionally apply Alembic migrations during startup (PaaS convenience)."""
    settings = get_settings()
    if not settings.run_migrations_on_startup:
        return
    _run_alembic_upgrade(reason="run_migrations_on_startup")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize DB + autonomous lifecycle; keep process alive on partial failures.

    A dead process behind a reverse proxy becomes HTTP 502 for the browser.
    Database/LLM issues must degrade readiness, not prevent liveness.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger(__name__)
    app.state.lifecycle = None
    app.state.db_ready = False

    log.info(
        "application_startup",
        version=__version__,
        app_env=settings.app_env,
        port=settings.port,
        agent_cycle_seconds=settings.agent_cycle_seconds,
        generation_lease_seconds=settings.generation_lease_seconds,
        cors_origins_configured=bool(settings.cors_origins.strip()),
        frontend_dist_present=_FRONTEND_DIST.is_dir(),
    )

    try:
        _maybe_run_migrations()
    except Exception:
        log.exception("startup_migrations_failed")
        # Continue — /ready will report DB issues; /health stays up.

    try:
        await init_db(settings)
        if settings.app_env == "test":
            async with get_engine().begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        await verify_db()

        if settings.app_env != "test" and not await verify_schema():
            # Plain `uvicorn` without alembic leaves an empty SQLite file —
            # dashboard then 500s on /api/dashboard/agents. Auto-heal once.
            log.warning("schema_missing_attempting_migrations")
            try:
                _run_alembic_upgrade(reason="schema_missing")
            except Exception:
                log.exception("schema_migrations_failed")

        schema_ok = settings.app_env == "test" or await verify_schema()
        app.state.db_ready = schema_ok
        log.info(
            "database_connection_status",
            status="ok" if schema_ok else "schema_missing",
        )
    except Exception:
        app.state.db_ready = False
        log.exception("database_connection_status", status="error")

    if app.state.db_ready:
        try:
            lifecycle = AgentLifecycleService(settings)
            await lifecycle.start()
            app.state.lifecycle = lifecycle
            log.info(
                "lifecycle_started",
                scheduler="running",
                llm="available" if lifecycle.llm_available else "unavailable",
            )
        except Exception:
            app.state.lifecycle = None
            log.exception("lifecycle_start_failed")
    else:
        log.warning(
            "lifecycle_skipped",
            reason="database_not_ready",
        )

    yield

    log.info("application_shutdown")
    lifecycle = getattr(app.state, "lifecycle", None)
    if lifecycle is not None:
        try:
            await lifecycle.stop()
        except Exception:
            log.exception("lifecycle_stop_failed")
    app.state.lifecycle = None
    try:
        await close_db()
    except Exception:
        log.exception("database_shutdown_failed")
    log.info("application_shutdown_complete")


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
    application.state.db_ready = False

    origins = settings.cors_origin_list
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
            max_age=600,
        )

    register_exception_handlers(application)

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Preserve FastAPI/Starlette HTTP + validation errors; JSON-500 everything else.
        if isinstance(exc, StarletteHTTPException):
            return await http_exception_handler(request, exc)
        if isinstance(exc, RequestValidationError):
            return await request_validation_exception_handler(request, exc)
        if isinstance(exc, HTTPException):
            return await http_exception_handler(request, exc)
        log = get_logger(__name__)
        log.exception(
            "unhandled_request_exception",
            path=str(request.url.path),
            method=request.method,
            error=str(exc),
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "service": "kestrel"},
        )

    application.include_router(health_router)
    application.include_router(agent_router)
    application.include_router(dashboard_router)

    # Operator UI (built assets). Evaluator APIs remain unchanged.
    if _FRONTEND_DIST.is_dir():
        assets = _FRONTEND_DIST / "assets"
        if assets.is_dir():
            application.mount(
                "/assets",
                StaticFiles(directory=str(assets)),
                name="dashboard-assets",
            )

        @application.get("/", include_in_schema=False)
        async def dashboard_index() -> FileResponse:
            return FileResponse(_FRONTEND_DIST / "index.html")

        @application.get("/dashboard", include_in_schema=False)
        async def dashboard_alias() -> FileResponse:
            return FileResponse(_FRONTEND_DIST / "index.html")

    return application


app = create_app()
