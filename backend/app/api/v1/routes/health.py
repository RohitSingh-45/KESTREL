"""Liveness and readiness probes."""

from __future__ import annotations

import time

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import SessionDep
from app.api.schemas.common import HealthResponse, ReadinessComponent, ReadinessResponse
from app.config import settings
from app.core.cache import cache
from app.db.base import utcnow

router = APIRouter(tags=["health"])

VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=VERSION,
        environment=settings.app_env,
        data_mode=settings.data_mode,
        time=utcnow(),
    )


@router.get("/ready", response_model=ReadinessResponse)
async def ready(session: SessionDep) -> ReadinessResponse:
    components: list[ReadinessComponent] = []

    started = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
        components.append(
            ReadinessComponent(
                name="database",
                ok=True,
                detail=settings.database_url.split("://", 1)[0],
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        )
    except Exception as exc:
        # #region agent log
        try:
            import json as _json, time as _time
            from pathlib import Path as _Path
            _Path(r"C:\Users\ROHIT\OneDrive\Desktop\HANUMAN\debug-41078f.log").open("a", encoding="utf-8").write(_json.dumps({"sessionId":"41078f","hypothesisId":"B","location":"health.py:ready","message":"database_check_failed","data":{"error":str(exc)[:300],"err_type":type(exc).__name__},"timestamp":int(_time.time()*1000)})+"\n")
        except Exception:
            pass
        # #endregion
        components.append(ReadinessComponent(name="database", ok=False, detail=str(exc)[:200]))

    started = time.perf_counter()
    redis_ok = await cache.ping()
    components.append(
        ReadinessComponent(
            name="cache",
            # The in-memory fallback is a supported configuration, not a failure.
            ok=True,
            detail="redis" if redis_ok else "in-memory fallback",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    )

    components.append(
        ReadinessComponent(
            name="data_mode",
            ok=True,
            detail=settings.data_mode,
        )
    )

    # #region agent log
    try:
        import json as _json, time as _time
        from pathlib import Path as _Path
        _Path(r"C:\Users\ROHIT\OneDrive\Desktop\HANUMAN\debug-41078f.log").open("a", encoding="utf-8").write(_json.dumps({"sessionId":"41078f","hypothesisId":"B","location":"health.py:ready","message":"ready_complete","data":{"ready":all(c.ok for c in components),"components":[{"name":c.name,"ok":c.ok,"detail":c.detail} for c in components]},"timestamp":int(_time.time()*1000)})+"\n")
    except Exception:
        pass
    # #endregion
    return ReadinessResponse(
        ready=all(c.ok for c in components), components=components, time=utcnow()
    )
