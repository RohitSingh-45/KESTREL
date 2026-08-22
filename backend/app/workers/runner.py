"""Where a trace actually runs.

Two backends behind one call. `inline` runs the trace as a background asyncio
task in the API process, which needs no broker and is what local development and
the Windows demo use. `celery` hands the job to a worker over Redis, which is
what docker-compose runs. The API route is identical either way - it enqueues a
job id and returns immediately - so the SSE stream and the job row are the only
things the frontend ever needs to know about.
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.db.session import session_scope
from app.logging_config import get_logger
from app.tracing.engine import TraceEngine

logger = get_logger(__name__)

# Held so the event loop does not garbage-collect a running inline trace.
_inline_tasks: set[asyncio.Task] = set()


async def run_trace_job(job_id: str) -> None:
    """Execute one trace in its own session, independent of any request."""
    async with session_scope() as session:
        engine = TraceEngine(session)
        await engine.execute(job_id)


async def _run_inline(job_id: str) -> None:
    try:
        await run_trace_job(job_id)
    except Exception:  # noqa: BLE001 - already recorded on the job row
        logger.warning("inline_trace_job_failed", job_id=job_id)


def submit_trace(job_id: str) -> str:
    """Queue a trace and return the backend reference for it."""
    if settings.job_backend == "celery":
        from app.workers.tasks import run_trace_task

        async_result = run_trace_task.delay(job_id)
        logger.info("trace_enqueued", job_id=job_id, backend="celery")
        return str(async_result.id)

    task = asyncio.create_task(_run_inline(job_id), name=f"trace-{job_id}")
    _inline_tasks.add(task)
    task.add_done_callback(_inline_tasks.discard)
    logger.info("trace_enqueued", job_id=job_id, backend="inline")
    return job_id


async def wait_for_inline_tasks(timeout: float = 30.0) -> None:
    """Test and shutdown helper: let in-flight inline traces finish."""
    if not _inline_tasks:
        return
    await asyncio.wait(set(_inline_tasks), timeout=timeout)
