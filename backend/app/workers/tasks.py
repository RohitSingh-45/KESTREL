"""Celery task wrappers.

Celery tasks are synchronous, so each one owns an event loop for the async work
it delegates to. Nothing but the loop plumbing lives here - the actual logic is
in `app.workers.runner`, which the inline backend calls directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from app.logging_config import configure_logging, get_logger
from app.workers.celery_app import celery_app
from app.workers.runner import run_trace_job

configure_logging()
logger = get_logger(__name__)


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        # Dispose everything bound to this loop, or the next task on this worker
        # inherits connections (DB, Redis) belonging to a closed loop and fails
        # with "Future attached to a different loop".
        from app.core.cache import cache
        from app.db.session import dispose_engine

        loop.run_until_complete(dispose_engine())
        loop.run_until_complete(cache.close())
        asyncio.set_event_loop(None)
        loop.close()


@celery_app.task(name="tracex.trace", bind=True, max_retries=1)
def run_trace_task(self, job_id: str) -> dict[str, Any]:  # noqa: ANN001
    logger.info("celery_trace_started", job_id=job_id, task_id=self.request.id)
    _run(run_trace_job(job_id))
    return {"job_id": job_id, "status": "completed"}
