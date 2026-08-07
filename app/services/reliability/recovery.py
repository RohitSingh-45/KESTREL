"""Startup recovery helpers for stale leases and pipeline runs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.logging import get_logger
from app.orchestration.locks import GenerationLeaseManager, utc_now

logger = get_logger(__name__)


async def recover_stale_runtime_state(
    session: AsyncSession,
    *,
    lease_seconds: int,
    now: datetime | None = None,
) -> dict[str, int]:
    """Mark stale running pipeline rows failed and clear expired leases.

    Safe to call on every process start before workers begin.
    Does not steal active (non-expired) leases.
    """
    stamp = now or utc_now()
    runs = PipelineRunRepository(session)
    leases = GenerationLeaseManager(session, lease_seconds)
    stale_runs = await runs.recover_stale_runs(finished_at=stamp)
    cleared_leases = await leases.clear_expired(now=stamp)
    logger.info(
        "runtime_state_recovered",
        stale_pipeline_runs=stale_runs,
        expired_leases_cleared=cleared_leases,
        stage="recovery",
    )
    return {
        "stale_pipeline_runs": stale_runs,
        "expired_leases_cleared": cleared_leases,
    }
