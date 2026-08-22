"""ChainWatch API entrypoint."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.chains.resolver import registry
from app.config import settings
from app.core.cache import cache
from app.core.errors import register_exception_handlers
from app.db.bootstrap import init_database
from app.db.session import dispose_engine
from app.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

DESCRIPTION = """
Real-Time Crypto Fraud Attribution System.

Traces victim-reported wallet addresses across Bitcoin, EVM chains and TRON,
identifies the nearest receiving exchange or VASP, scores laundering risk and
produces law-enforcement-ready investigation packets.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info(
        "startup",
        env=settings.app_env,
        data_mode=settings.data_mode,
        job_backend=settings.job_backend,
    )
    # #region agent log
    try:
        import json as _json, sys as _sys, time as _time
        from pathlib import Path as _Path
        _p = _Path(r"C:\Users\ROHIT\OneDrive\Desktop\HANUMAN\debug-41078f.log")
        _p.open("a", encoding="utf-8").write(_json.dumps({"sessionId":"41078f","hypothesisId":"A","location":"main.py:lifespan","message":"backend_startup","data":{"cwd":str(_Path.cwd()),"python":_sys.executable,"db_url":settings.database_url.split("://",1)[0],"is_sqlite":settings.is_sqlite,"cors":settings.cors_origins,"api_prefix":settings.api_prefix},"timestamp":int(_time.time()*1000)})+"\n")
    except Exception:
        pass
    # #endregion
    await init_database()
    warm_caches()
    yield
    await cache.close()
    await registry.close()
    await dispose_engine()
    logger.info("shutdown_complete")


def warm_caches() -> None:
    """Pay one-time import costs at startup rather than in the first trace.

    Loading the entity classifier takes a couple of seconds. Doing it lazily
    means whoever runs the first trace waits for it, which on a live demo is
    exactly the wrong moment.
    """
    from app.attribution.classifier import classifier

    classifier.warm()


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.app_name} API",
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Duration-Ms"],
    )

    @app.middleware("http")
    async def timing_middleware(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-Duration-Ms"] = f"{elapsed_ms:.1f}"
        # #region agent log
        try:
            import json as _json, time as _time
            from pathlib import Path as _Path
            _origin = request.headers.get("origin") or request.headers.get("referer")
            if _origin or request.url.path.startswith("/api/"):
                _Path(r"C:\Users\ROHIT\OneDrive\Desktop\HANUMAN\debug-41078f.log").open("a", encoding="utf-8").write(_json.dumps({"sessionId":"41078f","hypothesisId":"D","location":"main.py:timing","message":"http_request","data":{"path":request.url.path,"method":request.method,"status":response.status_code,"origin":_origin,"ua":(request.headers.get("user-agent") or "")[:80]},"timestamp":int(_time.time()*1000)})+"\n")
        except Exception:
            pass
        # #endregion
        if elapsed_ms > 3000:
            logger.warning(
                "slow_request", path=request.url.path, duration_ms=round(elapsed_ms, 1)
            )
        return response

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "name": settings.app_name,
            "description": "Real-Time Crypto Fraud Attribution System",
            "docs": "/docs",
            "api": settings.api_prefix,
        }

    return app


app = create_app()
